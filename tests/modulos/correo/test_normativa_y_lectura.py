"""Legal data self-check (BOE extracts), MIME parsing, extraction and domains."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hacienda_gpt.modulos.correo import dominios, extraccion
from hacienda_gpt.modulos.correo.mime import MAX_BYTES_PARTE, html_a_texto, leer_mensaje
from hacienda_gpt.modulos.correo.normativa import Normativa, cargar, comprobar
from hacienda_gpt.tax.autonomos_params import textos_de_directorio
from tests.modulos.correo import corpus
from tests.modulos.correo.conftest import FIXTURES
from tests.modulos.correo.falso import b64, correo

BOE = FIXTURES / "boe"


def test_cada_cita_es_literal_y_cada_cifra_esta_en_su_cita() -> None:
    assert comprobar(cargar(), textos_de_directorio(BOE)) == []
    assert set(textos_de_directorio(BOE)) == set(cargar().fuentes_principales)


def test_cifras_y_fuentes() -> None:
    datos = cargar()
    assert datos.entero("plazo_rechazo_notificacion", "dias_naturales") == 10
    for fuente in datos.fuentes_principales.values():
        assert fuente.url == f"https://www.boe.es/buscar/act.php?id={fuente.boe_id}"
    assert all(r.fuente.verificado for r in datos.reglas.values())


def test_la_comprobacion_detecta_errores() -> None:
    crudo = cargar().model_dump(mode="json")
    crudo["reglas"]["plazo_rechazo_notificacion"]["valores"]["dias_naturales"] = "15"
    crudo["reglas"]["sede_estatal_gob_es"]["evidencia"][1]["cita"] += " (inventado)"
    problemas = comprobar(Normativa.model_validate(crudo), textos_de_directorio(BOE))
    assert any("dias_naturales" in p for p in problemas)
    assert any("sede_estatal_gob_es" in p and "literal" in p for p in problemas)


def test_mime_prefiere_texto_plano_y_decodifica_cabeceras() -> None:
    m = correo(
        "abc",
        asunto="=?UTF-8?B?" + b64("Notificación").replace("-", "+").replace("_", "/") + "=?=",
        texto="Hola\r\n  mundo",
        html="<p>otro</p>",
    )
    msg = leer_mensaje(m)
    assert msg.texto == "Hola\nmundo" and msg.remitente_dominio == "agenciatributaria.gob.es"
    assert msg.fecha == date(2026, 11, 15) and msg.thread_id == "tabc"


def test_html_visible_sin_ocultos_ni_scripts_y_con_enlaces() -> None:
    visible, enlaces, oculto = html_a_texto(
        "<html><head><title>T</title><style>p{}</style></head><body><script>alert(1)</script>"
        "<p>Total: <b>10,00 €</b></p><div hidden>escondido1</div><p style='display: none'>escondido2</p>"
        "<span aria-hidden='true'>escondido3</span><a href='https://x.example/a'>pulsa aquí</a></body></html>"
    )
    assert "Total: 10,00 €" in visible and "alert" not in visible and "escondido" not in visible
    assert {"escondido1", "escondido2", "escondido3"} <= set(oculto.split())
    assert enlaces == [("https://x.example/a", "pulsa aquí")]


def test_tamanos_limitados_y_adjuntos_sin_descargar() -> None:
    enorme = "a" * (MAX_BYTES_PARTE * 2)
    m = correo("big", texto=enorme, adjuntos=(("datos.xml", "application/xml"),))
    msg = leer_mensaje(m)
    assert len(msg.texto) <= MAX_BYTES_PARTE
    assert [a.nombre for a in msg.adjuntos] == ["datos.xml"]  # name and type only: never fetched nor parsed


def test_mensaje_sin_id_valido_se_rechaza() -> None:
    import pytest

    with pytest.raises(ValueError):
        leer_mensaje({"id": "../../etc", "payload": {}})


def test_extraccion_de_importes_y_fechas() -> None:
    assert extraccion.importe_decimal("1.234,56") == Decimal("1234.56")
    assert extraccion.importe_decimal("54.20") == Decimal("54.20")
    assert extraccion.importe_decimal("1,234.50") == Decimal("1234.50")
    total = extraccion.importe_total("Subtotal: 30,00 €\nIVA 21 %: 6,30 €\nTotal a pagar: 36,30 €")
    assert total is not None and total.valor == "36.30" and total.fragmento == "Total a pagar: 36,30 €"
    assert extraccion.importe_total("Uno 3,00 € y otro 4,00 €") is None  # ambiguous: not guessed
    plazo = extraccion.plazo("Tienes hasta el 3 de diciembre para pagar.", date(2026, 11, 20))
    assert plazo is not None and plazo.valor == "2026-12-03" and plazo.literal == "3 de diciembre"
    assert extraccion.plazo("El 3 de diciembre hay reunión.", date(2026, 11, 20)) is None
    assert extraccion.plazo("Antes del 01/01/2020 hay que…", date(2026, 11, 20)) is None  # past
    potencia = extraccion.potencia_kw("Potencia contratada P1: 3,45 kW · consumo 200 kWh")
    assert potencia is not None and potencia.valor == "3.45"
    assert extraccion.potencia_kw("Consumo 200 kWh") is None


def test_dominios() -> None:
    r = dominios.cargar()
    assert dominios.registrable("sede.agenciatributaria.gob.es") == "agenciatributaria.gob.es"
    assert dominios.es_sede_estatal("www.dgt.gob.es") and not dominios.es_sede_estatal("dgt-multas.com")
    for parecido, marca in (
        ("bbva-avisos.com", "bbva"),
        ("santnader.es", "santander"),
        ("bbv4.es", "bbva"),
        ("agenciatributaria-es.net", "aeat"),
        ("santander.es.acceso-seguro.com", "santander"),
        ("agenciatributaria.com", "aeat"),
    ):
        encontrada = dominios.parecido_a(parecido, r)
        assert encontrada is not None and encontrada.clave == marca, parecido
    for legitimo in ("bbva.es", "sede.agenciatributaria.gob.es", "bancosantander.es", "tienda-ejemplo.es", "bbva.net"):
        assert dominios.parecido_a(legitimo, r) is None, legitimo
    assert dominios.marca_nombrada("Agencia Tributaria", r).clave == "aeat"  # type: ignore[union-attr]
    assert dominios.host_valido("Bücher.example") == "xn--bcher-kva.example"


def test_operador_amplia_dominios_oficiales(monkeypatch) -> None:
    monkeypatch.setenv("CORREO_DOMINIOS_OFICIALES_EXTRA", "bbva=bbva-avisos.com")
    assert dominios.parecido_a("bbva-avisos.com", dominios.cargar()) is None


def test_corpus_se_lee() -> None:
    for m in corpus.todos():
        assert leer_mensaje(m).id == m["id"]
