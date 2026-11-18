"""Paired statistics for the promotion gate: is the candidate variant NOT worse than the active one?

Why paired: both variants run the very same cases, so what matters is the per-case difference (a case that is hard
for both says nothing about the change). Two views of the same pairs:

* **paired bootstrap** of the mean per-case score difference (score = repetitions passed / repetitions, in [0, 1]):
  B resamples of the cases with replacement, percentile 95 % interval. The gate needs its LOWER bound ≥ 0 — i.e.
  the data are compatible only with «not worse». A single uncompensated regression among 40 cases already pushes
  the bound below 0; identical behaviour gives [0, 0] and passes (a cheaper model that is just as good may win).
  The seed is derived from the two reports, so the same reports always give the same interval.
* **exact McNemar (sign) test** on pass/fail: ``b`` cases passed by the active variant and failed by the candidate,
  ``c`` the reverse. Under «no difference» each discordant case is a fair coin; ``p_empeora`` = P(X ≥ b | b + c,
  ½) tests «the candidate breaks more than it fixes», ``p_bilateral`` any difference. Reported, and a significant
  ``p_empeora`` (< 0.05) also blocks.

With fewer than ``min_casos`` pairs nothing is decided: «evidencia insuficiente» (never promoted). Every number
is pure Python on the two reports, so the decision is reproducible and auditable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
from math import comb
import random
import statistics

from asistente.nucleo.evaluacion.modelos import InformeEvaluacion, ResultadoCaso


@dataclass(frozen=True)
class Par:
    """One case run by both variants."""

    id: str
    categoria: str
    critica: bool
    base: float
    candidata: float
    base_aprobado: bool
    candidata_aprobado: bool
    latencia_base_s: float
    latencia_candidata_s: float

    @property
    def delta(self) -> float:
        return self.candidata - self.base


def puntuacion(resultado: ResultadoCaso, repeticiones: int) -> float:
    """The case's score in [0, 1]: the share of repetitions that passed."""
    if repeticiones <= 1:
        return 1.0 if resultado.aprobado else 0.0
    return resultado.repeticiones_aprobadas / repeticiones


def emparejar(base: InformeEvaluacion, candidata: InformeEvaluacion) -> tuple[list[Par], list[str]]:
    """The pairs (same case id AND version, run by both, not omitted), and warnings for what was left out."""
    avisos: list[str] = []
    de_base = {r.id: r for r in base.casos}
    de_candidata = {r.id: r for r in candidata.casos}
    pares: list[Par] = []
    for caso_id in sorted(set(de_base) | set(de_candidata)):
        b, c = de_base.get(caso_id), de_candidata.get(caso_id)
        if b is None or c is None:
            avisos.append(f"«{caso_id}» solo está en una de las dos ejecuciones: no se compara.")
            continue
        if b.omitido or c.omitido:
            continue
        if b.version != c.version:
            avisos.append(f"«{caso_id}» se ejecutó en versiones distintas ({b.version} y {c.version}): no se compara.")
            continue
        pares.append(
            Par(
                id=caso_id,
                categoria=c.categoria,
                critica=b.critica or c.critica,
                base=puntuacion(b, base.repeticiones),
                candidata=puntuacion(c, candidata.repeticiones),
                base_aprobado=b.aprobado,
                candidata_aprobado=c.aprobado,
                latencia_base_s=b.ejecucion.latencia_s,
                latencia_candidata_s=c.ejecucion.latencia_s,
            )
        )
    return pares, avisos


@dataclass(frozen=True)
class Intervalo:
    media: float
    inferior: float
    superior: float
    remuestras: int
    semilla: int
    nivel: float


def bootstrap_pareado(
    diferencias: Sequence[float], *, remuestras: int = 10_000, nivel: float = 0.95, semilla: int = 0
) -> Intervalo:
    """Percentile bootstrap interval of the mean of ``diferencias`` (deterministic for a given seed)."""
    n = len(diferencias)
    if n == 0:
        return Intervalo(0.0, 0.0, 0.0, remuestras, semilla, nivel)
    media = statistics.fmean(diferencias)
    valores = list(diferencias)
    if all(v == valores[0] for v in valores):
        return Intervalo(media, media, media, remuestras, semilla, nivel)
    rng = random.Random(semilla)
    medias = sorted(sum(rng.choices(valores, k=n)) / n for _ in range(remuestras))
    alfa = (1.0 - nivel) / 2.0
    inferior = medias[max(0, int(alfa * remuestras))]
    superior = medias[min(remuestras - 1, int((1.0 - alfa) * remuestras) - 1)]
    return Intervalo(round(media, 6), round(inferior, 6), round(superior, 6), remuestras, semilla, nivel)


@dataclass(frozen=True)
class McNemar:
    regresiones: int  # b: passed before, fails now
    mejoras: int  # c: failed before, passes now
    p_bilateral: float
    p_empeora: float


def mcnemar_exacto(regresiones: int, mejoras: int) -> McNemar:
    """Exact (binomial) McNemar test on the discordant pairs."""
    n = regresiones + mejoras
    if n == 0:
        return McNemar(regresiones, mejoras, 1.0, 1.0)
    total = 2**n
    cola_inferior = sum(comb(n, k) for k in range(min(regresiones, mejoras) + 1)) / total
    p_bilateral = min(1.0, 2.0 * cola_inferior)
    p_empeora = sum(comb(n, k) for k in range(regresiones, n + 1)) / total
    return McNemar(regresiones, mejoras, round(p_bilateral, 6), round(p_empeora, 6))


def semilla_de(*partes: str) -> int:
    """A seed tied to the two reports: the same reports always give the same interval."""
    return int(hashlib.sha256("|".join(partes).encode()).hexdigest()[:12], 16)


def p95(valores: Sequence[float]) -> float:
    ordenados = sorted(valores)
    if not ordenados:
        return 0.0
    return ordenados[min(len(ordenados) - 1, int(0.95 * len(ordenados)))]


__all__ = [
    "Intervalo",
    "McNemar",
    "Par",
    "bootstrap_pareado",
    "emparejar",
    "mcnemar_exacto",
    "p95",
    "puntuacion",
    "semilla_de",
]
