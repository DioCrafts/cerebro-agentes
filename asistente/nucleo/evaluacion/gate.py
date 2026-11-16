"""The gate: a prompt or model change is applied only if it improves on the suite.

``promote`` runs the active variant (baseline) and the candidate on the same suite, with the same provider
factory, and writes the candidate as the active variant only when

1. both runs are complete (no case skipped for budget), on the same suite, against a model (a script
   replay measures no model);
2. the candidate is **at least as good on every critical category** (``critica`` in ``casos/suite.json``);
3. the candidate is **strictly better overall**.

Otherwise nothing is written and the decision carries the reasons and both reports. Regressions in
non-critical categories do not block a promotion that meets those rules, but they are listed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from asistente.nucleo.evaluacion.modelos import CasoEvaluacion, ConfigSuite, InformeEvaluacion, ResultadoCaso
from asistente.nucleo.evaluacion.runner import Entorno, comparar, evaluar
from asistente.nucleo.evaluacion.variantes import Variante, escribir_activa, variante_activa


@dataclass
class DecisionPromocion:
    aplicada: bool
    motivos: list[str]
    base: InformeEvaluacion
    candidata: InformeEvaluacion
    ruta_activa: Path | None = None
    regresiones: list[str] = field(default_factory=list)


def decidir(base: InformeEvaluacion, candidata: InformeEvaluacion, config: ConfigSuite) -> tuple[bool, list[str]]:
    """(promote?, reasons). Pure: only the two reports and the suite's critical categories."""
    motivos: list[str] = []
    if base.suite_huella != candidata.suite_huella:
        motivos.append("Las dos ejecuciones no usaron la misma suite: no son comparables.")
    if base.incompleto or candidata.incompleto:
        motivos.append("Alguna ejecución quedó incompleta (presupuesto agotado): no se puede decidir.")
    if base.modo != "modelo" or candidata.modo != "modelo":
        motivos.append("Una reproducción de guiones no mide ningún modelo: la promoción exige ejecutar el modelo.")
    criticas = sorted(nombre for nombre, cat in config.categorias.items() if cat.critica)
    for categoria in criticas:
        b = base.resumen.por_categoria.get(categoria)
        c = candidata.resumen.por_categoria.get(categoria)
        if b is None and c is None:
            continue
        tasa_b = b.tasa if b else 0.0
        tasa_c = c.tasa if c else 0.0
        if tasa_c < tasa_b:
            motivos.append(f"Empeora la categoría crítica «{categoria}»: {tasa_c:.1%} frente a {tasa_b:.1%}.")
    if candidata.resumen.tasa <= base.resumen.tasa:
        motivos.append(
            f"No mejora en conjunto: {candidata.resumen.tasa:.1%} frente a {base.resumen.tasa:.1%} "
            "(hace falta ser estrictamente mejor)."
        )
    if motivos:
        return False, motivos
    return True, [
        f"Mejora en conjunto ({candidata.resumen.tasa:.1%} frente a {base.resumen.tasa:.1%}) sin empeorar ninguna "
        f"categoría crítica ({', '.join(criticas) or 'ninguna definida'})."
    ]


def promover(
    candidata: Variante,
    entorno: Entorno,
    casos: Sequence[CasoEvaluacion],
    *,
    config: ConfigSuite,
    suite_huella: str,
    base: Variante | None = None,
    repeticiones: int = 1,
    ruta_activa: Path | None = None,
    progreso: Callable[[str, CasoEvaluacion, ResultadoCaso], None] | None = None,
) -> DecisionPromocion:
    """Evaluate baseline and candidate; write the candidate as active only if :func:`decidir` says so."""
    base = base or variante_activa()

    def con_etiqueta(etiqueta: str) -> Callable[[CasoEvaluacion, ResultadoCaso], None] | None:
        if progreso is None:
            return None
        return lambda caso, resultado: progreso(etiqueta, caso, resultado)

    informe_base = evaluar(
        casos,
        base,
        replace(entorno, identidad=None, avisos=list(entorno.avisos)),
        config=config,
        suite_huella=suite_huella,
        repeticiones=repeticiones,
        progreso=con_etiqueta("base"),
    )
    informe_candidata = evaluar(
        casos,
        candidata,
        replace(entorno, identidad=None, avisos=list(entorno.avisos)),
        config=config,
        suite_huella=suite_huella,
        repeticiones=repeticiones,
        progreso=con_etiqueta("candidata"),
    )
    informe_candidata.comparacion = comparar(informe_candidata, informe_base)
    if candidata.id == base.id:
        return DecisionPromocion(
            aplicada=False,
            motivos=[f"La candidata «{candidata.id}» tiene el mismo id que la variante activa."],
            base=informe_base,
            candidata=informe_candidata,
        )
    aplicada, motivos = decidir(informe_base, informe_candidata, config)
    ruta: Path | None = None
    if aplicada:
        r = informe_candidata.resumen
        ruta = escribir_activa(
            candidata,
            frente_a=base.id,
            resumen={
                "suite_version": informe_candidata.suite_version,
                "suite_huella": informe_candidata.suite_huella,
                "tasa_base": informe_base.resumen.tasa,
                "tasa_candidata": r.tasa,
                "por_categoria": {k: v.tasa for k, v in r.por_categoria.items()},
                "coste_usd_candidata": str(r.coste_usd),
                "regresiones": informe_candidata.comparacion.regresiones,
            },
            ruta=ruta_activa,
        )
    return DecisionPromocion(
        aplicada=aplicada,
        motivos=motivos,
        base=informe_base,
        candidata=informe_candidata,
        ruta_activa=ruta,
        regresiones=informe_candidata.comparacion.regresiones,
    )


__all__ = ["DecisionPromocion", "decidir", "promover"]
