"""Missions (``navegador/agente.py``): a model chooses the steps over the ARIA snapshot; code decides if they run.

The «model» here is a scripted double: it reads the same prompt a real one would get and answers one action per
turn. Two kinds of doubles: one that works towards the objective, and one that OBEYS a hostile page (clicks what
would unsubscribe, leaves the site, types what it read). The second must get nothing done.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
import json
from pathlib import Path
import re
import sys
from typing import Any

import pytest

from asistente.capacidades.navegador import confinamiento
from asistente.capacidades.navegador.agente import (
    ESQUEMA_DECISION,
    MAX_RECHAZOS,
    EjecutorAgente,
    Mision,
    MisionError,
    problemas_pulsar,
    problemas_rellenar,
    resumen_de_formulario,
    validar_mision,
    valor_permitido,
)
from asistente.capacidades.navegador.ejecutor import Evento, ResultadoEjecucion
from asistente.capacidades.navegador.guion import guion_activo
from asistente.capacidades.navegador.instantanea import AVISO_RECORTE, preparar, redactar
from asistente.capacidades.navegador.seguridad import origenes_efectivos
from asistente.capacidades.navegador.sitio import Inicio, Sitio, SpecSitio, sitios
from asistente.capacidades.peticion import APERTURA as MARCA
from asistente.capacidades.peticion import contiene_no_confiable
from tests.navegador.conftest import login_clave, login_datadis
from tests.navegador.simulacion import Sitios, csv_datadis

CUPS = "ES0021000000000001AA"
VACIA = dict.fromkeys(ESQUEMA_DECISION["required"], "")
MISION_DATADIS = Mision(
    objetivo="Descarga mi consumo horario del suministro de casa entre las dos fechas",
    datos=dict(cups=CUPS, desde="2026-09-01", hasta="2026-09-03"),
)
navegador = pytest.mark.navegador


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class Modelo:
    """A scripted model: ``decidir(prompt, turno)`` returns the fields of one action."""

    def __init__(self, decidir: Callable[[str, int], dict[str, str]]) -> None:
        self.decidir = decidir
        self.prompts: list[str] = []
        self.sistemas: list[str] = []

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any], schema_name: str, **_: Any) -> Any:
        assert schema == ESQUEMA_DECISION and schema_name == "accion_navegador"
        self.prompts.append(user)
        self.sistemas.append(system)
        return {**VACIA, **self.decidir(user, len(self.prompts))}


def ref(prompt: str, rol: str, nombre: str) -> str:
    """The reference of ``rol "nombre"`` in the snapshot of a prompt ("" when it is not there)."""
    encontrado = re.search(rf'- {rol} "{re.escape(nombre)}"[^\n]*\[ref=([a-z0-9]+)\]', prompt)
    return encontrado.group(1) if encontrado else ""


def hechas(prompt: str) -> str:
    """The log of the model's own past actions, as the prompt gives it."""
    return prompt.split("TUS ACCIONES ANTERIORES:")[1].split("PÁGINA ACTUAL")[0]


def pagina_de(prompt: str) -> str:
    return prompt.split("PÁGINA ACTUAL")[1]


class Registro:
    def __init__(self) -> None:
        self.eventos: list[Evento] = []
        self.capturas: list[bytes] = []

    def evento(self, evento: Evento) -> None:
        self.eventos.append(evento)

    def captura(self, png: bytes) -> None:
        self.capturas.append(png)

    def textos(self) -> str:
        return "\n".join(e.texto for e in self.eventos)


def sitio_de(clave: str) -> Sitio:
    return Sitio.de_guion(guion_activo(clave).reglas)


def correr(
    chromium: str | None,
    simulados: Sitios,
    modelo: Modelo,
    *,
    sitio: Sitio | None = None,
    mision: Mision = MISION_DATADIS,
    usuario: Callable[[Any], None] | None = login_datadis,
    **opciones: Any,
) -> tuple[ResultadoEjecucion, Registro]:
    sitio = sitio or sitio_de("datadis_consumo")
    registro = Registro()
    ejecutor = EjecutorAgente(
        sitio,
        mision,
        proveedor=modelo,
        origenes=origenes_efectivos(sitio, simulados.sustitutos()),
        modo="ventana",
        observador=registro,
        headless=True,
        ejecutable=chromium,
        pausa_ms=0,
        espera_login_s=15,
        timeout_paso_ms=4_000,
        entrega_max_s=0,
        usuario_simulado=usuario,
        **opciones,
    )
    return ejecutor.ejecutar(), registro


def _posts(simulados: Sitios, sitio: str) -> list[str]:
    return [ruta for s, metodo, ruta in simulados.registro.peticiones if s == sitio and metodo == "POST"]


def descarga_datadis(prompt: str, turno: int) -> dict[str, str]:
    """A model that works towards the Datadis download, reading the page it is shown."""
    if ref(prompt, "link", "Descargar CSV"):
        return dict(accion="descargar", ref=ref(prompt, "link", "Descargar CSV"))
    if not ref(prompt, "button", "Consultar"):
        return dict(accion="pulsar", ref=ref(prompt, "link", "Consumos"), nota="voy a la consulta")
    anteriores = hechas(prompt)
    if "Elegir una opción" not in anteriores:
        return dict(accion="seleccionar", ref=ref(prompt, "combobox", "Suministro (CUPS)"), opcion=CUPS)
    if anteriores.count("Rellenar") == 0:
        return dict(accion="rellenar", ref=ref(prompt, "textbox", "Fecha inicial"), valor="2026-09-01")
    if anteriores.count("Rellenar") == 1:
        return dict(accion="rellenar", ref=ref(prompt, "textbox", "Fecha final"), valor="2026-09-03")
    return dict(accion="pulsar", ref=ref(prompt, "button", "Consultar"))


# --------------------------------------------------------------------------- #
# Deterministic parts (no browser)
# --------------------------------------------------------------------------- #


def test_una_mision_valida_y_las_que_se_rechazan() -> None:
    mision = validar_mision(dict(objetivo="  Mira   cuánto consumí en octubre ", datos=dict(cups=CUPS, n=3)))
    assert mision.objetivo == "Mira cuánto consumí en octubre" and mision.datos == dict(cups=CUPS, n="3")
    banco = "Entra en mi cuenta del banco"
    malas: list[dict[str, Any]] = [
        dict(objetivo="corto"),
        dict(objetivo="x" * 700),
        dict(objetivo=banco, datos=dict(contraseña="hunter2")),
        dict(objetivo=banco, datos=dict(pin="1234")),
        dict(objetivo=banco, datos=dict(a=["lista"])),
        dict(objetivo=banco, datos=dict(a="")),
        dict(objetivo=banco, otro=1),
        dict(objetivo=7),
    ]
    for mala in malas:
        with pytest.raises(MisionError):
            validar_mision(mala)


def test_solo_se_escribe_lo_que_dio_el_usuario() -> None:
    mision = Mision(objetivo="Busca la referencia AB-123 en mis expedientes", datos=dict(desde="2026-09-01"))
    assert valor_permitido("2026-09-01", mision)
    assert valor_permitido("01/09/2026", mision)  # the same date as a Spanish form wants it
    assert valor_permitido("ab-123", mision)  # literally in the objective
    assert not valor_permitido("12345678Z", mision)  # something the page showed
    assert not valor_permitido("", mision) and not valor_permitido("la", mision)
    assert not valor_permitido("2026-09-02", mision)


def test_puerta_de_pulsar() -> None:
    origenes = ["https://sede.example"]
    base = dict(texto="Siguiente", visible=True, tipo_efectivo="button", en_formulario=True)
    assert problemas_pulsar(base, origenes) == []
    assert problemas_pulsar(dict(base, texto="Firmar y enviar"), origenes)
    assert problemas_pulsar(dict(base, texto="Darse de baja"), origenes)
    envio = dict(base, texto="Buscar", tipo_efectivo="submit")
    assert problemas_pulsar(dict(envio, metodo_formulario="post"), origenes)
    assert problemas_pulsar(dict(envio, metodo_formulario="get"), origenes) == []  # a search form
    assert problemas_pulsar(dict(base, formaction="https://otro.example/x"), origenes)
    assert problemas_pulsar(dict(base, href="https://otro.example/x"), origenes)
    assert problemas_pulsar(dict(base, href="https://sede.example/ayuda"), origenes) == []
    assert problemas_pulsar(dict(base, visible=False), origenes)


def test_puerta_de_rellenar() -> None:
    mision = Mision(objetivo="Rellena la solicitud de información", datos=dict(nombre="María Ejemplo"))
    campo = dict(tag="input", tipo="text", tipo_efectivo="text", nombre="nombre", etiqueta="Nombre")
    assert problemas_rellenar(campo, "María Ejemplo", mision) == []
    assert problemas_rellenar(campo, "Otra Persona", mision)
    assert problemas_rellenar(dict(campo, tipo="password", tipo_efectivo="password"), "María Ejemplo", mision)
    assert problemas_rellenar(dict(campo, autocomplete="cc-number"), "María Ejemplo", mision)
    assert problemas_rellenar(dict(campo, etiqueta="PIN de la tarjeta"), "María Ejemplo", mision)
    assert problemas_rellenar(dict(campo, tag="select"), "María Ejemplo", mision)
    assert problemas_rellenar(dict(campo, tipo_efectivo="hidden"), "María Ejemplo", mision)


def test_la_instantanea_enmascara_recorta_y_solo_ofrece_lo_mostrado() -> None:
    cruda = "\n".join(
        [
            "- banner [ref=e1]:",
            "  - text: Hola, María Ejemplo Ficticia",
            '  - link "Consumos" [ref=e2] [cursor=pointer]:',
            f"    - /url: /privado/consumos?cups={CUPS}#resultado",
            f'  - cell "{CUPS}" [ref=e3]',
            '  - cell "Del 2026-10-01 al 2026-10-31: 1.234,56 kWh" [ref=e4]',
            '  - cell "ana@example.org 12345678Z 612 345 678" [ref=e5]',
        ]
    )
    entera = preparar(cruda, maximo=10_000)
    assert "María" not in entera.texto and CUPS not in entera.texto
    assert "ana@example.org" not in entera.texto and "12345678Z" not in entera.texto and "612 345" not in entera.texto
    assert "/url: /privado/consumos" in entera.texto and "cups=" not in entera.texto
    assert "2026-10-01" in entera.texto and "1.234,56 kWh" in entera.texto  # dates and amounts are kept
    assert sorted(entera.elementos) == ["e1", "e2", "e3", "e4", "e5"] and not entera.recortada
    assert entera.nombre_de("e2") == "el enlace «Consumos»"
    corta = preparar(cruda, maximo=90)
    assert corta.recortada and corta.texto.endswith(AVISO_RECORTE)
    assert sorted(corta.elementos) == ["e1", "e2"]  # what was cut off cannot be targeted
    assert redactar("[ref=e12] total 99,10 €") == "[ref=e12] total 99,10 €"


def test_el_resumen_de_un_formulario_no_lleva_credenciales() -> None:
    filas = [
        ["Nombre", "María", "nombre", ""],
        ["Contraseña", "hunter2", "password", "current-password"],
        ["Tarjeta", "4111", "tarjeta", "cc-number"],
        ["", "valor", "sin_etiqueta", ""],
        "basura",
    ]
    assert resumen_de_formulario(filas) == [("Nombre", "María"), ("sin_etiqueta", "valor")]
    assert resumen_de_formulario(None) == []


def test_sitios_de_ficheros_y_de_guiones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    declarados = sitios([guion_activo("datadis_consumo"), guion_activo("sede_ejemplo")])
    assert set(["boe", "datadis_consumo", "sede_ejemplo"]) <= set(declarados)
    assert declarados["boe"].reglas.login is None and declarados["boe"].reglas.pasos == ()
    assert declarados["datadis_consumo"].reglas.descarga is not None
    # The operator's directory wins over the bundled one; an invalid file is left out.
    propio = Sitio(
        titulo="BOE del operador",
        descripcion="d",
        origenes=dict(boe="https://www.boe.es"),
        inicio=Inicio(origen="boe"),
    )
    (tmp_path / "boe.json").write_text(
        SpecSitio(fuente="sitio_boe", version=7, reglas=propio).model_dump_json(), encoding="utf-8"
    )
    (tmp_path / "roto.json").write_text(json.dumps(dict(fuente="x")), encoding="utf-8")
    propios = sitios(directorio=tmp_path)
    assert propios["boe"].version == 7 and "roto" not in propios
    with pytest.raises(ValueError, match="https"):
        Sitio(titulo="t", descripcion="d", origenes=dict(a="http://inseguro.example"), inicio=Inicio(origen="a"))
    with pytest.raises(ValueError, match="origen no declarado"):
        Sitio(titulo="t", descripcion="d", origenes=dict(a="https://a.example"), inicio=Inicio(origen="b"))


def test_confinamiento_argumentos_y_perfil(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    argumentos = confinamiento.argumentos_de_red(
        ["https://sede.example", "http://127.0.0.1:8123", "https://sede.example"]
    )
    assert argumentos[0] == "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE sede.example"
    assert "--disable-background-networking" in argumentos
    perfil = confinamiento.perfil_macos(hogar="/Users/ana", navegador="/Users/ana/Library/Caches/pw", temporal="/t")
    lineas = perfil.splitlines()
    # The home is denied first, the browser's own files allowed after it (the last matching rule wins).
    assert lineas.index('(deny file-read* (subpath "/Users/ana"))') < lineas.index(
        '(allow file-read* (subpath "/Users/ana/Library/Caches/pw"))'
    )
    assert '(deny file-write* (subpath "/Users/ana"))' in lineas
    sin = confinamiento.preparar(tmp_path / "a", origenes=["https://x.example"], ejecutable=None, modo="ninguno")
    assert sin.confinado == "ninguno" and sin.ejecutable is None and Path(sin.descargas).is_dir()
    linux = confinamiento.preparar(tmp_path / "b", origenes=[], ejecutable="/usr/bin/chromium", plataforma="linux")
    assert linux.confinado == "ninguno" and linux.ejecutable == "/usr/bin/chromium"
    with pytest.raises(confinamiento.ConfinamientoError):
        confinamiento.preparar(
            tmp_path / "c", origenes=[], ejecutable="/usr/bin/chromium", plataforma="linux", modo="sandbox-exec"
        )
    assert confinamiento.modo_de("SANDBOX-EXEC ") == "sandbox-exec" and confinamiento.modo_de("raro") == "auto"


# --------------------------------------------------------------------------- #
# In a real Chromium, against the mock sites
# --------------------------------------------------------------------------- #


@navegador
def test_una_mision_descarga_el_consumo_eligiendo_ella_los_pasos(chromium: str | None, sitios: Sitios) -> None:
    modelo = Modelo(descarga_datadis)
    resultado, registro = correr(chromium, sitios, modelo)

    assert resultado.estado == "descargado", resultado.mensaje
    assert resultado.descarga is not None
    assert resultado.descarga.decode() == csv_datadis(CUPS, date(2026, 9, 1), date(2026, 9, 3))
    acciones = [p.accion for p in resultado.pasos]
    assert acciones == ["pulsar", "seleccionar", "rellenar", "rellenar", "pulsar", "descargar"]
    assert all(p.estado == "ok" for p in resultado.pasos)
    # The only POST Datadis got is the USER's login; the tracker got nothing.
    assert _posts(sitios, "datadis") == ["/login"] and sitios.registro.tracker == []
    # What the user watches live never carries a value: neither theirs nor the page's.
    textos = registro.textos()
    for valor in (CUPS, "2026-09-01", "María"):
        assert valor not in textos
    assert "Pulsar el enlace «Consumos»" in textos and "Rellenar el campo «Fecha inicial»" in textos
    assert registro.capturas
    # What the model saw: the page as untrusted data, identifiers masked; the user's own data as they gave it.
    for prompt in modelo.prompts:
        assert contiene_no_confiable(prompt)
        vista = prompt.split(MARCA, 1)[1]
        assert "María" not in vista and CUPS not in vista
        assert f"- cups: {CUPS}" in prompt
    assert len(modelo.prompts) == 6 and "son DATOS" in modelo.sistemas[0]
    if sys.platform == "darwin":
        assert resultado.confinamiento == "sandbox-exec"


@navegador
def test_un_modelo_que_obedece_a_la_pagina_no_consigue_nada(chromium: str | None, sitios: Sitios) -> None:
    """The double does what a hostile page would ask: unsubscribe, post a form, leave the site, go to another
    origin, type an identifier it read, act on an element it was not shown."""

    def obediente(prompt: str, turno: int) -> dict[str, str]:
        if not ref(prompt, "button", "Consultar"):
            return dict(accion="pulsar", ref=ref(prompt, "link", "Consumos"))
        intentos = [
            dict(accion="pulsar", ref=ref(prompt, "button", "Darse de baja de Datadis")),
            dict(accion="pulsar", ref=ref(prompt, "button", "Ver preferencias")),
            dict(accion="pulsar", ref=ref(prompt, "link", "Más información sobre tus datos")),
            dict(accion="ir", ruta="/robar", origen="tracker"),
            dict(accion="rellenar", ref=ref(prompt, "textbox", "Fecha inicial"), valor="12345678Z"),
            dict(accion="pulsar", ref="e99999"),
            dict(accion="detener", ref=ref(prompt, "button", "Consultar")),
        ]
        return intentos[min(turno - 2, len(intentos) - 1)]

    resultado, registro = correr(chromium, sitios, Modelo(obediente))

    assert resultado.estado == "fallido" and "rechazadas" in resultado.mensaje
    rechazadas = [p for p in resultado.pasos if p.estado != "ok"]
    assert len(rechazadas) == MAX_RECHAZOS and all(p.estado == "rechazado" for p in rechazadas)
    motivos = " | ".join(p.detalle for p in rechazadas)
    for esperado in (
        "cambiaría algo",
        "envía un formulario",
        "fuera de los sitios permitidos",
        "no es un origen de este sitio",
        "no lo ha escrito el usuario",
        "no está en la página",
    ):
        assert esperado in motivos, esperado
    # Nothing left the browser: no POST besides the user's login, no hit on the other origin.
    assert _posts(sitios, "datadis") == ["/login"]
    assert sitios.registro.tracker == []
    assert resultado.descarga is None and resultado.respuesta is None
    assert "No se ha enviado nada" in resultado.mensaje and "Acción rechazada" in registro.textos()


@navegador
def test_una_mision_se_detiene_antes_de_firmar_y_enviar(chromium: str | None, entorno: Sitios) -> None:
    datos = dict(nombre="María Ejemplo Ficticia", nif="12345678Z", correo="maria@example.org")
    mision = Mision(objetivo="Prepara la solicitud genérica: solicito información sobre mi expediente", datos=datos)

    def tramite(prompt: str, turno: int) -> dict[str, str]:
        anteriores = hechas(prompt)
        if ref(prompt, "button", "Firmar y enviar"):
            if "RECHAZADA" not in anteriores:  # first it tries to click it, as a careless model would
                return dict(accion="pulsar", ref=ref(prompt, "button", "Firmar y enviar"))
            return dict(accion="detener", ref=ref(prompt, "button", "Firmar y enviar"))
        if ref(prompt, "combobox", "Motivo"):
            if "Elegir una opción" not in anteriores:
                return dict(accion="seleccionar", ref=ref(prompt, "combobox", "Motivo"), opcion="informacion")
            if "«Expone»" not in anteriores:
                texto = "solicito información sobre mi expediente"
                return dict(accion="rellenar", ref=ref(prompt, "textbox", "Expone"), valor=texto)
            return dict(accion="pulsar", ref=ref(prompt, "button", "Siguiente"))
        for etiqueta, clave in (("Nombre y apellidos", "nombre"), ("NIF", "nif"), ("Correo electrónico", "correo")):
            if f"«{etiqueta}»" not in anteriores:
                return dict(accion="rellenar", ref=ref(prompt, "textbox", etiqueta), valor=datos[clave])
        return dict(accion="pulsar", ref=ref(prompt, "button", "Siguiente"))

    resultado, registro = correr(
        chromium, entorno, Modelo(tramite), sitio=sitio_de("sede_ejemplo"), mision=mision, usuario=login_clave
    )

    assert resultado.estado == "detenido_antes_de_enviar", resultado.mensaje
    assert entorno.registro.envios_sede == []  # the sede never received the form
    assert dict(resultado.resumen_envio)["Nombre y apellidos"] == "María Ejemplo Ficticia"
    assert resultado.captura is not None
    rechazo = next(p for p in resultado.pasos if p.estado == "rechazado")
    assert rechazo.accion == "pulsar" and "detener" in rechazo.detalle
    assert "12345678Z" not in registro.textos() and "maria@example.org" not in registro.textos()


@navegador
def test_una_mision_de_consulta_termina_con_su_respuesta(chromium: str | None, sitios: Sitios) -> None:
    def consulta(prompt: str, turno: int) -> dict[str, str]:
        total = re.search(r"Consumo total del periodo:[^\d]*([\d.,]*\d)", pagina_de(prompt).replace("\n", " "))
        if total:
            return dict(accion="terminar", respuesta=f"Consumo total del periodo: {total.group(1)} kWh")
        return descarga_datadis(prompt, turno)

    sitio = sitio_de("datadis_consumo").model_copy(update=dict(descarga=None))
    mision = Mision(objetivo="Dime cuánto consumí en total entre las dos fechas", datos=dict(MISION_DATADIS.datos))
    resultado, _registro = correr(chromium, sitios, Modelo(consulta), sitio=sitio, mision=mision)

    assert resultado.estado == "respondido", resultado.mensaje
    assert resultado.respuesta is not None and re.search(r"\d kWh", resultado.respuesta)
    assert resultado.descarga is None


@navegador
def test_limites_pasos_repeticion_y_modelo_caido(chromium: str | None, sitios: Sitios) -> None:
    quieto = Modelo(lambda prompt, turno: dict(accion="esperar"))
    resultado, _ = correr(chromium, sitios, quieto, max_pasos=30)
    assert resultado.estado == "fallido" and "repetía la misma acción" in resultado.mensaje
    assert len(quieto.prompts) <= 4

    def vueltas(prompt: str, turno: int) -> dict[str, str]:
        if turno % 2:
            return dict(accion="pulsar", ref=ref(prompt, "link", "Consumos"))
        return dict(accion="ir", ruta="/privado/inicio")

    da_vueltas = Modelo(vueltas)
    resultado, _ = correr(chromium, sitios, da_vueltas, max_pasos=4)
    assert resultado.estado == "fallido" and "4 pasos" in resultado.mensaje and len(da_vueltas.prompts) == 4

    def caido(prompt: str, turno: int) -> dict[str, str]:
        raise RuntimeError("503 del proveedor con detalles internos")

    resultado, _ = correr(chromium, sitios, Modelo(caido))
    assert resultado.estado == "fallido" and "El modelo no ha respondido" in resultado.mensaje
    assert "detalles internos" not in resultado.mensaje

    resultado, _ = correr(chromium, sitios, Modelo(lambda prompt, turno: dict(accion="borrar_todo")))
    assert resultado.estado == "fallido" and "acción que no existe" in resultado.mensaje


@navegador
@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec solo existe en macOS")
def test_el_navegador_confinado_no_lee_el_directorio_del_usuario(
    chromium: str | None, sitios: Sitios, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from playwright.sync_api import sync_playwright

    secreto = Path.home() / ("cerebro-prueba-confinamiento-" + tmp_path.name + ".txt")
    secreto.write_text("SECRETO-DEL-USUARIO", encoding="utf-8")
    try:
        leido: dict[str, str] = dict()
        for modo in ("ninguno", "sandbox-exec"):
            with sync_playwright() as pw:
                lanzamiento = confinamiento.preparar(
                    tmp_path / modo,
                    origenes=[sitios.origenes["datadis"]],
                    ejecutable=chromium,
                    ejecutable_por_defecto=pw.chromium.executable_path,
                    modo=modo,
                )
                assert lanzamiento.confinado == modo
                abierto = pw.chromium.launch(
                    headless=True,
                    executable_path=lanzamiento.ejecutable,
                    args=list(lanzamiento.argumentos),
                    downloads_path=lanzamiento.descargas,
                )
                pagina = abierto.new_page()
                # The allowed site still works under confinement, and the user's file is out of reach.
                pagina.goto(sitios.origenes["datadis"] + "/login")
                assert "Datadis" in pagina.title()
                try:
                    pagina.goto(secreto.as_uri())
                    leido[modo] = pagina.inner_text("body")
                except Exception as exc:
                    leido[modo] = "bloqueado: " + str(exc).splitlines()[0]
                # No other host name resolves, with or without the sandbox.
                with pytest.raises(Exception, match="ERR_NAME_NOT_RESOLVED"):
                    pagina.goto("https://otro-sitio.example/", timeout=5_000)
                abierto.close()
        assert "SECRETO-DEL-USUARIO" in leido["ninguno"]  # an unconfined browser reads the user's files
        assert "SECRETO-DEL-USUARIO" not in leido["sandbox-exec"] and "ERR_ACCESS_DENIED" in leido["sandbox-exec"]
    finally:
        secreto.unlink(missing_ok=True)
