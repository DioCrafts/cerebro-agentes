"""Is the council's confidence worth believing? Calibration and agreement metrics, in pure Python.

A ruling says «recomiendo o1, confianza 0,8». That number is only useful if, over many rulings, the ones stated with
80 % confidence are right about 80 % of the time (**calibration**), and if a divided council is a warning the user
should heed — i.e. if disagreement predicts mistakes (**discrimination**). Both are measured here on the evaluation's
decision cases, where «right» is deterministic: the recommendation is among the case's admitted ones.

* :func:`ece` — expected calibration error over equal-width reliability bins (the gap between stated confidence and
  observed accuracy, weighted by how many rulings fall in each bin), with the bins for a reliability diagram;
* :func:`brier` — mean squared error of the confidence as a probability of being right;
* :func:`auroc` — area under the ROC curve (Mann–Whitney U, ties count one half): does a score rank the wrong
  rulings above the right ones? 0.5 = no better than a coin;
* :func:`kendall_w` — Kendall's coefficient of concordance of the members' rankings (0 = no agreement, 1 = full).
  Each member ranks only the OTHER members' proposals, so rankings are incomplete: W is computed from the mean
  pairwise Spearman correlation over the items each pair of rankings shares (``W = ((m − 1)·ρ̄ + 1) / m``, the
  identity that holds for complete rankings), clipped to [0, 1];
* :func:`calibrar` — all of it at once for a list of :class:`PuntoCalibracion`, with bootstrap intervals.

Every function is deterministic (seeded bootstrap) and never calls a model.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math
import random
import statistics

from pydantic import BaseModel, ConfigDict, Field


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BinFiabilidad(_Modelo):
    """One bin of the reliability diagram."""

    inferior: float
    superior: float
    n: int
    confianza_media: float | None = None
    tasa_acierto: float | None = None


def _validar(confianzas: Sequence[float], aciertos: Sequence[bool]) -> None:
    if len(confianzas) != len(aciertos):
        raise ValueError("confianzas y aciertos deben tener la misma longitud")
    if any(not 0.0 <= c <= 1.0 for c in confianzas):
        raise ValueError("las confianzas deben estar entre 0 y 1")


def bins_fiabilidad(confianzas: Sequence[float], aciertos: Sequence[bool], *, n_bins: int = 10) -> list[BinFiabilidad]:
    """Equal-width bins over [0, 1] (the last one closed): mean confidence and accuracy of each."""
    _validar(confianzas, aciertos)
    if n_bins < 1:
        raise ValueError("hace falta al menos un intervalo")
    grupos: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for c, a in zip(confianzas, aciertos, strict=True):
        grupos[min(n_bins - 1, int(c * n_bins))].append((c, a))
    bins: list[BinFiabilidad] = []
    for i, grupo in enumerate(grupos):
        bins.append(
            BinFiabilidad(
                inferior=round(i / n_bins, 6),
                superior=round((i + 1) / n_bins, 6),
                n=len(grupo),
                confianza_media=round(statistics.fmean(c for c, _ in grupo), 6) if grupo else None,
                tasa_acierto=round(sum(a for _, a in grupo) / len(grupo), 6) if grupo else None,
            )
        )
    return bins


def ece(confianzas: Sequence[float], aciertos: Sequence[bool], *, n_bins: int = 10) -> float:
    """Expected calibration error: Σ_b (n_b / n) · |accuracy_b − confidence_b|. 0 for an empty list."""
    bins = bins_fiabilidad(confianzas, aciertos, n_bins=n_bins)
    n = len(confianzas)
    if n == 0:
        return 0.0
    total = 0.0
    for b in bins:
        if b.n and b.confianza_media is not None and b.tasa_acierto is not None:
            total += b.n / n * abs(b.tasa_acierto - b.confianza_media)
    return round(total, 6)


def brier(confianzas: Sequence[float], aciertos: Sequence[bool]) -> float:
    _validar(confianzas, aciertos)
    if not confianzas:
        return 0.0
    return round(statistics.fmean((c - float(a)) ** 2 for c, a in zip(confianzas, aciertos, strict=True)), 6)


def auroc(puntuaciones: Sequence[float], positivos: Sequence[bool]) -> float | None:
    """P(score of a random positive > score of a random negative), ties ½. None without both classes."""
    if len(puntuaciones) != len(positivos):
        raise ValueError("puntuaciones y etiquetas deben tener la misma longitud")
    pos = [s for s, p in zip(puntuaciones, positivos, strict=True) if p]
    neg = [s for s, p in zip(puntuaciones, positivos, strict=True) if not p]
    if not pos or not neg:
        return None
    # Rank-sum with average ranks for ties: O(n log n).
    ordenados = sorted((s, i) for i, s in enumerate(puntuaciones))
    rangos = [0.0] * len(puntuaciones)
    i = 0
    while i < len(ordenados):
        j = i
        while j + 1 < len(ordenados) and ordenados[j + 1][0] == ordenados[i][0]:
            j += 1
        medio = (i + j) / 2 + 1
        for k in range(i, j + 1):
            rangos[ordenados[k][1]] = medio
        i = j + 1
    suma_pos = sum(r for r, p in zip(rangos, positivos, strict=True) if p)
    u = suma_pos - len(pos) * (len(pos) + 1) / 2
    return round(u / (len(pos) * len(neg)), 6)


def _rangos(orden: Sequence[str], comunes: Sequence[str]) -> list[float]:
    posicion = {item: i for i, item in enumerate(x for x in orden if x in set(comunes))}
    return [float(posicion[c]) for c in comunes]


def _spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    n = len(a)
    if n < 2:
        return None
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / (sa * sb)


def kendall_w(rankings: Sequence[Sequence[str]]) -> float | None:
    """Kendall's W of possibly incomplete rankings (best first). None with fewer than 2 usable rankings.

    For complete rankings without ties this is exactly the textbook W. For incomplete ones (each member ranks the
    others' proposals, never its own) the mean pairwise Spearman ρ over the shared items gives W through
    ``W = ((m − 1)·ρ̄ + 1) / m``; pairs that share fewer than two items carry no information and are skipped.
    """
    usables = [list(r) for r in rankings if len(r) >= 2]
    m = len(usables)
    if m < 2:
        return None
    rhos: list[float] = []
    for i in range(m):
        for j in range(i + 1, m):
            comunes = [x for x in usables[i] if x in set(usables[j])]
            rho = _spearman(_rangos(usables[i], comunes), _rangos(usables[j], comunes))
            if rho is not None:
                rhos.append(rho)
    if not rhos:
        return None
    w = ((m - 1) * statistics.fmean(rhos) + 1) / m
    return round(min(1.0, max(0.0, w)), 6)


# --------------------------------------------------------------------------- #
# Everything at once
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PuntoCalibracion:
    """One ruling: its stated confidence, whether it was right, and the members' agreement (None = unknown)."""

    caso: str
    confianza: float
    acierto: bool
    acuerdo: float | None = None


class Intervalo(_Modelo):
    valor: float | None
    inferior: float | None = None
    superior: float | None = None


class Calibracion(_Modelo):
    n: int
    tasa_acierto: float | None = None
    confianza_media: float | None = None
    ece: Intervalo
    brier: float | None = None
    # Does LOW confidence point at the wrong rulings? (score = 1 − confidence, positive = wrong)
    auroc_confianza: Intervalo
    # Does DISAGREEMENT point at the wrong rulings? (score = 1 − Kendall's W, positive = wrong)
    auroc_desacuerdo: Intervalo
    bins: list[BinFiabilidad] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)


def _bootstrap(
    puntos: Sequence[PuntoCalibracion],
    funcion: Callable[[Sequence[PuntoCalibracion]], float | None],
    *,
    remuestras: int,
    semilla: int,
    nivel: float,
) -> tuple[float | None, float | None]:
    rng = random.Random(semilla)
    valores: list[float] = []
    for _ in range(remuestras):
        muestra = rng.choices(list(puntos), k=len(puntos))
        valor = funcion(muestra)
        if valor is not None:
            valores.append(float(valor))
    if len(valores) < remuestras // 2:
        return None, None
    valores.sort()
    alfa = (1 - nivel) / 2
    return round(valores[int(alfa * len(valores))], 6), round(
        valores[min(len(valores) - 1, int((1 - alfa) * len(valores)))], 6
    )


def calibrar(
    puntos: Sequence[PuntoCalibracion],
    *,
    n_bins: int = 10,
    remuestras: int = 1000,
    semilla: int = 0,
    nivel: float = 0.95,
) -> Calibracion:
    """ECE (with a bootstrap interval), Brier, reliability bins and the two AUROCs."""
    avisos: list[str] = []
    n = len(puntos)
    if n == 0:
        return Calibracion(
            n=0,
            ece=Intervalo(valor=None),
            auroc_confianza=Intervalo(valor=None),
            auroc_desacuerdo=Intervalo(valor=None),
        )
    if n < 30:
        avisos.append(f"Solo {n} dictámenes: la calibración es orientativa (hacen falta decenas para afinarla).")

    def _ece(m: Sequence[PuntoCalibracion]) -> float:
        return ece([p.confianza for p in m], [p.acierto for p in m], n_bins=n_bins)

    def _auc_conf(m: Sequence[PuntoCalibracion]) -> float | None:
        return auroc([1 - p.confianza for p in m], [not p.acierto for p in m])

    con_acuerdo = [p for p in puntos if p.acuerdo is not None]

    def _auc_desacuerdo(m: Sequence[PuntoCalibracion]) -> float | None:
        return auroc([1 - (p.acuerdo or 0.0) for p in m], [not p.acierto for p in m])

    ece_inf, ece_sup = _bootstrap(puntos, _ece, remuestras=remuestras, semilla=semilla, nivel=nivel)
    auc_c = _auc_conf(puntos)
    auc_c_ic = (
        _bootstrap(puntos, _auc_conf, remuestras=remuestras, semilla=semilla + 1, nivel=nivel)
        if auc_c is not None
        else (None, None)
    )
    auc_d = _auc_desacuerdo(con_acuerdo) if con_acuerdo else None
    auc_d_ic = (
        _bootstrap(con_acuerdo, _auc_desacuerdo, remuestras=remuestras, semilla=semilla + 2, nivel=nivel)
        if auc_d is not None
        else (None, None)
    )
    if auc_c is None:
        avisos.append("Todos los dictámenes aciertan (o todos fallan): no se puede medir si la confianza discrimina.")
    if not con_acuerdo:
        avisos.append("Ningún dictamen trae el acuerdo de los miembros (W de Kendall).")
    return Calibracion(
        n=n,
        tasa_acierto=round(sum(p.acierto for p in puntos) / n, 6),
        confianza_media=round(statistics.fmean(p.confianza for p in puntos), 6),
        ece=Intervalo(valor=_ece(puntos), inferior=ece_inf, superior=ece_sup),
        brier=brier([p.confianza for p in puntos], [p.acierto for p in puntos]),
        auroc_confianza=Intervalo(valor=auc_c, inferior=auc_c_ic[0], superior=auc_c_ic[1]),
        auroc_desacuerdo=Intervalo(valor=auc_d, inferior=auc_d_ic[0], superior=auc_d_ic[1]),
        bins=bins_fiabilidad([p.confianza for p in puntos], [p.acierto for p in puntos], n_bins=n_bins),
        avisos=avisos,
    )


def resumen_calibracion(c: Calibracion, *, quien: str = "el consejo") -> list[str]:
    """Spanish lines for the decision report."""
    de = f"del {quien[3:]}" if quien.startswith("el ") else f"de {quien}"
    if c.n == 0:
        return [f"Calibración {de}: sin dictámenes con los que medirla."]

    def ic(i: Intervalo) -> str:
        if i.valor is None:
            return "no medible"
        if i.inferior is None or i.superior is None:
            return f"{i.valor:.2f}"
        return f"{i.valor:.2f} [{i.inferior:.2f}, {i.superior:.2f}]"

    lineas = [
        f"Calibración {de} ({c.n} dictámenes): acierta el {c.tasa_acierto or 0:.0%} con una confianza media "
        f"del {c.confianza_media or 0:.0%}; ECE {ic(c.ece)}; Brier {c.brier if c.brier is not None else '—'}.",
        f"  ¿La confianza baja señala los errores? AUROC {ic(c.auroc_confianza)} (0,5 = como una moneda).",
        f"  ¿El desacuerdo señala los errores? AUROC {ic(c.auroc_desacuerdo)}.",
    ]
    lineas.extend(f"  Aviso: {a}" for a in c.avisos)
    return lineas


__all__ = [
    "BinFiabilidad",
    "Calibracion",
    "Intervalo",
    "PuntoCalibracion",
    "auroc",
    "bins_fiabilidad",
    "brier",
    "calibrar",
    "ece",
    "kendall_w",
    "resumen_calibracion",
]
