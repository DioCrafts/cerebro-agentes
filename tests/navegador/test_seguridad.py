"""Network policy, element checks, redaction and the selector-repair gate (no browser needed)."""

from __future__ import annotations

import json

import pytest

from hacienda_gpt.navegador.ejecutor import filtrar_sesion
from hacienda_gpt.navegador.guion import guion_activo
from hacienda_gpt.navegador.reparacion import ReparadorLLM, describir_paso, puerta
from hacienda_gpt.navegador.seguridad import (
    PoliticaError,
    PoliticaRed,
    comprobar_descarga,
    es_campo_credencial,
    origen_de,
    origenes_efectivos,
    problemas_campo,
    problemas_pulsable,
    redactar_esqueleto,
    redactar_texto,
    sustitutos_validos,
    texto_de_fichero,
)

DATADIS = "https://datadis.es"


def _politica() -> PoliticaRed:
    guion = guion_activo("datadis_consumo").reglas
    return PoliticaRed.para(guion, origenes_efectivos(guion, {}))


def test_only_the_scripts_origins_are_reachable() -> None:
    politica = _politica()
    assert politica.decidir("https://datadis.es/login", "GET")[0]
    assert politica.decidir("https://datadis.es:443/privado/consumos?x=1", "GET")[0]
    for url in (
        "https://tracker.example/pixel.gif",
        "http://datadis.es/login",  # same host, other scheme
        "https://datadis.es.evil.example/",
        "https://evil.example/?u=https://datadis.es",
        "https://datadis.es:8443/",
    ):
        assert politica.decidir(url, "GET") == (False, "origen fuera de la lista permitida"), url
    assert politica.decidir("data:image/png;base64,AAAA", "GET")[0]


def test_the_agent_phase_is_read_only_while_the_user_may_log_in() -> None:
    politica = _politica()
    assert politica.fase == "login"
    assert politica.decidir("https://datadis.es/login", "POST")[0]  # the USER submits the login form
    politica.fase = "agente"
    permitido, motivo = politica.decidir("https://datadis.es/privado/baja", "POST")
    assert not permitido and "no envía datos" in motivo
    for metodo in ("PUT", "DELETE", "PATCH"):
        assert not politica.decidir("https://datadis.es/x", metodo)[0]
    politica.posts_permitidos = (("https://datadis.es", "/api/consulta"),)
    assert politica.decidir("https://datadis.es/api/consulta/1", "POST")[0]
    assert not politica.decidir("https://datadis.es/api/otra", "POST")[0]
    politica.fase = "cerrada"
    assert not politica.decidir("https://datadis.es/login", "GET")[0]


def test_origin_substitution_only_points_to_this_machine() -> None:
    assert sustitutos_validos('{"https://datadis.es": "http://127.0.0.1:8791"}') == {DATADIS: "http://127.0.0.1:8791"}
    with pytest.raises(PoliticaError, match="local"):
        sustitutos_validos({"https://datadis.es": "https://evil.example"})
    with pytest.raises(PoliticaError):
        sustitutos_validos("[1, 2]")
    guion = guion_activo("datadis_consumo").reglas
    assert origenes_efectivos(guion, {DATADIS: "http://127.0.0.1:1"}) == {"datadis": "http://127.0.0.1:1"}
    assert origen_de("HTTPS://Datadis.es:443/a") == DATADIS and origen_de("nada") is None


def test_downloads_are_restricted_to_declared_types_and_sizes() -> None:
    descarga = guion_activo("datadis_consumo").reglas.descarga
    assert comprobar_descarga("consumo.csv", 1000, descarga) is None
    assert "tipo" in (comprobar_descarga("consumo.exe", 1000, descarga) or "")
    assert "tipo" in (comprobar_descarga("consumo.csv.html", 1000, descarga) or "")
    assert "más de" in (comprobar_descarga("consumo.csv", 5_000_001, descarga) or "")
    assert "vacío" in (comprobar_descarga("consumo.csv", 0, descarga) or "")
    assert comprobar_descarga("x.csv", 1, None) is not None
    assert texto_de_fichero("ñ;á".encode("latin-1")) == "ñ;á"
    with pytest.raises(ValueError):
        texto_de_fichero(b"MZ\x00\x00binario")


def _campo(**cambios: object) -> dict:
    return {
        "tag": "input",
        "tipo": "text",
        "tipo_efectivo": "text",
        "nombre": "fechaInicial",
        "id": "f",
        "autocomplete": "",
        "etiqueta": "Fecha inicial",
        "texto": "",
        **cambios,
    }


def test_the_agent_never_types_into_credential_fields() -> None:
    for info in (
        _campo(tipo="password", tipo_efectivo="password"),
        _campo(autocomplete="current-password"),
        _campo(autocomplete="one-time-code"),
        _campo(nombre="pin"),
        _campo(id="clave-pin"),
        _campo(autocomplete="cc-number"),
    ):
        assert es_campo_credencial(info), info
        assert any(
            "contraseña" in p
            for p in problemas_campo(info, campo="fechaInicial", etiqueta="Fecha inicial", seleccion=False)
        )
    assert problemas_campo(_campo(), campo="fechaInicial", etiqueta="fecha  INICIAL", seleccion=False) == []
    assert problemas_campo(_campo(nombre="otra"), campo="fechaInicial", etiqueta="Fecha inicial", seleccion=False)
    assert problemas_campo(
        _campo(etiqueta="Fecha de alta"), campo="fechaInicial", etiqueta="Fecha inicial", seleccion=False
    )
    assert problemas_campo(_campo(), campo="fechaInicial", etiqueta="Fecha inicial", seleccion=True)


def _boton(**cambios: object) -> dict:
    return {
        "tag": "button",
        "tipo": "button",
        "tipo_efectivo": "button",
        "texto": "Siguiente",
        "en_formulario": True,
        "formaction": "",
        "href": "",
        **cambios,
    }


def test_controls_that_submit_or_leave_are_never_clicked() -> None:
    origenes = [DATADIS]
    assert problemas_pulsable(_boton(), texto="Siguiente", origenes=origenes) == []
    assert problemas_pulsable(_boton(tipo_efectivo="submit"), texto="Siguiente", origenes=origenes)
    assert problemas_pulsable(_boton(texto="Enviar"), texto="Enviar", origenes=origenes)
    assert problemas_pulsable(_boton(formaction="/x"), texto="Siguiente", origenes=origenes)
    enlace = _boton(
        tag="a",
        tipo="",
        tipo_efectivo="",
        texto="Más información",
        en_formulario=False,
        href="https://tracker.example/fuera",
    )
    assert any("fuera" in p for p in problemas_pulsable(enlace, texto="Más información", origenes=origenes))
    assert (
        problemas_pulsable(dict(enlace, href="https://datadis.es/x"), texto="Más información", origenes=origenes) == []
    )
    # The stop step locates the submit button without clicking it: there it must BE a submit button.
    firmar = _boton(texto="Firmar y enviar", tipo_efectivo="submit")
    assert problemas_pulsable(firmar, texto="Firmar y enviar", origenes=origenes, es_parada=True) == []
    assert problemas_pulsable(_boton(), texto="Siguiente", origenes=origenes, es_parada=True)


def test_redaction_masks_personal_data_in_texts_and_skeletons() -> None:
    texto = "Hola, María Ejemplo · CUPS ES0021000000000001AA · NIF 12345678Z · m@example.org · 600 123 456"
    redactado = redactar_texto(texto)
    for dato in ("María", "ES0021000000000001AA", "12345678Z", "m@example.org", "600 123 456"):
        assert dato not in redactado
    esqueleto = redactar_esqueleto(
        [
            {"tag": "label", "for": "cups", "texto": "Suministro (CUPS)"},
            {"tag": "span", "id": "usuario-conectado", "texto": "Juan Nadie"},
            {"tag": "input", "id": "nif", "name": "nif", "value": "12345678Z"},
        ],
        enmascarar_ids=["#usuario-conectado"],
    )
    assert esqueleto[0]["texto"] == "Suministro (CUPS)"
    assert esqueleto[1]["texto"] == "•••"
    assert "value" not in esqueleto[2] and "12345678Z" not in json.dumps(esqueleto)


def test_a_handed_over_session_is_reduced_to_the_scripts_sites() -> None:
    sesion = {
        "cookies": [
            {"name": "s", "value": "1", "domain": ".datadis.es", "path": "/"},
            {"name": "t", "value": "2", "domain": "datadis.es", "path": "/"},
            {"name": "x", "value": "3", "domain": "banco.example", "path": "/"},
        ],
        "origins": [
            {"origin": "https://datadis.es", "localStorage": []},
            {"origin": "https://banco.example", "localStorage": [{"name": "k", "value": "v"}]},
        ],
    }
    filtrada, descartados = filtrar_sesion(sesion, {DATADIS})
    assert [c["name"] for c in filtrada["cookies"]] == ["s", "t"]
    assert [o["origin"] for o in filtrada["origins"]] == [DATADIS]
    assert descartados == 2
    with pytest.raises(ValueError):
        filtrar_sesion("cookies", {DATADIS})
    with pytest.raises(ValueError, match="grande"):
        filtrar_sesion({"cookies": [{"domain": "datadis.es", "value": "x" * 300_000}]}, {DATADIS})


class _Proveedor:
    def __init__(self, respuesta: dict) -> None:
        self.respuesta = respuesta
        self.llamadas: list[dict] = []

    def complete_json(self, **kwargs: object) -> dict:
        self.llamadas.append(kwargs)
        return self.respuesta


def test_the_repair_prompt_never_carries_values_and_the_gate_decides() -> None:
    guion = guion_activo("datadis_consumo").reglas
    paso = guion.pasos[2]  # seleccionar suministro, valor "{cups}"
    proveedor = _Proveedor({"selector": "#suministro", "motivo": "id cambiado"})
    reparador = ReparadorLLM(proveedor)
    esqueleto = redactar_esqueleto([{"tag": "select", "id": "suministro", "name": "cups"}])
    assert reparador.proponer(paso, esqueleto) == "#suministro"
    enviado = json.dumps(proveedor.llamadas[0], ensure_ascii=False)
    assert "{cups}" not in enviado and "valor" not in json.loads(proveedor.llamadas[0]["user"])["paso"]
    assert describir_paso(paso)["campo"] == "cups"

    select = {
        "tag": "select",
        "tipo": "",
        "tipo_efectivo": "select-one",
        "nombre": "cups",
        "id": "suministro",
        "autocomplete": "",
        "etiqueta": "Suministro (CUPS)",
        "texto": "",
    }
    assert puerta(paso, "#suministro", 1, select, [DATADIS]) == []
    assert puerta(paso, "#suministro", 2, select, [DATADIS])  # ambiguous
    assert puerta(paso, "#suministro", 1, dict(select, nombre="otro"), [DATADIS])  # other field name
    assert puerta(paso, "#suministro", 1, dict(select, etiqueta="Punto de suministro"), [DATADIS])  # other label
    assert puerta(paso, "input[type=password]", 1, select, [DATADIS])  # never a credential selector
    consultar = guion.pasos[5]
    boton = _boton(texto="Darse de baja de Datadis", tipo_efectivo="submit")
    assert puerta(consultar, "#baja", 1, boton, [DATADIS])  # a repair can never point at a submit control
    assert ReparadorLLM(_Proveedor({"selector": "", "motivo": "no"})).proponer(paso, []) is None
