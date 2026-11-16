"""The recodificación through a batch API, and escalation of a reader that fails the gate.

Batch mode (``RECODE_BATCH=true``, off by default) runs ``params-recode``, the
autopilot and ``params-eval`` through the provider's batch API (about half the
price; results within hours). It reuses the synchronous loop unchanged by
replay (:mod:`asistente.nucleo.agent.providers.lotes`): rounds of
:func:`~asistente.conocimiento.recodificacion.nucleo.recodificar` with stand-in
providers discover the next call of every group, those calls go out as one
batch, and once no group needs anything else the real
:func:`~asistente.conocimiento.recodificacion.nucleo.ejecutar` runs with the answers:
same deterministic gate, same reviewer, same proposals, same saving and
auto-application as a synchronous run with those answers.

Escalation: when a reader on a cheaper tier ends a group «sin_converger» after
failing the deterministic gate, the group is re-read ONCE by the reader of the
next tier (another model), if routing gives one (``agent/enrutado.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from functools import partial
import logging
from pathlib import Path
from typing import Any

from asistente.ambitos.fiscal.calculo.params import DATA_DIR
from asistente.conocimiento.recodificacion.nucleo import (
    MAX_ITER,
    AlmacenPropuestas,
    Informe,
    Objetivo,
    Propuesta,
    RecodificacionError,
    SinTextoError,
    clave_de,
    ejecutar,
    ficheros_de,
    leer_texto,
    recodificar,
)
from asistente.conocimiento.verificacion import TextoDe
from asistente.nucleo.agent.providers.base import ProviderError
from asistente.nucleo.agent.providers.lotes import (
    ColaDeLotes,
    LoteSinTerminarError,
    PendienteDeLoteError,
    ProveedorEnLote,
    admite_lotes,
    ejecutar_en_lotes,
)
from asistente.plataforma import settings

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Escalation
# --------------------------------------------------------------------------- #


def fallo_el_control(propuesta: Propuesta) -> bool:
    """Whether the reader's output failed the deterministic gate in the run (not a reviewer's objection)."""
    for iteracion in propuesta.iteraciones:
        if iteracion.control is not None and not iteracion.control.ok:
            return True
        if any(objecion == "valor_json no es JSON válido." for objecion in iteracion.objeciones):
            return True
    return False


def recodificar_con_escalado(
    objetivo: Objetivo,
    *,
    lector: Any,
    revisor: Any,
    texto_de: TextoDe,
    datos: dict[int, Any] | None = None,
    max_iter: int = MAX_ITER,
    ahora: datetime | None = None,
) -> Propuesta:
    """:func:`recodificar`, and once more with the next tier's reader when the first run failed the gate.

    Only when the proposal did not converge, the deterministic gate rejected the
    reader's output at least once, and routing has another model on the tier above
    (``lector.escalado()``; with no routing setting there is none, so this is
    exactly :func:`recodificar`). The returned proposal is the second run's, with
    the calls and characters of both.
    """
    propuesta = recodificar(
        objetivo, lector=lector, revisor=revisor, texto_de=texto_de, datos=datos, max_iter=max_iter, ahora=ahora
    )
    if propuesta.estado != "sin_converger" or not fallo_el_control(propuesta):
        return propuesta
    escalar = getattr(lector, "escalado", None)
    superior = escalar() if callable(escalar) else None
    if superior is None:
        return propuesta
    logger.info(
        "Re-reading %s with %s/%s: the reader %s failed the gate.",
        objetivo.etiqueta,
        superior.name,
        superior.model,
        getattr(lector, "model", "?"),
    )
    segunda = recodificar(
        objetivo, lector=superior, revisor=revisor, texto_de=texto_de, datos=datos, max_iter=max_iter, ahora=ahora
    )
    return segunda.model_copy(
        update={
            "llamadas_llm": propuesta.llamadas_llm + segunda.llamadas_llm,
            "caracteres_enviados": propuesta.caracteres_enviados + segunda.caracteres_enviados,
        }
    )


# --------------------------------------------------------------------------- #
# Batch mode
# --------------------------------------------------------------------------- #


def _ficheros(data_dir: Path, objetivo: Objetivo) -> dict[int, Any]:
    return ficheros_de(data_dir, objetivo)


def a_procesar(
    objetivos: Sequence[Objetivo],
    *,
    texto_de: TextoDe,
    almacen: AlmacenPropuestas,
    max_objetivos: int | None,
    forzar: bool,
) -> list[Objetivo]:
    """The groups :func:`ejecutar` would run the loop on, by its own rules (same order)."""
    elegidos: list[Objetivo] = []
    for objetivo in objetivos:
        try:
            _, texto = leer_texto(objetivo, texto_de)
        except SinTextoError:
            continue
        if almacen.con_clave(clave_de(objetivo, texto)) is not None and not forzar:
            continue
        if max_objetivos is not None and len(elegidos) >= max_objetivos:
            continue
        elegidos.append(objetivo)
    return elegidos


def _opciones_de_espera() -> dict[str, Any]:
    return {
        "intervalo": settings.LLM_BATCH_POLL_SECONDS,
        "max_espera": timedelta(hours=settings.LLM_BATCH_MAX_WAIT_HOURS),
    }


class EjecutorPorLotes:
    """Drop-in for :func:`ejecutar` that pays the model calls at batch prices.

    ``resumen`` holds, after each call, the batches sent (ids, sizes, cost).
    """

    def __init__(
        self,
        estado_path: str | Path,
        *,
        recodificador: Callable[..., Propuesta] = recodificar_con_escalado,
        esperar: Callable[[float], None] | None = None,
        **opciones: Any,
    ) -> None:
        self.estado_path = Path(estado_path)
        self.recodificador = recodificador
        self.esperar = esperar
        self.opciones = opciones
        self.resumen: dict[str, Any] = {}

    def __call__(
        self,
        objetivos: Sequence[Objetivo],
        *,
        lector: Any,
        revisor: Any,
        texto_de: TextoDe,
        almacen: AlmacenPropuestas,
        data_dir: Path = DATA_DIR,
        max_iter: int = MAX_ITER,
        max_objetivos: int | None = None,
        auto_aplicar: bool = False,
        forzar: bool = False,
        hoy: date | None = None,
        al_terminar: Callable[[Propuesta], None] | None = None,
    ) -> tuple[Informe, list[Propuesta]]:
        comunes: dict[str, Any] = {
            "texto_de": texto_de,
            "almacen": almacen,
            "data_dir": data_dir,
            "max_iter": max_iter,
            "max_objetivos": max_objetivos,
            "auto_aplicar": auto_aplicar,
            "forzar": forzar,
            "hoy": hoy,
            "al_terminar": al_terminar,
            "recodificador": self.recodificador,
        }
        if not (admite_lotes(lector) and admite_lotes(revisor)):
            logger.warning(
                "RECODE_BATCH: provider %s has no batch API; running the recodificación synchronously.",
                getattr(lector, "name", "?"),
            )
            return ejecutar(objetivos, lector=lector, revisor=revisor, **comunes)
        from asistente.plataforma.ops.consumo import PresupuestoAgotadoError

        cola = ColaDeLotes(self.estado_path, tipo="recodificacion")
        lector_lote, revisor_lote = ProveedorEnLote(lector, cola), ProveedorEnLote(revisor, cola)
        grupos = a_procesar(objetivos, texto_de=texto_de, almacen=almacen, max_objetivos=max_objetivos, forzar=forzar)

        def ronda() -> None:
            for objetivo in grupos:
                try:
                    self.recodificador(
                        objetivo,
                        lector=lector_lote,
                        revisor=revisor_lote,
                        texto_de=texto_de,
                        datos=_ficheros(data_dir, objetivo),
                        max_iter=max_iter,
                    )
                except PendienteDeLoteError:
                    continue  # its next call is queued
                except PresupuestoAgotadoError:
                    return  # the final run reports it
                except (ProviderError, RecodificacionError):
                    continue  # the final run reports it, as the synchronous one would

        espera = {**_opciones_de_espera(), **self.opciones}
        try:
            ejecutar_en_lotes(
                ronda,
                cola,
                contable=lector,
                esperar=self.esperar or _dormir,
                intervalo=espera["intervalo"],
                max_espera=espera["max_espera"],
            )
        except LoteSinTerminarError as exc:
            self.resumen = cola.resumen()
            informe = Informe(objetivos=len(objetivos), errores=[str(exc)], sin_procesar=[g.etiqueta for g in grupos])
            return informe, []
        # Every answer is known (or the budget closed the queue): the real run, with its side effects.
        informe, nuevas = ejecutar(objetivos, lector=lector_lote, revisor=revisor_lote, **comunes)
        self.resumen = cola.resumen()
        logger.info("Recodificación by batches: %s", self.resumen)
        cola.terminar()
        return informe, nuevas


class EvaluadorPorLotes:
    """Drop-in for :func:`~asistente.conocimiento.recodificacion.evaluacion.evaluar` at batch prices."""

    def __init__(self, estado_path: str | Path, *, esperar: Callable[[float], None] | None = None, **opciones: Any):
        self.estado_path = Path(estado_path)
        self.esperar = esperar
        self.opciones = opciones
        self.resumen: dict[str, Any] = {}

    def __call__(self, objetivos: Sequence[Objetivo], *, lector: Any, revisor: Any, **kwargs: Any) -> Any:
        from asistente.conocimiento.recodificacion import evaluacion

        if not (admite_lotes(lector) and admite_lotes(revisor)):
            logger.warning("RECODE_BATCH: provider without a batch API; evaluating synchronously.")
            return evaluacion.evaluar(objetivos, lector=lector, revisor=revisor, **kwargs)
        cola = ColaDeLotes(self.estado_path, tipo="evaluacion")
        lector_lote, revisor_lote = ProveedorEnLote(lector, cola), ProveedorEnLote(revisor, cola)
        espera = {**_opciones_de_espera(), **self.opciones}
        try:
            # ``evaluar`` writes nothing: each round is the whole evaluation, the last one is the result.
            resultado = ejecutar_en_lotes(
                lambda: evaluacion.evaluar(objetivos, lector=lector_lote, revisor=revisor_lote, **kwargs),
                cola,
                contable=lector,
                esperar=self.esperar or _dormir,
                intervalo=espera["intervalo"],
                max_espera=espera["max_espera"],
            )
        except LoteSinTerminarError as exc:
            self.resumen = cola.resumen()
            return evaluacion.Evaluacion(interrumpida=str(exc))
        self.resumen = cola.resumen()
        cola.terminar()
        return resultado


def _dormir(segundos: float) -> None:
    import time

    time.sleep(segundos)


def _ruta_de_estado(tipo: str) -> Path:
    return Path(settings.LLM_BATCH_STATE_PATH) / f"{tipo}.json"


def ejecutor_de_recodificacion(tipo: str = "params-recode") -> Callable[..., tuple[Informe, list[Propuesta]]]:
    """What runs the recodificación loop, by settings: the batch executor with ``RECODE_BATCH``, else
    :func:`ejecutar` with the escalating reader (identical to plain ``ejecutar`` without routing settings).

    ``tipo`` names the state file of the batches (one per kind of run: ``params-recode``, ``autopilot``).
    """
    if settings.RECODE_BATCH:
        return EjecutorPorLotes(_ruta_de_estado(tipo))
    return partial(ejecutar, recodificador=recodificar_con_escalado)


def evaluador_de_recodificacion() -> Callable[..., Any]:
    """``evaluar``, or its batch version with ``RECODE_BATCH``. The evaluation measures the configured
    reader: it never escalates."""
    if settings.RECODE_BATCH:
        return EvaluadorPorLotes(_ruta_de_estado("params-eval"))
    from asistente.conocimiento.recodificacion import evaluacion

    return evaluacion.evaluar


__all__ = [
    "EjecutorPorLotes",
    "EvaluadorPorLotes",
    "a_procesar",
    "ejecutor_de_recodificacion",
    "evaluador_de_recodificacion",
    "fallo_el_control",
    "recodificar_con_escalado",
]
