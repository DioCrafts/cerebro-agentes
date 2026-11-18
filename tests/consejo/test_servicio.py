"""The council's service end to end with a stand-in engine: convene (estimate, no model), confirm (once), the job
(lease, one running per case, dossier, engine, gates, store, ONE notification), failures told honestly, cancel."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import threading
from typing import Any

import pytest

from asistente.nucleo.consejo.almacen import AlmacenConsejo
from asistente.nucleo.consejo.servicio import (
    KIND_CONSEJO,
    ConsejoError,
    ControlSesion,
    ServicioConsejo,
    _PresupuestoDeSesion,
)
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.proactive.store import NotificationStore
from asistente.plataforma import settings
from asistente.plataforma.ops.consumo import ConsumoStore, PresupuestoAgotadoError, Usage
from .fakes import HOY, FabricaFalsa, MemoriaFalsa, MotorFalso, fiscal, hipoteca

PREGUNTA = "Tengo 20.000 € ahorrados: ¿me conviene amortizar la hipoteca o invertir en un fondo indexado?"


def _servicio(db: str, fabrica: FabricaFalsa | None = None, **kw: Any) -> ServicioConsejo:
    return ServicioConsejo(db, fabrica=fabrica or FabricaFalsa(), en_segundo_plano=False, hoy=lambda: HOY, **kw)


def _caso(db: str, case_id: str = "c1") -> Any:
    case = SQLiteCaseStateStore(db).get_case(case_id)
    assert case is not None
    return case


def test_convening_only_creates_a_pending_session_with_its_estimate(db: str) -> None:
    fabrica = FabricaFalsa()
    servicio = _servicio(db, fabrica)
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    assert sesion.estado == "pendiente_confirmacion" and sesion.fase is None
    assert sesion.estimacion_usd > 0 and sesion.tope_usd >= sesion.estimacion_usd
    assert sesion.user_id == "u1" and sesion.case_id == "c1"
    # Nothing ran: no provider asked, no engine, no notification.
    assert fabrica.propositos == [] and fabrica.motor.expedientes == []
    assert NotificationStore(db).list("c1") == []
    # The same open question is not convened twice.
    otra = servicio.convocar(case=_caso(db), pregunta="  " + PREGUNTA + " ")
    assert otra.id == sesion.id
    registro = servicio.obtener(sesion.id, case_id="c1")
    assert registro.extra.minutos_estimados >= 1 and registro.extra.origen == "usuario"


def test_convening_refuses_when_off_too_short_or_above_the_maximum(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    servicio = _servicio(db)
    with pytest.raises(ConsejoError) as corta:
        servicio.convocar(case=_caso(db), pregunta="¿Y?")
    assert corta.value.codigo == "invalida"
    monkeypatch.setattr(settings, "CONSEJO_TOPE_MAX_USD", "0.01")
    with pytest.raises(ConsejoError) as cara:
        servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    assert cara.value.codigo == "tope" and "máximo permitido" in cara.value.mensaje
    monkeypatch.setattr(settings, "CONSEJO_ACTIVO", False)
    with pytest.raises(ConsejoError) as apagado:
        servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    assert apagado.value.codigo == "desactivado"


def test_the_job_runs_a_confirmed_session_end_to_end(db: str) -> None:
    motor = MotorFalso()
    fabrica = FabricaFalsa(
        motor=motor,
        especialistas={"hipoteca": hipoteca(), "fiscal": fiscal()},
        memoria=MemoriaFalsa([("dato", "Tiene un fondo de emergencia de seis meses de gastos.")]),
    )
    servicio = _servicio(db, fabrica)
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA, preferencia="Creo que amortizar es lo mejor.")
    assert servicio.confirmar(sesion.id, case_id="c1").confirmada_ahora

    resumen = servicio.ejecutar_pendientes()

    assert resumen["ejecutadas"] == 1 and resumen["terminadas"] == 1
    final = servicio.obtener(sesion.id, case_id="c1").sesion
    assert final.estado == "terminada" and final.dictamen is not None
    assert final.dictamen.sesion_id == sesion.id
    expediente = final.expediente
    assert expediente is not None and expediente is not None
    # The engine read the dossier the service built: neutral question, options, the specialists' exact figures.
    assert motor.expedientes[0].id == expediente.id
    assert [o.id for o in expediente.opciones] == ["o1", "o2"]
    assert {c.literal for c in expediente.cifras} >= {"6.400 €", "850 €/mes"}
    assert expediente.preferencia_usuario == "Creo que amortizar es lo mejor."
    assert any(h.origen == "memoria" for h in expediente.hechos)
    # The figures gate ran on the ruling against the dossier's figures: «6.400 €» is a specialist's.
    assert final.dictamen.puerta is not None and final.dictamen.puerta.ok
    assert final.propuestas and final.criticas and final.rankings
    assert final.coste.llamadas == 13  # the engine's own count (nothing metered by the fake providers)
    # The tope handed to the engine is what is left of the user's cap.
    assert motor.topes[0] == sesion.tope_usd
    # ONE notification, kind modulo:consejo, never urgent; the session knows it.
    avisos = NotificationStore(db).list("c1")
    assert len(avisos) == 1
    aviso = avisos[0]
    assert aviso.kind == KIND_CONSEJO and aviso.severity == "info" and aviso.title == "Dictamen del consejo listo"
    assert "Amortizar" in aviso.body and "la mayoría coincide" in aviso.body
    assert servicio.obtener(sesion.id, case_id="c1").notificacion_id == aviso.id
    # The progress of the phases was recorded (without saying who is who).
    progreso = servicio.obtener(sesion.id, case_id="c1").extra.progreso
    assert {p.fase for p in progreso if p.tipo == "fase"} >= {"instruccion", "propuestas", "sintesis"}
    assert all(p.miembro_id is None for p in progreso if p.fase != "instruccion")
    # A second pass has nothing to do (idempotent).
    assert servicio.ejecutar_pendientes()["ejecutadas"] == 0
    assert len(NotificationStore(db).list("c1")) == 1


def test_an_invented_figure_in_the_ruling_is_withdrawn_and_said(db: str) -> None:
    servicio = _servicio(db, FabricaFalsa(motor=MotorFalso(literal="9.999 €")))
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    servicio.confirmar(sesion.id, case_id="c1")
    servicio.ejecutar_pendientes()
    dictamen = servicio.obtener(sesion.id, case_id="c1").sesion.dictamen
    assert dictamen is not None and dictamen.puerta is not None and not dictamen.puerta.ok
    assert "9.999 €" not in dictamen.resumen
    assert any("He retirado del dictamen" in a for a in dictamen.avisos)


def test_a_failing_engine_fails_the_session_honestly_and_the_job_never_raises(db: str) -> None:
    servicio = _servicio(db, FabricaFalsa(motor=MotorFalso(error=RuntimeError("boom"))))
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    servicio.confirmar(sesion.id, case_id="c1")
    resumen = servicio.ejecutar_pendientes()
    assert resumen["fallidas"] == 1
    final = servicio.obtener(sesion.id, case_id="c1").sesion
    assert final.estado == "fallida" and "No hay dictamen" in (final.error or "")
    assert final.expediente is not None  # the dossier phase did finish: kept for the record
    aviso = NotificationStore(db).list("c1")[0]
    assert aviso.title == "El consejo no ha podido terminar" and aviso.severity == "info"
    assert "No hay dictamen" in aviso.body and "boom" not in aviso.body


def test_an_engine_that_ends_in_failure_is_reported_as_failed(db: str) -> None:
    servicio = _servicio(db, FabricaFalsa(motor=MotorFalso(estado="fallida")))
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    servicio.confirmar(sesion.id, case_id="c1")
    servicio.ejecutar_pendientes()
    final = servicio.obtener(sesion.id, case_id="c1").sesion
    assert final.estado == "fallida" and final.error == "El motor falló a propósito." and final.dictamen is None


def test_the_job_never_raises_even_when_the_factory_breaks(db: str) -> None:
    class Rota(FabricaFalsa):
        def entorno(self, *a: Any, **k: Any) -> Any:
            raise OSError("sin disco")

    servicio = _servicio(db, Rota())
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    servicio.confirmar(sesion.id, case_id="c1")
    assert servicio.ejecutar_pendientes()["fallidas"] == 1
    assert servicio.obtener(sesion.id, case_id="c1").sesion.estado == "fallida"


def test_reaching_the_cap_stops_the_session_and_says_so(db: str) -> None:
    control_visto: list[ControlSesion] = []

    def gastar() -> None:
        control = control_visto[-1]
        control.anotar(Usage(provider="p", model="m", input_tokens=1, output_tokens=1), control.tope_usd)
        control.comprobar()  # what the session's budget does before the next model call

    fabrica = FabricaFalsa(motor=MotorFalso(antes=gastar))
    original = fabrica.entorno

    def entorno(*a: Any, **k: Any) -> Any:
        control_visto.append(k["control"])
        return original(*a, **k)

    fabrica.entorno = entorno  # type: ignore[method-assign]
    servicio = _servicio(db, fabrica)
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    servicio.confirmar(sesion.id, case_id="c1")
    servicio.ejecutar_pendientes()
    final = servicio.obtener(sesion.id, case_id="c1").sesion
    assert final.estado == "fallida" and "tope de coste" in (final.error or "")
    assert final.coste.usd == sesion.tope_usd


def test_the_session_budget_checks_cap_calls_and_cancellation(db: str) -> None:
    control = ControlSesion(tope_usd=Decimal("0.10"), max_llamadas=2)
    presupuesto = _PresupuestoDeSesion(store=ConsumoStore(db), control=control)
    presupuesto.comprobar("u1", "c1", llamadas_en_pregunta=500)  # the app's per-question cap does not apply here
    control.anotar(Usage(provider="p", model="m", input_tokens=10, output_tokens=5), Decimal("0.04"))
    control.anotar(Usage(provider="p", model="m", input_tokens=10, output_tokens=5), None)
    with pytest.raises(PresupuestoAgotadoError):
        presupuesto.comprobar("u1", "c1")
    assert control.motivo == "llamadas" and control.coste().tokens_entrada == 20
    otro = ControlSesion(tope_usd=Decimal("0.10"))
    otro.cancelada.set()
    with pytest.raises(PresupuestoAgotadoError):
        otro.comprobar()
    assert otro.motivo == "cancelada"


def test_double_confirm_queues_and_starts_once(db: str) -> None:
    servicio = _servicio(db)
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    lanzados: list[str] = []
    servicio._lanzar = lambda case_id: lanzados.append(case_id)  # type: ignore[method-assign]
    resultados: list[bool] = []
    barrera = threading.Barrier(8)

    def confirmar() -> None:
        barrera.wait()
        resultados.append(servicio.confirmar(sesion.id, case_id="c1").confirmada_ahora)

    hilos = [threading.Thread(target=confirmar) for _ in range(8)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    assert sorted(resultados) == [False] * 7 + [True]
    assert lanzados == ["c1"]
    assert servicio.obtener(sesion.id, case_id="c1").sesion.estado == "en_cola"


def test_one_running_session_per_case_and_leases(db: str) -> None:
    servicio = _servicio(db)
    a = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    b = servicio.convocar(case=_caso(db), pregunta="¿Debería vender el piso de la playa o alquilarlo?")
    c = servicio.convocar(case=_caso(db, "c2"), pregunta=PREGUNTA)
    for sesion, case_id in ((a, "c1"), (b, "c1"), (c, "c2")):
        servicio.confirmar(sesion.id, case_id=case_id)
    ahora = datetime.now(UTC)
    almacen: AlmacenConsejo = servicio.almacen
    reclamadas = almacen.reclamar(momento=ahora, arriendo_s=600, max_intentos=2, limite=5)
    # One per case: a (older) for c1 and c for c2; b waits.
    assert sorted(r.sesion.id for r, _ in reclamadas) == sorted([a.id, c.id])
    assert almacen.reclamar(momento=ahora, arriendo_s=600, max_intentos=2, limite=5) == []
    # The lease of a expires (a crashed worker): it is taken over (second run), still alone in its case.
    tarde = ahora + timedelta(seconds=601)
    otra = almacen.reclamar(momento=tarde, arriendo_s=600, max_intentos=2, limite=5)
    ids = {r.sesion.id: r for r, _ in otra}
    assert a.id in ids and ids[a.id].intentos == 2 and b.id not in ids
    # A stale worker can no longer write its result (its lease is gone).
    viejo = dict((r.sesion.id, arriendo) for r, arriendo in reclamadas)[a.id]
    assert not almacen.renovar(a.id, viejo, arriendo_s=600)
    # Exhausted (no runs left): the job marks it failed and tells the user.
    muy_tarde = tarde + timedelta(seconds=601)
    servicio._reloj = lambda: muy_tarde  # type: ignore[method-assign]
    resumen = servicio.ejecutar_pendientes(limite=0)
    assert resumen["agotadas"] >= 1
    assert servicio.obtener(a.id, case_id="c1").sesion.estado == "fallida"


def test_cancelling_a_running_session_discards_its_result(db: str) -> None:
    servicio = _servicio(db)

    def cancelar_en_medio() -> None:
        servicio.cancelar(sesion.id, case_id="c1")

    servicio._fabrica = FabricaFalsa(motor=MotorFalso(antes=cancelar_en_medio))
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    servicio.confirmar(sesion.id, case_id="c1")
    servicio.ejecutar_pendientes()
    final = servicio.obtener(sesion.id, case_id="c1").sesion
    assert final.estado == "cancelada" and final.dictamen is None
    assert NotificationStore(db).list("c1") == []


def test_reject_cancel_and_ownership(db: str) -> None:
    servicio = _servicio(db)
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    with pytest.raises(ConsejoError) as ajena:
        servicio.confirmar(sesion.id, case_id="c2")
    assert ajena.value.codigo == "no_encontrada"
    with pytest.raises(ConsejoError) as otro_usuario:
        servicio.obtener(sesion.id, case_id="c1", user_id="u-otro")
    assert otro_usuario.value.codigo == "no_encontrada"
    assert servicio.rechazar(sesion.id, case_id="c1").estado == "rechazada"
    assert servicio.rechazar(sesion.id, case_id="c1").estado == "rechazada"  # idempotent
    with pytest.raises(ConsejoError) as tarde:
        servicio.confirmar(sesion.id, case_id="c1")
    assert tarde.value.codigo == "estado"
    with pytest.raises(ConsejoError):
        servicio.cancelar(sesion.id, case_id="c1")
    # A rejected question may be convened again (a new session).
    nueva = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    assert nueva.id != sesion.id


def test_unconfirmed_convocations_expire(db: str) -> None:
    servicio = _servicio(db)
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    luego = datetime.now(UTC) + timedelta(hours=73)
    servicio._reloj = lambda: luego  # type: ignore[method-assign]
    assert servicio.ejecutar_pendientes()["caducadas"] == 1
    final = servicio.obtener(sesion.id, case_id="c1").sesion
    assert final.estado == "cancelada" and "caducó" in (final.error or "")


def test_background_start_in_single_process_mode(db: str) -> None:
    servicio = ServicioConsejo(db, fabrica=FabricaFalsa(), en_segundo_plano=True, hoy=lambda: HOY)
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    servicio.confirmar(sesion.id, case_id="c1")
    servicio.esperar(timeout=30)
    assert servicio.obtener(sesion.id, case_id="c1").sesion.estado == "terminada"
    assert len(NotificationStore(db).list("c1")) == 1


def test_a_deleted_case_fails_the_session(db: str) -> None:
    servicio = _servicio(db)
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    servicio.confirmar(sesion.id, case_id="c1")
    # Erase the case: the trigger erases its sessions with it.
    assert SQLiteCaseStateStore(db).delete_case("c1")
    assert servicio.almacen.listar("c1") == []
    assert servicio.ejecutar_pendientes()["ejecutadas"] == 0


def test_a_partial_ruling_is_kept_said_and_notified_as_partial(db: str) -> None:
    motivo = "Dictamen parcial: se alcanzó el tope de coste antes de la revisión."
    servicio = _servicio(db, FabricaFalsa(motor=MotorFalso(avisos=[motivo])))
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    servicio.confirmar(sesion.id, case_id="c1")
    servicio.ejecutar_pendientes()
    final = servicio.obtener(sesion.id, case_id="c1").sesion
    assert final.estado == "terminada" and final.dictamen is not None and final.dictamen.avisos[0] == motivo
    aviso = NotificationStore(db).list("c1")[0]
    assert aviso.title == "Dictamen parcial del consejo" and "se alcanzó el tope de coste" in aviso.body
    from asistente.plataforma.api.routers.consejo import resumen

    assert resumen(servicio.obtener(sesion.id, case_id="c1")).parcial == (
        "se alcanzó el tope de coste antes de la revisión."
    )


def test_a_stop_before_round_one_had_three_proposals_is_a_failure(db: str) -> None:
    motor = MotorFalso(avisos=["Dictamen parcial: límite diario de uso."], miembros_ronda1=2)
    servicio = _servicio(db, FabricaFalsa(motor=motor))
    sesion = servicio.convocar(case=_caso(db), pregunta=PREGUNTA)
    servicio.confirmar(sesion.id, case_id="c1")
    assert servicio.ejecutar_pendientes()["fallidas"] == 1
    final = servicio.obtener(sesion.id, case_id="c1").sesion
    assert final.estado == "fallida" and final.dictamen is None
    assert "límite diario de uso." in (final.error or "") and "propuestas suficientes" in (final.error or "")
