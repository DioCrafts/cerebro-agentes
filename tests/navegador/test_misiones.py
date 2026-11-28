"""Missions through the task manager, the API and the chat tool: who may start one, what is kept, what comes out.

The model is the scripted double of ``test_agente.py``; the browser is a real headless Chromium against the mocks.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from asistente.capacidades.navegador import tareas
from asistente.capacidades.navegador.modulo import MODULO, encargar_mision
from asistente.capacidades.navegador.tareas import (
    TEXTO_CONSENTIMIENTO_MISION_VERSION,
    TEXTO_CONSENTIMIENTO_VERSION,
    GestorTareas,
    Servicios,
    TareaError,
)
from asistente.nucleo.decision.schemas import CaseState
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.modulos.acciones import AccionStore
from asistente.nucleo.modulos.contrato import ContextoModulo
from asistente.plataforma import settings
from asistente.plataforma.api.routers import navegador as navegador_router
from tests.navegador.conftest import login_clave, login_datadis
from tests.navegador.simulacion import Sitios
from tests.navegador.test_agente import CUPS, Modelo, descarga_datadis, hechas, pagina_de, ref

navegador = pytest.mark.navegador
OBJETIVO = "Descarga mi consumo horario del suministro de casa entre las dos fechas"
DATOS = dict(cups=CUPS, desde="2026-09-01", hasta="2026-09-03")
ACUSE = dict(acepto=True, texto_version=TEXTO_CONSENTIMIENTO_MISION_VERSION)


@dataclass
class Entorno:
    gestor: GestorTareas
    cases: SQLiteCaseStateStore
    acciones: AccionStore
    sitios: Sitios
    db: str

    def servicios(self, modelo: Any) -> Servicios:
        return Servicios(cases=self.cases, acciones=self.acciones, proveedor=modelo)

    def correr(self, sitio: str, modelo: Any, *, objetivo: str = OBJETIVO, datos: dict[str, Any] | None = None) -> Any:
        tarea = self.gestor.preparar_mision(
            case_id="c1", sitio=sitio, objetivo=objetivo, datos=DATOS if datos is None else datos
        )
        self.gestor.iniciar(tarea, modo="ventana", servicios=self.servicios(modelo), user_id=None)
        self.gestor.esperar(tarea, 90)
        return tarea

    def auditoria(self) -> list[dict[str, Any]]:
        return self.cases.list_audit_events("c1")

    def en_otro_proceso(self, tarea_id: str, estado: str) -> Any:
        """The task as ANOTHER process reads it from the database, once the owner's heartbeat wrote ``estado``."""
        limite = time.monotonic() + 10
        while True:
            otra = GestorTareas(db_path=self.db).get(tarea_id)
            if (otra is not None and otra.estado == estado) or time.monotonic() > limite:
                return otra
            time.sleep(0.2)


@pytest.fixture
def mis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entorno: Sitios, request: Any) -> Iterator[Entorno]:
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    monkeypatch.setattr(settings, "NAVEGADOR_AGENTE", True)
    monkeypatch.setattr(settings, "MODULOS_ACTIVOS_POR_DEFECTO", "navegador,luz")
    db = str(tmp_path / "casos.sqlite3")
    cases = SQLiteCaseStateStore(db)
    cases.save_case(CaseState(case_id="c1", user_id="u1", jurisdiction="ES", tax_period="2026", facts=[]))
    # The browser options are filled in by the tests that start one (they ask for the ``chromium`` fixture).
    gestor = GestorTareas(db_path=db)
    monkeypatch.setattr(tareas, "_gestor", [gestor])
    yield Entorno(gestor=gestor, cases=cases, acciones=AccionStore(db), sitios=entorno, db=db)


def _con_navegador(mis: Entorno, chromium: str | None, usuario: Any) -> None:
    mis.gestor.opciones_ejecutor = dict(
        headless=True,
        ejecutable=chromium,
        pausa_ms=0,
        espera_login_s=15,
        timeout_paso_ms=4_000,
        entrega_max_s=0,
        usuario_simulado=usuario,
    )


# --------------------------------------------------------------------------- #
# Preparing (no browser, no model)
# --------------------------------------------------------------------------- #


def test_preparar_una_mision_valida_y_no_arranca_nada(mis: Entorno) -> None:
    tarea = mis.gestor.preparar_mision(case_id="c1", sitio="datadis_consumo", objetivo=OBJETIVO, datos=DATOS)
    assert tarea.estado == "preparada" and tarea.clave == "mision_datadis_consumo"
    assert tarea.titulo.startswith("Misión en ")
    # The user reviews the objective in full; the data are masked wherever the task is shown.
    visibles = tarea.parametros_visibles()
    assert visibles["objetivo"] == OBJETIVO
    assert CUPS not in visibles["cups"] and visibles["cups"].endswith(CUPS[-4:])
    # Another process sees the same prepared mission (it lives in the cases' database until it starts).
    otra = GestorTareas(db_path=mis.db).get(tarea.id)
    assert otra is not None and otra.parametros == tarea.parametros and otra.estado == "preparada"


def test_misiones_que_no_se_pueden_preparar(mis: Entorno, monkeypatch: pytest.MonkeyPatch) -> None:
    def codigo(**kw: Any) -> int:
        argumentos = dict(case_id="c1", sitio="datadis_consumo", objetivo=OBJETIVO, datos=DATOS)
        argumentos.update(kw)
        with pytest.raises(TareaError) as error:
            mis.gestor.preparar_mision(**argumentos)
        return error.value.codigo

    assert codigo(sitio="no_existe") == 404
    assert codigo(objetivo="corto") == 422
    assert codigo(datos=dict(contraseña="hunter2")) == 422
    monkeypatch.setattr(settings, "NAVEGADOR_AGENTE", False)
    assert codigo() == 503  # the operator did not switch missions on
    monkeypatch.setattr(settings, "NAVEGADOR_AGENTE", True)
    monkeypatch.setattr(settings, "NAVEGADOR_ACTIVO", False)
    assert codigo() == 503


def test_un_sitio_de_demostracion_solo_existe_con_origenes_locales(
    mis: Entorno, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "sede_ejemplo" in tareas.sitios_disponibles() and "boe" in tareas.sitios_disponibles()
    monkeypatch.setattr(settings, "NAVEGADOR_ORIGENES_SUSTITUTOS", "")
    disponibles = tareas.sitios_disponibles()
    assert "sede_ejemplo" not in disponibles and "boe" in disponibles


def test_no_empieza_sin_modelo_ni_con_las_misiones_apagadas(mis: Entorno, monkeypatch: pytest.MonkeyPatch) -> None:
    tarea = mis.gestor.preparar_mision(case_id="c1", sitio="datadis_consumo", objetivo=OBJETIVO, datos=DATOS)
    with pytest.raises(TareaError) as sin_modelo:
        mis.gestor.iniciar(tarea, modo="ventana", servicios=mis.servicios(None), user_id=None)
    assert sin_modelo.value.codigo == 503 and "modelo" in str(sin_modelo.value)
    monkeypatch.setattr(settings, "NAVEGADOR_AGENTE", False)
    with pytest.raises(TareaError) as apagadas:
        mis.gestor.iniciar(tarea, modo="ventana", servicios=mis.servicios(Modelo(descarga_datadis)), user_id=None)
    assert apagadas.value.codigo == 503
    assert tarea.estado == "preparada"


def test_la_herramienta_del_chat_solo_prepara(mis: Entorno) -> None:
    ctx = ContextoModulo(perfil=dict(), hoy=date(2026, 11, 26), case_id="c1")
    herramienta = next(h for h in MODULO.herramientas if h.spec.name == "navegador_encargar_mision")
    assert herramienta.efecto == "prepara"
    resultado = encargar_mision(dict(sitio="datadis_consumo", objetivo=OBJETIVO, datos=DATOS), ctx)
    assert (
        not resultado.es_error
        and "No ha empezado" in resultado.contenido
        and "revisa el objetivo" in resultado.contenido
    )
    (tarea,) = mis.gestor.de_caso("c1")
    assert tarea.estado == "preparada" and tarea.hilo is None
    desconocido = encargar_mision(dict(sitio="banco", objetivo=OBJETIVO), ctx)
    assert desconocido.es_error and "datadis_consumo" in desconocido.contenido
    assert encargar_mision(dict(sitio="boe", objetivo=OBJETIVO), ContextoModulo(perfil=dict(), hoy=ctx.hoy)).es_error


# --------------------------------------------------------------------------- #
# Running (real Chromium, scripted model)
# --------------------------------------------------------------------------- #


@navegador
def test_la_descarga_de_una_mision_llega_al_modulo_de_luz(mis: Entorno, chromium: str | None) -> None:
    _con_navegador(mis, chromium, login_datadis)
    tarea = mis.correr("datadis_consumo", Modelo(descarga_datadis))

    assert tarea.estado == "importado", tarea.mensaje
    assert tarea.importacion is not None and not tarea.importacion_error
    assert [p["estado"] for p in tarea.pasos] == ["ok"] * 6
    # The audit trail has the names of what was given, never the objective's text nor a value.
    inicio = next(e for e in mis.auditoria() if e.get("event_type") == "navegador_tarea_iniciada")
    assert inicio["tipo"] == "mision" and inicio["parametros_recibidos"] == ["objetivo", "cups", "desde", "hasta"]
    assert inicio["texto_consentimiento"] == TEXTO_CONSENTIMIENTO_MISION_VERSION
    todo = str(mis.auditoria())
    assert OBJETIVO not in todo and "2026-09-01" not in todo
    fin = next(e for e in mis.auditoria() if e.get("event_type") == "navegador_tarea_terminada")
    assert fin["estado"] == "importado" and [p["accion"] for p in fin["pasos"]][-1] == "descargar"
    # Once finished the mission's parameters are gone from memory and from the database.
    assert tarea.parametros == dict()
    otra = mis.en_otro_proceso(tarea.id, "importado")
    assert otra is not None and otra.estado == "importado" and otra.parametros == dict()


@navegador
def test_una_mision_que_llega_a_firmar_deja_una_accion_pendiente(mis: Entorno, chromium: str | None) -> None:
    _con_navegador(mis, chromium, login_clave)
    datos = dict(nombre="María Ejemplo Ficticia", nif="12345678Z", correo="maria@example.org")

    def tramite(prompt: str, turno: int) -> dict[str, str]:
        anteriores = hechas(prompt)
        if ref(prompt, "button", "Firmar y enviar"):
            return dict(accion="detener", ref=ref(prompt, "button", "Firmar y enviar"))
        if ref(prompt, "combobox", "Motivo"):
            if "Elegir una opción" not in anteriores:
                return dict(accion="seleccionar", ref=ref(prompt, "combobox", "Motivo"), opcion="informacion")
            return dict(accion="pulsar", ref=ref(prompt, "button", "Siguiente"))
        for etiqueta, clave in (("Nombre y apellidos", "nombre"), ("NIF", "nif"), ("Correo electrónico", "correo")):
            if f"«{etiqueta}»" not in anteriores:
                return dict(accion="rellenar", ref=ref(prompt, "textbox", etiqueta), valor=datos[clave])
        return dict(accion="pulsar", ref=ref(prompt, "button", "Siguiente"))

    tarea = mis.correr(
        "sede_ejemplo", Modelo(tramite), objetivo="Prepara la solicitud genérica de información", datos=datos
    )

    assert tarea.estado == "detenido_antes_de_enviar", tarea.mensaje
    assert mis.sitios.registro.envios_sede == []
    assert tarea.captura is not None and dict(tarea.resumen_envio)["NIF"] == "12345678Z"
    (accion,) = mis.acciones.list("c1")
    assert accion.estado == "pendiente" and accion.modulo == "navegador" and accion.id == tarea.accion_id
    assert accion.argumentos["guion"] == "mision_sede_ejemplo"
    # The pending action lists labels, never the values.
    assert "12345678Z" not in str(accion.model_dump()) and "NIF" in accion.resumen


@navegador
def test_la_respuesta_de_una_mision_vive_solo_en_memoria(mis: Entorno, chromium: str | None) -> None:
    _con_navegador(mis, chromium, login_datadis)

    def consulta(prompt: str, turno: int) -> dict[str, str]:
        total = re.search(r"Consumo total del periodo:[^\d]*([\d.,]*\d)", pagina_de(prompt).replace("\n", " "))
        if total:
            return dict(accion="terminar", respuesta=f"Consumiste {total.group(1)} kWh en el periodo")
        decision = descarga_datadis(prompt, turno)
        return decision

    tarea = mis.correr("datadis_consumo", Modelo(consulta), objetivo="Dime cuánto consumí entre las dos fechas")

    assert tarea.estado == "respondido", tarea.mensaje
    assert tarea.respuesta is not None and tarea.respuesta.startswith("Consumiste ")
    # Another process sees that it ended, not what the page said.
    otra = mis.en_otro_proceso(tarea.id, "respondido")
    assert otra is not None and otra.estado == "respondido" and otra.respuesta is None
    assert "Consumiste" not in str(mis.auditoria())
    mis.gestor.cerrar(tarea)
    assert tarea.respuesta is None and tarea.captura is None


# --------------------------------------------------------------------------- #
# The API
# --------------------------------------------------------------------------- #


@pytest.fixture
def cliente(mis: Entorno) -> TestClient:
    app = FastAPI()
    app.include_router(
        navegador_router.build_router(
            get_case_store=lambda: mis.cases,
            get_accion_store=lambda: mis.acciones,
            get_gestor=lambda: mis.gestor,
            get_reparador=lambda: None,
            get_proveedor=lambda: Modelo(descarga_datadis),
        )
    )
    return TestClient(app)


def test_estado_anuncia_las_misiones_sus_sitios_y_su_texto(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    estado = cliente.get("/navegador/estado").json()
    assert estado["misiones"] is True
    assert estado["texto_version_mision"] == TEXTO_CONSENTIMIENTO_MISION_VERSION
    assert "proveedor del modelo" in estado["texto_consentimiento_mision"]
    por_clave = dict((s["clave"], s) for s in estado["sitios"])
    assert por_clave["boe"]["requiere_login"] is False and por_clave["boe"]["sitios"] == ["www.boe.es"]
    assert por_clave["datadis_consumo"]["requiere_login"] is True and por_clave["datadis_consumo"]["admite_descarga"]
    assert estado["confinamiento"] in ("sandbox-exec", "ninguno")
    monkeypatch.setattr(settings, "NAVEGADOR_AGENTE", False)
    apagado = cliente.get("/navegador/estado").json()
    assert apagado["misiones"] is False and apagado["sitios"] == []


def test_la_api_prepara_una_mision_y_exige_el_acuse_de_su_texto(cliente: TestClient, mis: Entorno) -> None:
    cuerpo = dict(case_id="c1", sitio="datadis_consumo", objetivo=OBJETIVO, datos=DATOS)
    preparada = cliente.post("/navegador/misiones", json=cuerpo)
    assert preparada.status_code == 201, preparada.text
    info = preparada.json()
    assert info["tipo"] == "mision" and info["estado"] == "preparada" and info["respuesta"] is None
    assert info["parametros"]["objetivo"] == OBJETIVO and CUPS not in info["parametros"]["cups"]
    # Starting needs the acknowledgement of the MISSIONS' text: the scripts' one is not enough.
    ruta = "/navegador/tareas/" + info["id"] + "/iniciar"
    sin_acuse = cliente.post(ruta, json=dict(modo="ventana"))
    otro_texto = cliente.post(ruta, json=dict(modo="ventana", acepto=True, texto_version=TEXTO_CONSENTIMIENTO_VERSION))
    assert sin_acuse.status_code == 422 and otro_texto.status_code == 422
    tarea = mis.gestor.get(info["id"])
    assert tarea is not None and tarea.estado == "preparada"
    directo = cliente.post("/navegador/misiones", json=dict(cuerpo, iniciar=True))
    assert directo.status_code == 422 and len(mis.gestor.de_caso("c1")) == 1
    assert cliente.post("/navegador/misiones", json=dict(cuerpo, sitio="no_existe")).status_code == 404
    assert cliente.post("/navegador/misiones", json=dict(cuerpo, objetivo="x")).status_code == 422
    assert cliente.post("/navegador/misiones", json=dict(cuerpo, case_id="otro")).status_code == 404


@navegador
def test_una_mision_completa_por_la_api(cliente: TestClient, mis: Entorno, chromium: str | None) -> None:
    _con_navegador(mis, chromium, login_datadis)
    cuerpo = dict(case_id="c1", sitio="datadis_consumo", objetivo=OBJETIVO, datos=DATOS, iniciar=True, **ACUSE)
    respuesta = cliente.post("/navegador/misiones", json=cuerpo)
    assert respuesta.status_code == 201, respuesta.text
    tarea = mis.gestor.get(respuesta.json()["id"])
    assert tarea is not None
    mis.gestor.esperar(tarea, 90)
    final = cliente.get("/navegador/tareas/" + tarea.id).json()
    assert final["estado"] == "importado" and final["tipo"] == "mision"
    assert all(CUPS not in e["texto"] for e in final["eventos"])
