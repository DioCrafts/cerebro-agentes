"""The dispatcher: a turn's encargos run as a DAG, in parallel, and come back as reports in plan order.

``Despachador.ejecutar(encargos, contexto)`` is what the brain calls with its plan (and again when it replans). What
it guarantees — each one is a test in ``tests/cerebro/colaboracion/test_despacho.py``:

* **Dependencies first.** An encargo starts only when every encargo it ``depende_de`` has a report; it gets those
  reports as ``previos`` (as data). Scheduling is Kahn's algorithm run online: whenever a slot frees, the ready
  encargo earliest in the plan starts. A plan that is not a DAG (or names unknown encargos) is a programming error:
  ``ValueError`` (``Intencion`` already refuses it).
* **Bounded parallelism.** At most ``max_paralelo`` specialists run at once (``CEREBRO_MAX_PARALELO``, default 4;
  1 = one after another and fully deterministic). Worker threads ≤ distinct encargos of the call.
* **Context crosses threads.** Each worker runs in a copy of the caller's ``contextvars`` (taken at submission):
  the usage metering (``contexto_consumo``: user, case and the per-question call count) and the prompt variant in
  use reach the specialists' model calls.
* **Per-encargo timeout and cancellation.** An encargo that runs longer than ``CEREBRO_TIMEOUT_ENCARGO_S`` (counted
  from its start, not from when it was queued) is given up: its report says so (``estado="error"``), its token is
  cancelled (its next model call fails, its waits wake up, its events and requests stop being recorded) and the
  others go on. Its thread is abandoned, not killed (Python cannot); every worker is a fresh one of a pool sized to
  the call, so an abandoned thread never delays another encargo.
* **Budget.** When the question's model budget runs out (``PresupuestoAgotadoError``, raised or swallowed by a
  specialist's loop and seen by its watched provider), nothing else is scheduled: every encargo that has not started
  gets ``estado="presupuesto"``. ``presupuesto_agotado`` holds the error, for the brain to tell the user.
* **Isolation.** A specialist that raises yields ``estado="error"``; the others are unaffected.
* **Dedupe.** Two encargos with the same specialist, the same question (normalised) and the same dependencies run
  once; the second gets a copy of the first's report under its own id. Across calls of the same turn (replanning),
  an identical encargo already answered ``ok`` is not run again.
* **Determinism.** Reports come back in plan order whatever the completion order. Every ``Cifra`` of the turn gets
  a unique id ``C1``, ``C2``… assigned after the call in plan order (and kept across calls: the same figure keeps its
  id), so ids do not depend on thread timing.
* **Requests between specialists** go through the turn's :class:`~.pizarra.Pizarra` (``.pizarra``); the figures a
  plan encargo received that way are merged into its report (with their own specialist: attribution is preserved),
  and the requests it made into its ``peticiones``. ``.peticiones`` lists every request of the turn.
* **Events.** ``EVENTO_ESPECIALISTA`` with ``estado="start"`` and ``estado="done"`` (plus ``resultado``,
  ``duracion_ms``, ``cifras``, ``profundidad``) for every specialist run, sub-encargos included; ``EVENTO_PETICION``
  for every request. Emission is serialised across threads; a failing callback never breaks the turn.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import contextvars
from dataclasses import dataclass, replace
import logging
import math
import re
import threading
import time
from typing import Any

from asistente.nucleo.cerebro.colaboracion.aislamiento import (
    AVISO_PRESUPUESTO,
    Cancelacion,
    EstadoTurno,
    atender_aislado,
    emisor_de_encargo,
    emisor_seguro,
    informe_fallido,
    vigilar,
)
from asistente.nucleo.cerebro.colaboracion.cifras import clave_de_cifra, plano, unicas
from asistente.nucleo.cerebro.colaboracion.pizarra import Pizarra, RegistroPeticion
from asistente.nucleo.cerebro.contrato import (
    EVENTO_ESPECIALISTA,
    MAX_CIFRAS_POR_INFORME,
    Cifra,
    ContextoEncargo,
    ContextoTurno,
    Emit,
    Encargo,
    Especialista,
    InformeEspecialista,
    PeticionDato,
)
from asistente.plataforma import settings
from asistente.plataforma.ops.consumo import PresupuestoAgotadoError

logger = logging.getLogger(__name__)

_CLAVE_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
_MAX_ID_CIFRA = 9999

ClaveEncargo = tuple[str, str, tuple[str, ...]]


def normalizar_pregunta(pregunta: str) -> str:
    """The question as dedupe compares it: case, accents, spacing and closing punctuation do not matter."""
    return " ".join(plano(pregunta).split()).strip(" ¿?¡!.").strip()


def orden_topologico(encargos: Sequence[Encargo]) -> list[str]:
    """Kahn's algorithm, ties broken by plan order (dependencies outside ``encargos`` count as satisfied).

    Raises ``ValueError`` on a cycle.
    """
    ids = {e.id for e in encargos}
    pendientes = {e.id: {d for d in e.depende_de if d in ids} for e in encargos}
    orden: list[str] = []
    while pendientes:
        libres = [e.id for e in encargos if e.id in pendientes and not pendientes[e.id]]
        if not libres:
            raise ValueError(f"el plan tiene un ciclo de dependencias: {sorted(pendientes)}")
        for eid in libres:
            del pendientes[eid]
            orden.append(eid)
        for deps in pendientes.values():
            deps.difference_update(libres)
    return orden


def ondas(encargos: Sequence[Encargo]) -> list[list[str]]:
    """The plan's topological levels (what can run together), for the trace and the UI."""
    nivel: dict[str, int] = {}
    por_id = {e.id: e for e in encargos}
    for eid in orden_topologico(encargos):
        deps = [d for d in por_id[eid].depende_de if d in por_id]
        nivel[eid] = 1 + max((nivel[d] for d in deps), default=-1)
    resultado: list[list[str]] = [[] for _ in range(max(nivel.values(), default=-1) + 1)]
    for e in encargos:
        resultado[nivel[e.id]].append(e.id)
    return resultado


@dataclass
class _Tarea:
    encargo: Encargo
    token: Cancelacion
    limite: float  # monotonic deadline (inf without timeout)


def _como(informe: InformeEspecialista, encargo_id: str, aviso: str) -> InformeEspecialista:
    """``informe`` answering another encargo (a deduped or reused one)."""
    avisos = informe.avisos if aviso in informe.avisos else [*informe.avisos, aviso]
    return informe.model_copy(update={"encargo_id": encargo_id, "avisos": avisos})


class Despachador:
    """Runs a turn's encargos (see the module docstring). One per turn; ``ejecutar`` may be called again to replan.

    ``especialistas``: the turn's ACTIVE specialists by key (the tax advisor and the user's switched-on modules).
    ``max_paralelo``, ``timeout_s``, ``max_profundidad`` and ``max_peticiones`` default to the settings
    (``CEREBRO_MAX_PARALELO``, ``CEREBRO_TIMEOUT_ENCARGO_S``, ``CEREBRO_MAX_PROFUNDIDAD_PETICIONES``) and the
    contract's limits, which they can lower but never raise. ``emit`` defaults to the turn's ``contexto.emit``.
    """

    def __init__(
        self,
        especialistas: Mapping[str, Especialista],
        *,
        max_paralelo: int | None = None,
        emit: Emit | None = None,
        timeout_s: float | None = None,
        max_profundidad: int | None = None,
        max_peticiones: int | None = None,
    ) -> None:
        malas = [k for k in especialistas if not _CLAVE_RE.fullmatch(k)]
        if malas:
            raise ValueError(f"claves de especialista no válidas: {malas}")
        sin_atender = [k for k, e in especialistas.items() if not callable(getattr(e, "atender", None))]
        if sin_atender:
            raise TypeError(f"no son especialistas (sin atender): {sin_atender}")
        self._especialistas: dict[str, Especialista] = dict(especialistas)
        paralelo = settings.CEREBRO_MAX_PARALELO if max_paralelo is None else max_paralelo
        self.max_paralelo = max(1, int(paralelo))
        segundos = settings.CEREBRO_TIMEOUT_ENCARGO_S if timeout_s is None else timeout_s
        self.timeout_s: float | None = float(segundos) if segundos and segundos > 0 else None
        self._max_profundidad = max_profundidad
        self._max_peticiones = max_peticiones
        self._emit_externo = emit
        self._emitir: Emit | None = None
        self.estado = EstadoTurno()
        self.pizarra: Pizarra | None = None
        self._lock = threading.Lock()
        self._llamadas = 0
        self._informes: dict[str, InformeEspecialista] = {}  # every report of the turn, by encargo id
        self._cache: dict[ClaveEncargo, InformeEspecialista] = {}  # ok reports by (specialist, question, deps)
        self._ids_cifras: dict[tuple[str, ...], str] = {}

    # ------------------------------------------------------------------ public

    @property
    def especialistas(self) -> dict[str, Especialista]:
        return dict(self._especialistas)

    @property
    def peticiones(self) -> list[PeticionDato]:
        """Every request between specialists of the turn (audit trail), in id order."""
        return self.pizarra.peticiones if self.pizarra is not None else []

    @property
    def registros(self) -> list[RegistroPeticion]:
        return self.pizarra.registros if self.pizarra is not None else []

    @property
    def subinformes(self) -> list[InformeEspecialista]:
        """Reports of the sub-encargos run to answer requests (their figures are already in the asking reports)."""
        return self.pizarra.subinformes if self.pizarra is not None else []

    @property
    def informes(self) -> list[InformeEspecialista]:
        """Every plan report of the turn so far (all calls), in the order they were planned."""
        return list(self._informes.values())

    @property
    def presupuesto_agotado(self) -> PresupuestoAgotadoError | None:
        return self.estado.presupuesto

    def ejecutar(self, encargos: Sequence[Encargo], contexto: ContextoTurno) -> list[InformeEspecialista]:
        """Run ``encargos`` and return one report per encargo, in plan order."""
        plan = list(encargos)
        if not plan:
            return []
        with self._lock:  # a turn is sequential: one call at a time
            return self._ejecutar(plan, contexto)

    # ------------------------------------------------------------------ the run

    def _validar(self, plan: Sequence[Encargo]) -> None:
        ids = [e.id for e in plan]
        if len(set(ids)) != len(ids):
            raise ValueError(f"ids de encargo repetidos: {ids}")
        conocidos = set(ids) | set(self._informes)
        for e in plan:
            if e.id in e.depende_de:
                raise ValueError(f"{e.id} depende de sí mismo")
            desconocidos = set(e.depende_de) - conocidos
            if desconocidos:
                raise ValueError(f"{e.id} depende de encargos inexistentes: {sorted(desconocidos)}")
        orden_topologico(plan)

    def _ejecutar(self, plan: list[Encargo], contexto: ContextoTurno) -> list[InformeEspecialista]:
        self._validar(plan)
        self._llamadas += 1
        if self._emitir is None:
            self._emitir = emisor_seguro(self._emit_externo or contexto.emit)
        emitir = self._emitir
        if self.pizarra is None:
            self.pizarra = Pizarra(
                self._especialistas,
                contexto,
                ejecutar=self._atender,
                estado=self.estado,
                max_profundidad=self._max_profundidad,
                max_peticiones=self._max_peticiones,
            )
        pizarra = self.pizarra
        pizarra.reservar_ids(e.id for e in plan)
        por_id = {e.id: e for e in plan}

        # Dedupe (in topological order, so a dependency's canonical id is known before its dependents').
        canon: dict[str, str] = {}
        primeras: dict[ClaveEncargo, str] = {}
        claves: dict[str, ClaveEncargo] = {}
        resultados: dict[str, InformeEspecialista] = {}
        for eid in orden_topologico(plan):
            e = por_id[eid]
            deps = tuple(sorted({canon.get(d, d) for d in e.depende_de}))
            clave: ClaveEncargo = (e.especialista, normalizar_pregunta(e.pregunta), deps)
            claves[eid] = clave
            if clave in primeras:
                canon[eid] = primeras[clave]
                continue
            primeras[clave] = canon[eid] = eid
            previo = self._cache.get(clave)
            if previo is not None:
                resultados[eid] = _como(previo, eid, f"Mismo encargo que {previo.encargo_id}: se reutiliza su informe.")
            elif e.especialista not in self._especialistas:
                resultados[eid] = informe_fallido(
                    e, e.especialista, "denegado", "No es un especialista activo en esta consulta."
                )
            else:
                continue
            emitir(
                EVENTO_ESPECIALISTA,
                clave=e.especialista,
                nombre=resultados[eid].nombre,
                encargo_id=eid,
                estado="done",
                resultado=resultados[eid].estado,
                duracion_ms=0,
                cifras=len(resultados[eid].cifras),
                profundidad=0,
            )

        por_ejecutar = [e.id for e in plan if canon[e.id] == e.id and e.id not in resultados]
        raices = {eid: f"{self._llamadas}:{eid}" for eid in por_ejecutar}
        self._planificar(por_ejecutar, por_id, canon, raices, resultados, contexto, emitir, pizarra)

        for eid, raiz in raices.items():
            resultados[eid] = self._con_peticiones(resultados[eid], pizarra.respuestas_de(raiz))
        for e in plan:
            origen = canon[e.id]
            if origen != e.id:  # a deduped encargo: a copy of its original's (merged) report
                resultados[e.id] = _como(
                    resultados[origen], e.id, f"Mismo encargo que {origen}: se reutiliza su informe."
                )

        informes = [self._numerar(resultados[e.id]) for e in plan]
        pizarra.renumerar(self._renumerar)
        for informe in informes:
            self._informes[informe.encargo_id] = informe
            if informe.estado == "ok":
                self._cache.setdefault(claves[informe.encargo_id], informe)
        return informes

    def _planificar(
        self,
        por_ejecutar: list[str],
        por_id: Mapping[str, Encargo],
        canon: Mapping[str, str],
        raices: Mapping[str, str],
        resultados: dict[str, InformeEspecialista],
        contexto: ContextoTurno,
        emitir: Emit,
        pizarra: Pizarra,
    ) -> None:
        if not por_ejecutar:
            return
        pendientes = list(por_ejecutar)  # plan order
        corriendo: dict[Future[InformeEspecialista], _Tarea] = {}

        def listo(eid: str) -> bool:
            return all(canon.get(d, d) in resultados or d in self._informes for d in por_id[eid].depende_de)

        def terminar(eid: str, informe: InformeEspecialista) -> None:
            resultados[eid] = informe
            pizarra.anotar_informe(informe)

        pool = ThreadPoolExecutor(max_workers=len(por_ejecutar), thread_name_prefix="especialista")
        try:
            while pendientes or corriendo:
                if self.estado.agotado and pendientes:
                    for eid in pendientes:
                        informe = informe_fallido(
                            por_id[eid], self._nombre(por_id[eid]), "presupuesto", AVISO_PRESUPUESTO
                        )
                        terminar(eid, informe)
                        self._emitir_fin(emitir, informe, motivo="presupuesto")
                    pendientes.clear()
                for eid in [p for p in pendientes if listo(p)]:
                    if len(corriendo) >= self.max_paralelo:
                        break
                    pendientes.remove(eid)
                    futuro, tarea = self._lanzar(
                        pool, por_id[eid], por_id, canon, resultados, contexto, emitir, pizarra, raices[eid]
                    )
                    corriendo[futuro] = tarea
                if not corriendo:
                    for eid in pendientes:  # unreachable for a validated plan; never hang
                        terminar(
                            eid,
                            informe_fallido(por_id[eid], self._nombre(por_id[eid]), "error", "No se pudo programar."),
                        )
                    break
                plazo = None
                if self.timeout_s is not None:
                    plazo = max(0.0, min(t.limite for t in corriendo.values()) - time.monotonic())
                hechos, _ = wait(list(corriendo), timeout=plazo, return_when=FIRST_COMPLETED)
                for futuro in hechos:
                    tarea = corriendo.pop(futuro)
                    terminar(tarea.encargo.id, self._resultado(futuro, tarea))
                if self.timeout_s is None:
                    continue
                ahora = time.monotonic()
                for futuro, tarea in list(corriendo.items()):
                    if tarea.limite > ahora:
                        continue
                    del corriendo[futuro]
                    if futuro.done():
                        terminar(tarea.encargo.id, self._resultado(futuro, tarea))
                        continue
                    tarea.token.cancelar("tiempo agotado")
                    informe = informe_fallido(
                        tarea.encargo,
                        self._nombre(tarea.encargo),
                        "error",
                        f"Tiempo agotado: no respondió en {self.timeout_s:g} s.",
                    ).model_copy(update={"duracion_ms": int(self.timeout_s * 1000)})
                    terminar(tarea.encargo.id, informe)
                    self._emitir_fin(emitir, informe, motivo="tiempo")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _lanzar(
        self,
        pool: ThreadPoolExecutor,
        encargo: Encargo,
        por_id: Mapping[str, Encargo],
        canon: Mapping[str, str],
        resultados: Mapping[str, InformeEspecialista],
        contexto: ContextoTurno,
        emitir: Emit,
        pizarra: Pizarra,
        raiz: str,
    ) -> tuple[Future[InformeEspecialista], _Tarea]:
        token = Cancelacion()
        turno = replace(
            contexto,
            emit=emisor_de_encargo(emitir, token),
            proveedor_para=vigilar(contexto.proveedor_para, token=token, estado=self.estado),
        )
        previos = tuple(self._previo(d, canon, resultados) for d in encargo.depende_de)
        linea = pizarra.linea(encargo.especialista, turno=turno, token=token, encargo_id=encargo.id, raiz=raiz)
        ctx = ContextoEncargo(turno=turno, encargo=encargo, pizarra=linea, previos=previos, profundidad=0)
        limite = time.monotonic() + self.timeout_s if self.timeout_s is not None else math.inf
        # The copy is taken HERE, in the caller's thread: usage metering and the prompt variant cross into the worker.
        futuro = pool.submit(contextvars.copy_context().run, self._tarea, encargo, ctx)
        return futuro, _Tarea(encargo=encargo, token=token, limite=limite)

    def _previo(
        self, dep: str, canon: Mapping[str, str], resultados: Mapping[str, InformeEspecialista]
    ) -> InformeEspecialista:
        if dep in resultados:
            return resultados[dep]
        origen = canon.get(dep)
        if origen is not None and origen in resultados:
            return _como(resultados[origen], dep, f"Mismo encargo que {origen}: se reutiliza su informe.")
        return self._informes[dep]

    def _tarea(self, encargo: Encargo, ctx: ContextoEncargo) -> InformeEspecialista:
        if self.estado.agotado:
            informe = informe_fallido(encargo, self._nombre(encargo), "presupuesto", AVISO_PRESUPUESTO)
            self._emitir_fin(ctx.turno.emit, informe, motivo="presupuesto")
            return informe
        return self._atender(self._especialistas[encargo.especialista], ctx)

    def _atender(self, especialista: Especialista, ctx: ContextoEncargo) -> InformeEspecialista:
        """Run one specialist on one encargo (plan or sub-encargo), isolated, with its start/done events."""
        encargo = ctx.encargo
        ctx.turno.emit(
            EVENTO_ESPECIALISTA,
            clave=encargo.especialista,
            nombre=self._nombre(encargo),
            encargo_id=encargo.id,
            estado="start",
            profundidad=ctx.profundidad,
        )
        informe = atender_aislado(especialista, ctx, estado=self.estado)
        ctx.turno.emit(
            EVENTO_ESPECIALISTA,
            clave=encargo.especialista,
            nombre=informe.nombre,
            encargo_id=encargo.id,
            estado="done",
            resultado=informe.estado,
            duracion_ms=informe.duracion_ms,
            cifras=len(informe.cifras),
            profundidad=ctx.profundidad,
        )
        return informe

    def _resultado(self, futuro: Future[InformeEspecialista], tarea: _Tarea) -> InformeEspecialista:
        try:
            return futuro.result()
        except Exception as exc:  # pragma: no cover - _tarea isolates the specialist; this is a bug in the runtime
            logger.exception("dispatcher task %s failed", tarea.encargo.id)
            return informe_fallido(
                tarea.encargo, self._nombre(tarea.encargo), "error", f"Fallo interno ({type(exc).__name__})."
            )

    def _nombre(self, encargo: Encargo) -> str:
        return str(getattr(self._especialistas.get(encargo.especialista), "nombre", "") or encargo.especialista)

    def _emitir_fin(self, emitir: Emit, informe: InformeEspecialista, *, motivo: str) -> None:
        emitir(
            EVENTO_ESPECIALISTA,
            clave=informe.especialista,
            nombre=informe.nombre,
            encargo_id=informe.encargo_id,
            estado="done",
            resultado=informe.estado,
            duracion_ms=informe.duracion_ms,
            cifras=0,
            profundidad=0,
            motivo=motivo,
        )

    # ------------------------------------------------------------------ figures

    def _con_peticiones(
        self, informe: InformeEspecialista, registros: Sequence[RegistroPeticion]
    ) -> InformeEspecialista:
        """``informe`` with the figures and fragments its tree received through the switchboard, and its requests."""
        if not registros:
            return informe
        recibidas = [c for r in registros if r.respuesta.estado == "ok" for c in r.respuesta.cifras]
        cifras = unicas([*informe.cifras, *recibidas])
        avisos = list(informe.avisos)
        if len(cifras) > MAX_CIFRAS_POR_INFORME:
            cifras = cifras[:MAX_CIFRAS_POR_INFORME]
            avisos.append(f"Solo se conservan {MAX_CIFRAS_POR_INFORME} cifras de este informe.")
        fragmentos = list(
            dict.fromkeys(
                [
                    *informe.fragment_ids,
                    *(f for r in registros if r.respuesta.estado == "ok" for f in r.respuesta.fragment_ids),
                ]
            )
        )
        propias = {p.id for p in informe.peticiones}
        peticiones = [
            *informe.peticiones,
            *(r.peticion for r in registros if r.directa and r.peticion.id not in propias),
        ]
        return informe.model_copy(
            update={"cifras": cifras, "fragment_ids": fragmentos, "peticiones": peticiones, "avisos": avisos}
        )

    def _renumerar(self, cifra: Cifra) -> Cifra:
        clave = clave_de_cifra(cifra)
        nuevo = self._ids_cifras.get(clave)
        if nuevo is None:
            if len(self._ids_cifras) >= _MAX_ID_CIFRA:  # pragma: no cover - far beyond 6 reports × 60 figures
                return cifra
            nuevo = f"C{len(self._ids_cifras) + 1}"
            self._ids_cifras[clave] = nuevo
        return cifra if cifra.id == nuevo else cifra.model_copy(update={"id": nuevo})

    def _numerar(self, informe: InformeEspecialista) -> InformeEspecialista:
        cifras = unicas([self._renumerar(c) for c in informe.cifras])
        return informe.model_copy(update={"cifras": cifras})


def resumen_de_encargos(informes: Sequence[InformeEspecialista]) -> list[dict[str, Any]]:
    """A compact view of the reports for logs and traces (no report text)."""
    return [
        {
            "encargo_id": i.encargo_id,
            "especialista": i.especialista,
            "estado": i.estado,
            "cifras": [c.id for c in i.cifras],
            "duracion_ms": i.duracion_ms,
        }
        for i in informes
    ]


__all__ = ["Despachador", "normalizar_pregunta", "ondas", "orden_topologico", "resumen_de_encargos"]
