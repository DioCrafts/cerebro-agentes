"""The whole product with agent F's REAL engine (scripted providers): convene → confirm → the job builds the dossier
with the fake specialists, the engine deliberates (proposals, anonymous critiques, revision, chair), the ruling is
stored with authorship revealed and ONE notification is written. The members never read the user's identity, and the
user's preference only from the revision round on."""

from __future__ import annotations

import json
from typing import Any

from asistente.nucleo.consejo.deliberacion import Deliberacion
from asistente.nucleo.consejo.servicio import KIND_CONSEJO, ServicioConsejo
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.proactive.store import NotificationStore
from asistente.plataforma.api.routers.consejo import vista
from tests.agent.fakes import ScriptedProvider
from tests.consejo.motor.fakes import ProveedorConsejo, proveedores_basicos
from .fakes import HOY, FabricaFalsa, caso, fiscal, hipoteca, respuesta_planteamiento

PREGUNTA = (
    "Me llamo Lucía, soy rumana y viuda. Tengo 20.000 € ahorrados: ¿me conviene amortizar la hipoteca o invertir "
    "en un fondo indexado?"
)
PREFERENCIA = "Me inclino por amortizar: no soporto deber dinero."


def _montaje(db: str) -> tuple[ServicioConsejo, dict[str, ProveedorConsejo], ScriptedProvider]:
    SQLiteCaseStateStore(db).save_case(
        caso("c1", "u1", nombre="Lucía", sexo="mujer", nacionalidad="rumana", estado_civil="viuda", edad=52)
    )
    consejo = proveedores_basicos()
    expediente = ScriptedProvider([], [respuesta_planteamiento()])

    def proveedor_para(proposito: str) -> Any:
        return consejo[proposito] if proposito in consejo else expediente

    fabrica = FabricaFalsa(
        motor=Deliberacion(proveedor_para=proveedor_para),
        especialistas={"hipoteca": hipoteca(), "fiscal": fiscal()},
        proveedor_para=proveedor_para,
    )
    return ServicioConsejo(db, fabrica=fabrica, en_segundo_plano=False, hoy=lambda: HOY), consejo, expediente


def test_end_to_end_with_the_real_engine(db: str) -> None:
    servicio, consejo, expediente = _montaje(db)
    case = SQLiteCaseStateStore(db).get_case("c1")
    assert case is not None
    sesion = servicio.convocar(case=case, pregunta=PREGUNTA, preferencia=PREFERENCIA)
    servicio.confirmar(sesion.id, case_id="c1")

    resumen = servicio.ejecutar_pendientes()

    assert resumen["terminadas"] == 1, servicio.obtener(sesion.id, case_id="c1").sesion.error
    registro = servicio.obtener(sesion.id, case_id="c1")
    final = registro.sesion
    assert final.estado == "terminada" and final.dictamen is not None
    dictamen = final.dictamen
    assert dictamen.recomendacion == "o1" and dictamen.consenso in ("mayoria", "unanime")
    assert len([m for m in dictamen.miembros if m.rol.proposito != "consejo:presidente"]) == 4 and dictamen.autoria
    assert final.case_id == "c1" and final.pregunta_usuario == PREGUNTA  # identity fields kept (``base``)
    assert {p.ronda for p in final.propuestas} == {1, 2} and final.criticas and final.rankings
    assert dictamen.puerta is not None  # the engine's figures gate ran on the ruling
    avisos = NotificationStore(db).list("c1")
    assert [a.kind for a in avisos] == [KIND_CONSEJO] and avisos[0].severity == "info"
    # The API view reveals authorship now.
    assert vista(registro).autoria_revelada

    # Nothing identifying reached any council member or the dossier model.
    peticiones = [r for p in consejo.values() for r in p.json_requests] + expediente.json_requests
    texto = json.dumps([r["user"] + r["system"] for r in peticiones], ensure_ascii=False)
    for prohibido in ("Lucía", "rumana", "viuda", "mujer"):
        assert prohibido not in texto, prohibido
    # The user's preference is sealed in round 1 and unsealed in the revision.
    primera = [r for p in consejo.values() for r in p.json_requests if r["schema_name"] == "propuesta_consejo"]
    revision = [
        r for p in consejo.values() for r in p.json_requests if r["schema_name"] == "propuesta_revisada_consejo"
    ]
    assert primera and revision
    assert all("no soporto deber dinero" not in r["user"] for r in primera)
    assert all("no soporto deber dinero" in r["user"] for r in revision)


def test_a_budget_stop_during_the_critiques_gives_a_partial_ruling(db: str) -> None:
    from asistente.plataforma.ops.consumo import PresupuestoAgotadoError

    servicio, consejo, _expediente = _montaje(db)
    # The budget runs out during the anonymous critiques: the engine stops there and rules with round 1.
    for proposito, proveedor in consejo.items():
        if proposito.startswith("consejo:miembro:"):
            proveedor.json_responses[1] = PresupuestoAgotadoError("Tope de la sesión.", limite="llamadas_por_pregunta")
    case = SQLiteCaseStateStore(db).get_case("c1")
    assert case is not None
    sesion = servicio.convocar(case=case, pregunta=PREGUNTA)
    servicio.confirmar(sesion.id, case_id="c1")
    servicio.ejecutar_pendientes()
    registro = servicio.obtener(sesion.id, case_id="c1")
    final = registro.sesion
    assert final.estado == "terminada" and final.dictamen is not None
    assert any(a.startswith("Dictamen parcial:") for a in final.dictamen.avisos)
    assert vista(registro).parcial
    assert NotificationStore(db).list("c1")[0].title == "Dictamen parcial del consejo"
