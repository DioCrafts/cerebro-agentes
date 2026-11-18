"""The gate: a prompt or model change (a variant) is applied only if it does NOT make the evaluation worse.

``promote`` runs the active variant (baseline) and the candidate on the same cases — the versioned suite PLUS the
accepted real cases (:mod:`asistente.nucleo.evaluacion.privado`) — with the same provider factory, and writes the
candidate as the active variant only when every rule holds (:func:`evaluar_promocion`):

1. **comparable** — both runs complete (no case skipped for budget), on the same cases (same fingerprint), against
   a model (a script replay measures no model);
2. **enough evidence** — at least ``min_casos`` paired cases (``EVALUACION_GATE_MIN_CASOS``, 30); fewer is
   «evidencia insuficiente», never a promotion: a small sample cannot show that nothing got worse;
3. **no critical case regresses** — not one case of a critical category (``critica`` in ``casos/suite.json``: the
   tax figures, routing, never adding across specialists, the ``actua`` guarantee, real regressions, the brain's
   understanding, provenance, memory, the fiscal pass-through…) may score lower than with the active variant,
   whatever the average says;
4. **not worse overall** — the paired bootstrap 95 % interval of the mean per-case difference must not go below 0,
   and the exact McNemar test must not show that the candidate breaks significantly more than it fixes
   (:mod:`asistente.nucleo.evaluacion.estadistica`);
5. **within budget** — cost and p95 latency on the paired cases at most ``EVALUACION_GATE_COSTE_MAX_RELATIVO``
   (1.10) and ``EVALUACION_GATE_LATENCIA_MAX_RELATIVA`` (1.25) times the active variant's.

The decision and its evidence are written to an auditable report (:class:`InformePromocion`: JSON, plus a Spanish
summary from :func:`resumen_es`), whether the candidate is promoted or not.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.evaluacion.estadistica import bootstrap_pareado, emparejar, mcnemar_exacto, p95, semilla_de
from asistente.nucleo.evaluacion.modelos import CasoEvaluacion, ConfigSuite, InformeEvaluacion, ResultadoCaso
from asistente.nucleo.evaluacion.runner import Entorno, comparar, evaluar
from asistente.nucleo.evaluacion.variantes import Variante, escribir_activa, variante_activa
from asistente.plataforma import settings

Decision = Literal["promovida", "rechazada", "evidencia_insuficiente"]
ALFA_MCNEMAR = 0.05


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Umbrales(_Modelo):
    min_casos: int = Field(default=30, ge=1)
    coste_max_relativo: float = Field(default=1.10, gt=0)
    latencia_max_relativa: float = Field(default=1.25, gt=0)
    # Latency differences below this many seconds (p95) are noise, never a reason to refuse.
    latencia_tolerancia_s: float = Field(default=0.5, ge=0)
    nivel: float = Field(default=0.95, gt=0.5, lt=1)
    remuestras: int = Field(default=10_000, ge=100)

    @classmethod
    def desde_settings(cls) -> Umbrales:
        return cls(
            min_casos=int(getattr(settings, "EVALUACION_GATE_MIN_CASOS", 30)),
            coste_max_relativo=float(getattr(settings, "EVALUACION_GATE_COSTE_MAX_RELATIVO", 1.10)),
            latencia_max_relativa=float(getattr(settings, "EVALUACION_GATE_LATENCIA_MAX_RELATIVA", 1.25)),
        )


class Estadisticos(_Modelo):
    n_pares: int
    tasa_base: float
    tasa_candidata: float
    media_delta: float
    ic_inferior: float
    ic_superior: float
    nivel: float
    remuestras: int
    semilla: int
    regresiones: int
    mejoras: int
    p_mcnemar_bilateral: float
    p_empeora: float


class Presupuesto(_Modelo):
    coste_base_usd: Decimal
    coste_candidata_usd: Decimal
    coste_relativo: float | None = None
    # Some calls had no known price: the cost is a lower bound.
    coste_cota_inferior: bool = False
    latencia_p95_base_s: float
    latencia_p95_candidata_s: float
    latencia_relativa: float | None = None


class InformePromocion(_Modelo):
    """The gate's decision and all its evidence (written next to both runs' reports)."""

    decision: Decision
    candidata: str
    base: str
    generado_el: datetime
    suite_version: str
    suite_huella: str
    casos_reales: int = 0
    umbrales: Umbrales
    estadisticos: Estadisticos
    presupuesto: Presupuesto
    regresiones_criticas: list[str] = Field(default_factory=list)
    regresiones: list[str] = Field(default_factory=list)
    mejoras: list[str] = Field(default_factory=list)
    motivos: list[str] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)

    @property
    def promovida(self) -> bool:
        return self.decision == "promovida"


def _coste(resultados: Sequence[ResultadoCaso], ids: set[str]) -> tuple[Decimal, bool]:
    total = Decimal(0)
    cota = False
    for r in resultados:
        if r.id in ids:
            total += r.ejecucion.coste_usd or Decimal(0)
            cota = cota or r.ejecucion.llamadas_sin_coste > 0
    return total, cota


def evaluar_promocion(
    base: InformeEvaluacion, candidata: InformeEvaluacion, config: ConfigSuite, umbrales: Umbrales | None = None
) -> InformePromocion:
    """The decision on two finished runs. Pure: only the two reports, the critical categories and the thresholds."""
    umbrales = umbrales or Umbrales.desde_settings()
    motivos: list[str] = []
    no_comparable = False
    if base.suite_huella != candidata.suite_huella:
        motivos.append("Las dos ejecuciones no usaron los mismos casos (huella distinta): no son comparables.")
        no_comparable = True
    if base.incompleto or candidata.incompleto:
        motivos.append("Alguna ejecución quedó incompleta (presupuesto agotado): no se puede decidir.")
        no_comparable = True
    if base.modo != "modelo" or candidata.modo != "modelo":
        motivos.append("Una reproducción de guiones no mide ningún modelo: la promoción exige ejecutar el modelo.")
        no_comparable = True
    pares, avisos = emparejar(base, candidata)
    criticas = {nombre for nombre, cat in config.categorias.items() if cat.critica}
    deltas = [p.delta for p in pares]
    intervalo = bootstrap_pareado(
        deltas,
        remuestras=umbrales.remuestras,
        nivel=umbrales.nivel,
        semilla=semilla_de(base.suite_huella, base.variante, candidata.variante, str(len(pares))),
    )
    regresiones = [p.id for p in pares if p.base_aprobado and not p.candidata_aprobado]
    mejoras = [p.id for p in pares if p.candidata_aprobado and not p.base_aprobado]
    prueba = mcnemar_exacto(len(regresiones), len(mejoras))
    regresiones_criticas = [p.id for p in pares if (p.critica or p.categoria in criticas) and p.delta < 0]
    ids = {p.id for p in pares}
    coste_b, cota_b = _coste(base.casos, ids)
    coste_c, cota_c = _coste(candidata.casos, ids)
    lat_b = p95([p.latencia_base_s for p in pares])
    lat_c = p95([p.latencia_candidata_s for p in pares])
    presupuesto = Presupuesto(
        coste_base_usd=coste_b,
        coste_candidata_usd=coste_c,
        coste_relativo=round(float(coste_c / coste_b), 4) if coste_b > 0 else None,
        coste_cota_inferior=cota_b or cota_c,
        latencia_p95_base_s=round(lat_b, 3),
        latencia_p95_candidata_s=round(lat_c, 3),
        latencia_relativa=round(lat_c / lat_b, 4) if lat_b > 0 else None,
    )
    n = len(pares)
    estadisticos = Estadisticos(
        n_pares=n,
        tasa_base=round(sum(p.base for p in pares) / n, 4) if n else 0.0,
        tasa_candidata=round(sum(p.candidata for p in pares) / n, 4) if n else 0.0,
        media_delta=intervalo.media,
        ic_inferior=intervalo.inferior,
        ic_superior=intervalo.superior,
        nivel=intervalo.nivel,
        remuestras=intervalo.remuestras,
        semilla=intervalo.semilla,
        regresiones=prueba.regresiones,
        mejoras=prueba.mejoras,
        p_mcnemar_bilateral=prueba.p_bilateral,
        p_empeora=prueba.p_empeora,
    )
    decision: Decision = "promovida"
    if no_comparable:
        decision = "rechazada"
    elif n < umbrales.min_casos:
        decision = "evidencia_insuficiente"
        motivos.append(
            f"Evidencia insuficiente: {n} casos comparables y hacen falta al menos {umbrales.min_casos} para "
            "afirmar que no empeora. No se promueve."
        )
    else:
        if regresiones_criticas:
            motivos.append(
                f"Empeora {len(regresiones_criticas)} caso(s) de categorías críticas: {', '.join(regresiones_criticas)}."
            )
        if intervalo.inferior < 0:
            motivos.append(
                f"Puede empeorar: el intervalo del {intervalo.nivel:.0%} de la diferencia media por caso es "
                f"[{intervalo.inferior:+.3f}, {intervalo.superior:+.3f}] y baja de 0."
            )
        if prueba.p_empeora < ALFA_MCNEMAR and prueba.regresiones > prueba.mejoras:
            motivos.append(
                f"Rompe más de lo que arregla: {prueba.regresiones} regresiones frente a {prueba.mejoras} mejoras "
                f"(McNemar exacto, p = {prueba.p_empeora:.3f})."
            )
        if presupuesto.coste_relativo is not None and presupuesto.coste_relativo > umbrales.coste_max_relativo:
            motivos.append(
                f"Cuesta {presupuesto.coste_relativo:.2f} veces lo de la variante activa (máximo "
                f"{umbrales.coste_max_relativo:.2f})."
            )
        elif presupuesto.coste_relativo is None and coste_c > 0 and coste_b == 0 and not cota_b:
            motivos.append("La variante activa no costó nada en estos casos y la candidata sí.")
        if (
            presupuesto.latencia_relativa is not None
            and presupuesto.latencia_relativa > umbrales.latencia_max_relativa
            and lat_c - lat_b > umbrales.latencia_tolerancia_s
        ):
            motivos.append(
                f"Es más lenta: p95 {lat_c:.1f} s frente a {lat_b:.1f} s ({presupuesto.latencia_relativa:.2f} veces; "
                f"máximo {umbrales.latencia_max_relativa:.2f})."
            )
        if motivos:
            decision = "rechazada"
    if presupuesto.coste_cota_inferior:
        avisos.append("Hubo llamadas sin precio conocido: los costes son una cota inferior.")
    if decision == "promovida":
        motivos.append(
            f"No empeora: {n} casos comparables, diferencia media {intervalo.media:+.3f} con intervalo del "
            f"{intervalo.nivel:.0%} [{intervalo.inferior:+.3f}, {intervalo.superior:+.3f}], ninguna regresión en "
            f"categorías críticas, {len(mejoras)} mejora(s) y {len(regresiones)} regresión(es) no crítica(s)."
        )
    return InformePromocion(
        decision=decision,
        candidata=candidata.variante,
        base=base.variante,
        generado_el=datetime.now(UTC),
        suite_version=candidata.suite_version,
        suite_huella=candidata.suite_huella,
        casos_reales=candidata.con_casos_reales,
        umbrales=umbrales,
        estadisticos=estadisticos,
        presupuesto=presupuesto,
        regresiones_criticas=regresiones_criticas,
        regresiones=regresiones,
        mejoras=mejoras,
        motivos=motivos,
        avisos=avisos,
    )


def decidir(
    base: InformeEvaluacion, candidata: InformeEvaluacion, config: ConfigSuite, umbrales: Umbrales | None = None
) -> tuple[bool, list[str]]:
    """(promote?, reasons): :func:`evaluar_promocion` in short."""
    informe = evaluar_promocion(base, candidata, config, umbrales)
    return informe.promovida, informe.motivos


def resumen_es(informe: InformePromocion) -> str:
    """The decision in plain Spanish, for the terminal, the CI summary and the audit trail."""
    e, p = informe.estadisticos, informe.presupuesto
    titulo = {
        "promovida": "PROMOVIDA",
        "rechazada": "RECHAZADA",
        "evidencia_insuficiente": "NO PROMOVIDA (evidencia insuficiente)",
    }[informe.decision]
    lineas = [
        f"Candidata «{informe.candidata}» frente a «{informe.base}»: {titulo}",
        f"Casos: {e.n_pares} comparables (suite {informe.suite_version} [{informe.suite_huella[:12]}]"
        + (f", {informe.casos_reales} casos reales aceptados" if informe.casos_reales else "")
        + f"; mínimo {informe.umbrales.min_casos}).",
        f"Puntuación media: {e.tasa_base:.1%} → {e.tasa_candidata:.1%}; diferencia {e.media_delta:+.3f}, "
        f"IC {e.nivel:.0%} bootstrap pareado [{e.ic_inferior:+.3f}, {e.ic_superior:+.3f}] "
        f"({e.remuestras} remuestras, semilla {e.semilla}).",
        f"Discordantes: {e.mejoras} mejoras, {e.regresiones} regresiones; McNemar exacto p = {e.p_mcnemar_bilateral:.3f} "
        f"(bilateral), p = {e.p_empeora:.3f} (que empeore).",
        f"Coste: {p.coste_base_usd} → {p.coste_candidata_usd} USD"
        + (f" ({p.coste_relativo:.2f}×)" if p.coste_relativo is not None else "")
        + (" — cota inferior" if p.coste_cota_inferior else "")
        + f"; latencia p95: {p.latencia_p95_base_s:.1f} → {p.latencia_p95_candidata_s:.1f} s.",
    ]
    if informe.regresiones_criticas:
        lineas.append("Regresiones críticas: " + ", ".join(informe.regresiones_criticas))
    if informe.regresiones:
        lineas.append("Regresiones: " + ", ".join(informe.regresiones))
    if informe.mejoras:
        lineas.append("Mejoras: " + ", ".join(informe.mejoras))
    lineas += [f"Motivo: {m}" for m in informe.motivos]
    lineas += [f"Aviso: {a}" for a in informe.avisos]
    return "\n".join(lineas)


def guardar_informe(informe: InformePromocion, directorio: Path, *, marca: str | None = None) -> tuple[Path, Path]:
    """Write the decision as JSON and its Spanish summary as text. Returns both paths."""
    marca = marca or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directorio.mkdir(parents=True, exist_ok=True)
    json_path = directorio / f"promote-{marca}-decision.json"
    texto_path = directorio / f"promote-{marca}-decision.txt"
    json_path.write_text(informe.model_dump_json(indent=2) + "\n", encoding="utf-8")
    texto_path.write_text(resumen_es(informe) + "\n", encoding="utf-8")
    return json_path, texto_path


@dataclass
class DecisionPromocion:
    aplicada: bool
    motivos: list[str]
    base: InformeEvaluacion
    candidata: InformeEvaluacion
    ruta_activa: Path | None = None
    regresiones: list[str] = field(default_factory=list)
    informe: InformePromocion | None = None


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
    umbrales: Umbrales | None = None,
    casos_reales: int = 0,
) -> DecisionPromocion:
    """Evaluate baseline and candidate; write the candidate as active only if :func:`evaluar_promocion` says so."""
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
        casos_reales=casos_reales,
    )
    informe_candidata = evaluar(
        casos,
        candidata,
        replace(entorno, identidad=None, avisos=list(entorno.avisos)),
        config=config,
        suite_huella=suite_huella,
        repeticiones=repeticiones,
        progreso=con_etiqueta("candidata"),
        casos_reales=casos_reales,
    )
    informe_candidata.comparacion = comparar(informe_candidata, informe_base)
    if candidata.id == base.id:
        return DecisionPromocion(
            aplicada=False,
            motivos=[f"La candidata «{candidata.id}» tiene el mismo id que la variante activa."],
            base=informe_base,
            candidata=informe_candidata,
        )
    informe = evaluar_promocion(informe_base, informe_candidata, config, umbrales)
    ruta: Path | None = None
    if informe.promovida:
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
                "regresiones": informe.regresiones,
                "decision": informe.model_dump(mode="json", include={"decision", "estadisticos", "presupuesto"}),
            },
            ruta=ruta_activa,
        )
    return DecisionPromocion(
        aplicada=informe.promovida,
        motivos=informe.motivos,
        base=informe_base,
        candidata=informe_candidata,
        ruta_activa=ruta,
        regresiones=informe.regresiones,
        informe=informe,
    )


__all__ = [
    "ALFA_MCNEMAR",
    "DecisionPromocion",
    "Estadisticos",
    "InformePromocion",
    "Presupuesto",
    "Umbrales",
    "decidir",
    "evaluar_promocion",
    "guardar_informe",
    "promover",
    "resumen_es",
]
