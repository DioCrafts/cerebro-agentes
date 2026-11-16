"""The executor in a real (headless) Chromium against the local mock sites.

The mocks stand for Datadis, a Cl@ve-like gateway, a sede and a tracker (``simulacion.py``); each is its own
origin, so the allow-list is exercised on real cross-origin traffic, and the sites record what they received.
``usuario_simulado`` stands for the PERSON typing their own credentials in the window during the login phase.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import threading
from typing import Any

import pytest

from hacienda_gpt.navegador import ejecutor as ejecutor_mod
from hacienda_gpt.navegador import seguridad
from hacienda_gpt.navegador.ejecutor import Ejecutor, Evento, ResultadoEjecucion
from hacienda_gpt.navegador.guion import Guion, guion_activo, validar_parametros
from hacienda_gpt.navegador.seguridad import origenes_efectivos
from tests.navegador.conftest import login_clave, login_datadis, sesion_de_usuario
from tests.navegador.simulacion import USUARIO_DATADIS, Sitios, csv_datadis

PARAMS_DATADIS = {"cups": "ES0021000000000001AA", "desde": "2026-09-01", "hasta": "2026-09-03"}
PARAMS_SEDE = {
    "nombre": "María Ejemplo Ficticia",
    "nif": "12345678Z",
    "correo": "maria@example.org",
    "motivo": "informacion",
    "expone": "Solicito información sobre mi expediente.",
}


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


def correr(
    chromium: str | None,
    sitios: Sitios,
    *,
    clave: str = "datadis_consumo",
    guion: Guion | None = None,
    params: dict[str, Any] | None = None,
    modo: str = "ventana",
    usuario: Callable[[Any], None] | None = login_datadis,
    sesion: dict[str, Any] | None = None,
    **opciones: Any,
) -> tuple[ResultadoEjecucion, Registro]:
    guion = guion or guion_activo(clave).reglas
    valores = validar_parametros(guion, params if params is not None else PARAMS_DATADIS)
    registro = Registro()
    ejecutor = Ejecutor(
        guion,
        valores,
        origenes=origenes_efectivos(guion, sitios.sustitutos()),
        modo=modo,  # type: ignore[arg-type]
        sesion=sesion,
        observador=registro,
        headless=True,
        ejecutable=chromium,
        pausa_ms=0,
        espera_login_s=opciones.pop("espera_login_s", 15),
        timeout_paso_ms=opciones.pop("timeout_paso_ms", 4_000),
        entrega_max_s=0,
        usuario_simulado=usuario,
        **opciones,
    )
    return ejecutor.ejecutar(), registro


def _posts(sitios: Sitios, sitio: str) -> list[str]:
    return [ruta for s, metodo, ruta in sitios.registro.peticiones if s == sitio and metodo == "POST"]


def test_datadis_download_after_the_users_own_login(chromium: str | None, sitios: Sitios) -> None:
    resultado, registro = correr(chromium, sitios)

    assert resultado.estado == "descargado", resultado.mensaje
    assert resultado.descarga is not None
    assert resultado.descarga.decode() == csv_datadis("ES0021000000000001AA", *_fechas())
    assert resultado.nombre_descarga == "consumo_ES0021000000000001AA.csv"
    assert [p.estado for p in resultado.pasos] == ["ok"] * 8
    # The only POST Datadis ever received is the USER's login; the tracker got nothing at all.
    assert _posts(sitios, "datadis") == ["/login"]
    assert sitios.registro.tracker == []
    assert resultado.bloqueadas == [sitios.origenes["tracker"]]
    textos = registro.textos()
    assert "Inicia sesión tú" in textos and "solo consulta" in textos
    # Nothing typed ever appears in what the run reports.
    for valor in (USUARIO_DATADIS[1], "ES0021000000000001AA", "2026-09-01"):
        assert valor not in textos
    assert registro.capturas  # live screenshots of the agent's steps (none during the login)


def _fechas() -> tuple[Any, Any]:
    from datetime import date

    return date(2026, 9, 1), date(2026, 9, 3)


def test_a_session_handed_over_for_the_run_is_filtered_and_used(chromium: str | None, sitios: Sitios) -> None:
    sesion = sesion_de_usuario(chromium, sitios)
    antes = len(_posts(sitios, "datadis"))

    resultado, registro = correr(chromium, sitios, modo="sesion", sesion=sesion, usuario=None)

    assert resultado.estado == "descargado", resultado.mensaje
    assert len(_posts(sitios, "datadis")) == antes  # the agent itself never logs in nor posts
    assert "descartado 1 cookies" in registro.textos()  # the other site's cookie never reached the browser


def test_an_invalid_session_does_nothing(chromium: str | None, sitios: Sitios) -> None:
    sesion = {"cookies": [{"name": "sesion_datadis", "value": "falsa", "domain": "127.0.0.1", "path": "/"}]}
    resultado, _ = correr(chromium, sitios, modo="sesion", sesion=sesion, usuario=None, espera_login_s=2)
    assert resultado.estado == "login_no_completado"
    assert not resultado.llego_a_agente and resultado.pasos == []
    assert sitios.registro.descargas == 0


def test_without_the_users_login_the_agent_never_starts(chromium: str | None, sitios: Sitios) -> None:
    resultado, registro = correr(chromium, sitios, usuario=None, espera_login_s=1.5)
    assert resultado.estado == "login_no_completado" and resultado.pasos == []
    assert not any(ruta.startswith("/privado/consumos") for _s, _m, ruta in sitios.registro.peticiones)
    assert registro.capturas == []  # no screenshot of the login page


def test_stopping_while_the_user_logs_in(chromium: str | None, sitios: Sitios) -> None:
    cancelar = threading.Event()
    threading.Timer(1.0, cancelar.set).start()
    resultado, _ = correr(chromium, sitios, usuario=None, espera_login_s=30, cancelar=cancelar)
    assert resultado.estado == "cancelado" and resultado.pasos == []


def test_the_kill_switch_stops_a_run_between_steps(chromium: str | None, sitios: Sitios) -> None:
    resultado, _ = correr(chromium, sitios, activo=lambda: False)
    assert resultado.estado == "cancelado"
    assert sitios.registro.descargas == 0


def test_a_sede_procedure_stops_before_signing(chromium: str | None, sitios: Sitios) -> None:
    resultado, registro = correr(chromium, sitios, clave="sede_ejemplo", params=PARAMS_SEDE, usuario=login_clave)

    assert resultado.estado == "detenido_antes_de_enviar", resultado.mensaje
    assert resultado.resumen_envio == [
        ("Nombre y apellidos", "María Ejemplo Ficticia"),
        ("NIF", "12345678Z"),
        ("Correo electrónico", "maria@example.org"),
        ("Motivo", "Solicitud de información"),
        ("Expone", "Solicito información sobre mi expediente."),
    ]
    assert resultado.captura is not None and resultado.captura.startswith(b"\x89PNG")
    # «Firmar y enviar» was never pressed: the sede received no submission at all.
    assert sitios.registro.envios_sede == []
    assert _posts(sitios, "sede") == []
    assert _posts(sitios, "clave") == ["/login"]  # the user's own identification
    assert "ANTES de «Firmar y enviar»" in registro.textos()


def _guion_hostil(pasos: list[dict[str, Any]]) -> Guion:
    """A script that tries something it must not, built WITHOUT validation (as if the static checks were bypassed)."""
    from hacienda_gpt.navegador import guion as g

    clases = {
        "ir": g.PasoIr,
        "esperar": g.PasoEsperar,
        "rellenar": g.PasoRellenar,
        "pulsar": g.PasoPulsar,
        "descargar": g.PasoDescargar,
    }
    construidos = [clases[p["accion"]].model_construct(**p) for p in pasos]
    base = guion_activo("datadis_consumo").reglas
    return base.model_copy(update={"pasos": construidos})


def test_the_agent_refuses_to_type_into_a_password_field_even_if_a_script_asks(
    chromium: str | None, sitios: Sitios
) -> None:
    # The login form is visible from the start, so the hostile script reaches its "agent" phase at once.
    guion = _guion_hostil(
        [
            {"id": "ir", "accion": "ir", "descripcion": "ir", "origen": "datadis", "ruta": "/login"},
            {
                "id": "clave",
                "accion": "rellenar",
                "descripcion": "escribir",
                "selector": "#form-login input:nth-of-type(2)",
                "campo": "password",
                "etiqueta": "Contraseña",
                "valor": "robada",
            },
            {"id": "fin", "accion": "descargar", "descripcion": "x", "selector": "#entrar", "texto": "Entrar"},
        ]
    )
    guion = guion.model_copy(update={"login": guion.login.model_copy(update={"hecho_cuando": "#form-login"})})
    resultado, _ = correr(chromium, sitios, guion=guion, usuario=None)
    assert resultado.estado == "fallido"
    assert resultado.pasos[-1].id == "clave" and "contraseña" in resultado.pasos[-1].detalle
    assert _posts(sitios, "datadis") == []


def test_no_form_can_be_submitted_even_if_every_check_is_bypassed(
    chromium: str | None, sitios: Sitios, monkeypatch: pytest.MonkeyPatch
) -> None:
    guion = _guion_hostil(
        [
            {"id": "ir", "accion": "ir", "descripcion": "ir", "origen": "datadis", "ruta": "/privado/consumos"},
            {"id": "baja", "accion": "pulsar", "descripcion": "pulsar", "selector": "#baja", "texto": "Darse de baja"},
            {
                "id": "fin",
                "accion": "descargar",
                "descripcion": "x",
                "selector": "#descargar-csv",
                "texto": "Descargar CSV",
            },
        ]
    )
    # 1) The element check refuses the submit button.
    resultado, _ = correr(chromium, sitios, guion=guion)
    assert resultado.pasos[-1].id == "baja" and resultado.pasos[-1].estado == "rechazado"
    assert sitios.registro.posts_datadis == []
    # 2) Even with the element check disabled, the browser context aborts the POST the click causes.
    monkeypatch.setattr(ejecutor_mod, "problemas_pulsable", lambda *a, **k: [])
    resultado, registro = correr(chromium, sitios, guion=guion)
    assert sitios.registro.posts_datadis == []
    assert "el agente no envía datos (POST)" in registro.textos()


def test_navigation_outside_the_allow_list_is_blocked(
    chromium: str | None, sitios: Sitios, monkeypatch: pytest.MonkeyPatch
) -> None:
    guion = _guion_hostil(
        [
            {"id": "ir", "accion": "ir", "descripcion": "ir", "origen": "datadis", "ruta": "/privado/consumos"},
            {
                "id": "fuera",
                "accion": "pulsar",
                "descripcion": "salir",
                "selector": "#externo",
                "texto": "Más información",
            },
            {
                "id": "fin",
                "accion": "descargar",
                "descripcion": "x",
                "selector": "#descargar-csv",
                "texto": "Descargar CSV",
            },
        ]
    )
    resultado, _ = correr(chromium, sitios, guion=guion)
    assert resultado.pasos[-1].estado == "rechazado" and "fuera" in resultado.pasos[-1].detalle
    monkeypatch.setattr(ejecutor_mod, "problemas_pulsable", lambda *a, **k: [])
    resultado, _ = correr(chromium, sitios, guion=guion)
    assert sitios.registro.tracker == []  # the page never left the allowed origins
    assert sitios.origenes["tracker"] in resultado.bloqueadas


@pytest.mark.parametrize(
    ("variante", "motivo"), [("descarga_exe", "tipo de fichero"), ("descarga_grande", "permitidos")]
)
def test_unexpected_downloads_are_discarded(chromium: str | None, sitios: Sitios, variante: str, motivo: str) -> None:
    sitios.registro.variante = variante
    resultado, _ = correr(chromium, sitios)
    assert resultado.estado == "fallido" and resultado.descarga is None
    assert motivo in resultado.pasos[-1].detalle


class Reparador:
    def __init__(self, selector: str | None) -> None:
        self.selector = selector
        self.recibido: list[tuple[Any, list[dict[str, Any]]]] = []

    def proponer(self, paso: Any, esqueleto: list[dict[str, Any]]) -> str | None:
        self.recibido.append((paso, esqueleto))
        return self.selector


def test_a_changed_page_fails_safely_and_leaves_a_redacted_skeleton(chromium: str | None, sitios: Sitios) -> None:
    sitios.registro.variante = "selector_cambiado"
    resultado, _ = correr(chromium, sitios)
    assert resultado.estado == "fallido"
    assert resultado.pasos[-1].id == "suministro" and resultado.pasos[-1].estado == "no_encontrado"
    assert resultado.esqueleto_fallo
    volcado = json.dumps(resultado.esqueleto_fallo, ensure_ascii=False)
    assert "suministro" in volcado and "Suministro (CUPS)" in volcado
    assert "María" not in volcado and "ES0021000000000001AA" not in volcado
    assert sitios.registro.descargas == 0


def test_a_repaired_selector_is_used_only_if_the_deterministic_gate_accepts_it(
    chromium: str | None, sitios: Sitios
) -> None:
    sitios.registro.variante = "selector_cambiado"
    reparador = Reparador("#suministro")
    resultado, _ = correr(chromium, sitios, reparador=reparador)
    assert resultado.estado == "descargado", resultado.mensaje
    assert resultado.reparaciones == {"suministro": "#suministro"}
    assert [p.estado for p in resultado.pasos][2] == "reparado"
    paso, esqueleto = reparador.recibido[0]
    volcado = json.dumps(esqueleto, ensure_ascii=False)
    assert "María" not in volcado and "ES0021000000000001AA" not in volcado and "2026-09" not in volcado

    # Same id, but the label changed meaning: the gate refuses the proposal and nothing is downloaded.
    sitios.registro.variante = "etiqueta_cambiada"
    resultado, _ = correr(chromium, sitios, reparador=Reparador("#suministro"), timeout_paso_ms=2_000)
    assert resultado.estado == "fallido" and "no supera la comprobación" in resultado.pasos[-1].detalle
    # A proposal pointing at another control (a submit button) is refused too.
    resultado, _ = correr(chromium, sitios, reparador=Reparador("#baja"), timeout_paso_ms=2_000)
    assert resultado.estado == "fallido" and sitios.registro.posts_datadis == []


def test_the_agent_reads_nothing_of_the_login_page(
    chromium: str | None, sitios: Sitios, monkeypatch: pytest.MonkeyPatch
) -> None:
    leidos: list[str] = []
    original = seguridad.redactar_esqueleto

    def espia(items: Any, **kwargs: Any) -> Any:
        leidos.append(json.dumps(items))
        return original(items, **kwargs)

    monkeypatch.setattr(ejecutor_mod, "redactar_esqueleto", espia)
    reparador = Reparador(None)
    resultado, registro = correr(chromium, sitios, usuario=None, espera_login_s=1.5, reparador=reparador)
    assert resultado.estado == "login_no_completado"
    assert leidos == [] and reparador.recibido == [] and registro.capturas == []
