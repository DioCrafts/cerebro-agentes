"""Robust aggregation of the council's rankings and votes (pure functions: no model, no I/O, no clock).

The council's reviewers each rank the OTHER members' proposals (never their own). This module turns those rankings
into one order, measures how much the reviewers agree, flags a reviewer far from the rest, and turns the members'
final recommendations into the council's consensus label and confidence. Everything here is deterministic: the same
input always gives the same output (ties are broken by a documented key, optionally seeded).

Rankings
--------
A ranking is a best-first sequence whose elements are an item id or a group of tied ids («A», ``["B", "C"]``, «D»),
or a :class:`~asistente.nucleo.consejo.contrato.Ranking` (its ``orden``, strict). A ranking may cover only SOME
items: in the council a reviewer never ranks its own proposal, so an item missing from a ranking means «not ranked by
that reviewer» — never «tied last» (that would punish every member once) nor «first» (self-promotion by absence).

Methods, and why each one
-------------------------
* **Borda** (:func:`borda`), with ties and partial rankings: an item at mid-rank ``r`` of a ranking of ``k`` items
  scores ``(k − r) / (k − 1)`` (1 = top, 0 = bottom, ties share the average); its Borda score is the MEAN over the
  rankings that contain it, so an item ranked by fewer reviewers is not penalised for it. Robust and transparent, but
  it can miss the Condorcet winner (the Tennessee example in the tests).
* **Kemeny–Young** (:func:`kemeny`), exact, for at most :data:`MAX_KEMENY_EXACTO` (6) candidates: the order that
  agrees with the most pairwise preferences of the reviewers (each reviewer that ranks both ``a`` and ``b`` gives one
  preference, a tie half to each side). It is the maximum-likelihood consensus and the only rule that is neutral,
  consistent and Condorcet at once (Young & Levenglick, 1978). Brute force over the ``n!`` permutations, each scored
  over its ``n(n−1)/2`` pairs: O(n!·n²) — 720 × 15 ≈ 10⁴ steps for 6 candidates. Kemeny is NP-hard in general, so
  above 6 candidates (only a 7-seat council) the aggregation falls back to:
* **Schulze** (:func:`schulze`), O(n³) (widest paths, Floyd–Warshall), Condorcet-consistent and clone-proof, with
  **Copeland** (pairwise wins − losses, :func:`copeland`) and then Borda as tie-breakers.

Several optimal Kemeny orders are possible: the one closest (Kendall distance) to the Borda order wins, then the tie-
break key. :class:`Agregacion` says how many optimal orders there were and whether the top was tied.

Agreement: Kendall's W
----------------------
:func:`acuerdo_kendall`. With complete rankings it is the textbook coefficient of concordance with the tie
correction: ``W = 12·S / (m²(n³ − n) − m·Σ T)``, where ``S`` is the sum of squared deviations of the rank sums,
``m`` the reviewers, ``n`` the items and ``T = Σ (t³ − t)`` over each ranking's groups of ``t`` tied items.

With INCOMPLETE rankings (the council's case: each reviewer ranks ``n − 1`` of ``n`` proposals, a balanced
incomplete block design) the textbook denominator is wrong — perfect agreement would give W < 1 — so ``W = S / S*``
where ``S = Σ_j (R_j − E_j)²`` (``R_j`` the rank sum of item ``j`` over the rankings that contain it, ``E_j`` its
expected value under random rankings) and ``S*`` is the ``S`` of PERFECT agreement with the best possible consensus
order. ``S*`` is computed exactly by dynamic programming over subsets (placing item ``j`` after the set ``A`` gives it
rank sum ``m_j + Σ_{l∈A} c_jl``, with ``m_j`` the rankings containing ``j`` and ``c_jl`` those containing both), in
O(2ⁿ·n²); above :data:`MAX_ITEMS_ACUERDO_EXACTO` items it uses the Borda order (a lower bound of ``S*``; W is
clamped to [0, 1]). For complete strict rankings both formulas coincide. Ties in incomplete rankings use mid-ranks in
``S`` and strict agreement in ``S*`` (conservative: W can only be underestimated). W is None with fewer than
:data:`MIN_RANKINGS_ACUERDO` (3) rankings — the contract's rule — or fewer than 2 items.

Outlier reviewers
-----------------
:func:`revisores_atipicos`: for each reviewer, the share of the pairs it ranked where the OTHER reviewers' majority
prefers the opposite (leave-one-out, so a reviewer cannot pull the consensus towards itself). Above
:data:`UMBRAL_ATIPICO` (more contradictions than agreements), with at least :data:`MIN_PARES_ATIPICO` comparable
pairs, and above the median reviewer's distance (when everyone is that far there is no consensus to deviate from),
it is flagged. A flagged reviewer is SHOWN (to the chair and in the ruling), never silently dropped: dissent in the
rankings is information (the devil's advocate is expected to differ).

Votes and confidence
--------------------
:func:`consenso_de` labels the members' final recommendations: ``unanime`` (all the same), ``mayoria`` (strictly more
than half), ``dividido`` (otherwise, ties for the top included), ``insuficiente`` (fewer than ``MIN_MIEMBROS``).
:func:`confianza_del_consejo`: ``confianza = c̄ × A``, where ``c̄`` is the mean confidence of the members behind the
leading recommendation and ``A`` the agreement: ``A = p`` without W, ``A = (2p + W) / 3`` with it (``p`` = share of
members behind the leading recommendation). Votes weigh twice the ranking agreement because they are about the
decision itself, W only about which argument was best. Monotone: more agreement never lowers the confidence; a
unanimous council in full agreement keeps exactly its members' mean confidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
from itertools import combinations, permutations
from typing import Literal

from asistente.nucleo.consejo.contrato import MIN_MIEMBROS, Consenso, Ranking

MAX_KEMENY_EXACTO = 6
MAX_ITEMS_ACUERDO_EXACTO = 12
MIN_RANKINGS_ACUERDO = 3
UMBRAL_ATIPICO = 0.5
MIN_PARES_ATIPICO = 2
DEPENDE = "depende"

Elemento = str | Sequence[str]
RankingEntrada = Ranking | Sequence[Elemento]
Metodo = Literal["kemeny", "schulze", "trivial"]
Niveles = list[list[str]]


# --------------------------------------------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------------------------------------------


def niveles(ranking: RankingEntrada) -> Niveles:
    """``ranking`` as best-first groups of tied items (each group sorted). Raises ValueError on a repeated item."""
    elementos: Sequence[Elemento] = ranking.orden if isinstance(ranking, Ranking) else ranking
    if isinstance(elementos, str):
        raise ValueError("un ranking es una secuencia de elementos, no un texto")
    vistos: set[str] = set()
    resultado: Niveles = []
    for elemento in elementos:
        grupo = [elemento] if isinstance(elemento, str) else list(elemento)
        grupo = [g for g in grupo if g]
        if not grupo:
            continue
        for item in grupo:
            if not isinstance(item, str):
                raise ValueError(f"elemento de ranking no válido: {item!r}")
            if item in vistos:
                raise ValueError(f"«{item}» aparece dos veces en el mismo ranking")
            vistos.add(item)
        resultado.append(sorted(grupo))
    return resultado


def _todos(rankings: Sequence[Niveles]) -> list[str]:
    return sorted({item for r in rankings for grupo in r for item in grupo})


def rangos_medios(ranking: Niveles) -> dict[str, float]:
    """Mid-ranks 1..k of ``ranking`` (tied items share the average of the positions they occupy)."""
    rangos: dict[str, float] = {}
    posicion = 0
    for grupo in ranking:
        primero = posicion + 1
        ultimo = posicion + len(grupo)
        for item in grupo:
            rangos[item] = (primero + ultimo) / 2
        posicion = ultimo
    return rangos


def _preparar(rankings: Sequence[RankingEntrada]) -> list[Niveles]:
    return [n for n in (niveles(r) for r in rankings) if sum(len(g) for g in n) >= 1]


def clave_desempate(item: str, semilla: int | str | None = None) -> str:
    """The deterministic tie-break key of ``item``: its id, or a hash of (seed, id) so no id is favoured for life."""
    if semilla is None:
        return item
    return hashlib.sha256(f"{semilla}\x1f{item}".encode()).hexdigest()


# --------------------------------------------------------------------------------------------------------------
# Scores
# --------------------------------------------------------------------------------------------------------------


def borda(rankings: Sequence[RankingEntrada]) -> dict[str, float]:
    """Normalised Borda score in [0, 1] per item: mean over the rankings that contain it (see the module docstring)."""
    preparados = _preparar(rankings)
    sumas: dict[str, float] = {}
    cuentas: dict[str, int] = {}
    for ranking in preparados:
        k = sum(len(g) for g in ranking)
        if k < 2:
            continue
        for item, rango in rangos_medios(ranking).items():
            sumas[item] = sumas.get(item, 0.0) + (k - rango) / (k - 1)
            cuentas[item] = cuentas.get(item, 0) + 1
    return {item: sumas.get(item, 0.0) / cuentas[item] if item in cuentas else 0.0 for item in _todos(preparados)}


def matriz_preferencias(rankings: Sequence[RankingEntrada]) -> dict[tuple[str, str], float]:
    """``N[(a, b)]``: how many rankings put ``a`` above ``b`` (a tie gives ½ to each side; absent pairs count 0)."""
    matriz: dict[tuple[str, str], float] = {}
    for ranking in _preparar(rankings):
        rangos = rangos_medios(ranking)
        for a, b in combinations(sorted(rangos), 2):
            if rangos[a] < rangos[b]:
                matriz[(a, b)] = matriz.get((a, b), 0.0) + 1
            elif rangos[b] < rangos[a]:
                matriz[(b, a)] = matriz.get((b, a), 0.0) + 1
            else:
                matriz[(a, b)] = matriz.get((a, b), 0.0) + 0.5
                matriz[(b, a)] = matriz.get((b, a), 0.0) + 0.5
    return matriz


def _n(matriz: Mapping[tuple[str, str], float], a: str, b: str) -> float:
    return matriz.get((a, b), 0.0)


def copeland(rankings: Sequence[RankingEntrada]) -> dict[str, float]:
    """Copeland score: pairwise wins − losses (a pairwise tie, or a pair nobody compared, counts 0)."""
    preparados = _preparar(rankings)
    items = _todos(preparados)
    matriz = matriz_preferencias(preparados)
    puntos = dict.fromkeys(items, 0.0)
    for a, b in combinations(items, 2):
        ab, ba = _n(matriz, a, b), _n(matriz, b, a)
        if ab > ba:
            puntos[a] += 1
            puntos[b] -= 1
        elif ba > ab:
            puntos[b] += 1
            puntos[a] -= 1
    return puntos


def puntuacion_kemeny(orden: Sequence[str], matriz: Mapping[tuple[str, str], float]) -> float:
    """How many reviewer preferences ``orden`` agrees with (Σ over pairs a before b of N[a, b])."""
    return sum(_n(matriz, orden[i], orden[j]) for i in range(len(orden)) for j in range(i + 1, len(orden)))


def distancia_kendall(a: Sequence[str], b: Sequence[str]) -> int:
    """Discordant pairs between two strict orders of the same items."""
    posicion = {item: i for i, item in enumerate(b)}
    return sum(1 for i in range(len(a)) for j in range(i + 1, len(a)) if posicion[a[i]] > posicion[a[j]])


def _orden_borda(items: Sequence[str], puntos: Mapping[str, float], semilla: int | str | None) -> list[str]:
    return sorted(items, key=lambda x: (-round(puntos.get(x, 0.0), 12), clave_desempate(x, semilla)))


@dataclass(frozen=True)
class ResultadoKemeny:
    orden: list[str]
    puntuacion: float
    optimos: int  # how many orders reach the optimum
    empate_en_cabeza: bool  # optimal orders disagree on the winner


def kemeny(rankings: Sequence[RankingEntrada], *, semilla: int | str | None = None) -> ResultadoKemeny:
    """Exact Kemeny–Young consensus by brute force (≤ :data:`MAX_KEMENY_EXACTO` items; ValueError above)."""
    preparados = _preparar(rankings)
    items = _todos(preparados)
    if len(items) > MAX_KEMENY_EXACTO:
        raise ValueError(f"Kemeny exacto admite como mucho {MAX_KEMENY_EXACTO} candidatos, no {len(items)}")
    if not items:
        return ResultadoKemeny([], 0.0, 1, False)
    matriz = matriz_preferencias(preparados)
    mejor = -1.0
    optimos: list[tuple[str, ...]] = []
    for orden in permutations(items):
        puntos = puntuacion_kemeny(orden, matriz)
        if puntos > mejor + 1e-9:
            mejor, optimos = puntos, [orden]
        elif abs(puntos - mejor) <= 1e-9:
            optimos.append(orden)
    referencia = _orden_borda(items, borda(preparados), semilla)
    elegido = min(
        optimos,
        key=lambda o: (distancia_kendall(list(o), referencia), [clave_desempate(x, semilla) for x in o]),
    )
    return ResultadoKemeny(
        orden=list(elegido),
        puntuacion=mejor,
        optimos=len(optimos),
        empate_en_cabeza=len({o[0] for o in optimos}) > 1,
    )


def caminos_schulze(rankings: Sequence[RankingEntrada]) -> dict[tuple[str, str], float]:
    """Strength of the strongest (widest) path from ``a`` to ``b`` (Schulze), Floyd–Warshall in O(n³)."""
    preparados = _preparar(rankings)
    items = _todos(preparados)
    matriz = matriz_preferencias(preparados)
    p: dict[tuple[str, str], float] = {}
    for a in items:
        for b in items:
            if a != b:
                ab, ba = _n(matriz, a, b), _n(matriz, b, a)
                p[(a, b)] = ab if ab > ba else 0.0
    for k in items:
        for i in items:
            if i == k:
                continue
            for j in items:
                if j in (i, k):
                    continue
                p[(i, j)] = max(p[(i, j)], min(p[(i, k)], p[(k, j)]))
    return p


def schulze(rankings: Sequence[RankingEntrada], *, semilla: int | str | None = None) -> list[str]:
    """The Schulze order (Copeland, then Borda, then the tie-break key, between items Schulze leaves tied)."""
    preparados = _preparar(rankings)
    items = _todos(preparados)
    p = caminos_schulze(preparados)
    gana = {a: sum(1 for b in items if b != a and p[(a, b)] > p[(b, a)]) for a in items}
    cope = copeland(preparados)
    bord = borda(preparados)
    return sorted(
        items,
        key=lambda x: (-gana[x], -cope[x], -round(bord.get(x, 0.0), 12), clave_desempate(x, semilla)),
    )


# --------------------------------------------------------------------------------------------------------------
# Agreement (Kendall's W)
# --------------------------------------------------------------------------------------------------------------


def _t_empates(ranking: Niveles) -> int:
    return sum(len(g) ** 3 - len(g) for g in ranking)


def acuerdo_kendall(rankings: Sequence[RankingEntrada]) -> float | None:
    """Kendall's W in [0, 1] (see the module docstring); None with < 3 informative rankings or < 2 items."""
    preparados = [r for r in _preparar(rankings) if sum(len(g) for g in r) >= 2]
    if len(preparados) < MIN_RANKINGS_ACUERDO:
        return None
    items = _todos(preparados)
    n = len(items)
    if n < 2:
        return None
    rangos = [rangos_medios(r) for r in preparados]
    if all(len(r) == n for r in rangos):
        m = len(rangos)
        sumas = {j: sum(r[j] for r in rangos) for j in items}
        media = m * (n + 1) / 2
        s = sum((sumas[j] - media) ** 2 for j in items)
        denominador = m * m * (n**3 - n) - m * sum(_t_empates(r) for r in preparados)
        if denominador <= 0:
            return None
        return _acotar(12 * s / denominador)
    return _acuerdo_incompleto(preparados, rangos, items)


def _acotar(valor: float) -> float:
    return round(min(1.0, max(0.0, valor)), 6)


def _acuerdo_incompleto(
    preparados: Sequence[Niveles], rangos: Sequence[dict[str, float]], items: list[str]
) -> float | None:
    indice = {item: i for i, item in enumerate(items)}
    n = len(items)
    m = [0] * n
    esperado = [0.0] * n
    suma = [0.0] * n
    c = [[0] * n for _ in range(n)]
    for r in rangos:
        k = len(r)
        presentes = [indice[i] for i in r]
        for j in presentes:
            m[j] += 1
            esperado[j] += (k + 1) / 2
        for item, rango in r.items():
            suma[indice[item]] += rango
        for a in presentes:
            for b in presentes:
                if a != b:
                    c[a][b] += 1
    s = sum((suma[j] - esperado[j]) ** 2 for j in range(n))
    maximo = _s_maximo(n, m, esperado, c, preparados, items)
    if maximo <= 0:
        return None
    return _acotar(s / maximo)


def _s_maximo(
    n: int,
    m: Sequence[int],
    esperado: Sequence[float],
    c: Sequence[Sequence[int]],
    preparados: Sequence[Niveles],
    items: Sequence[str],
) -> float:
    """S of perfect strict agreement with the best consensus order (exact DP over subsets up to 12 items)."""

    def contribucion(j: int, antes: Sequence[int]) -> float:
        return (m[j] + sum(c[j][otro] for otro in antes) - esperado[j]) ** 2

    if n <= MAX_ITEMS_ACUERDO_EXACTO:
        mejor = [-1.0] * (1 << n)
        mejor[0] = 0.0
        for mascara in range(1, 1 << n):
            miembros = [i for i in range(n) if mascara >> i & 1]
            for j in miembros:
                previo = mejor[mascara & ~(1 << j)]
                if previo < 0:
                    continue
                antes = [i for i in miembros if i != j]
                valor = previo + contribucion(j, antes)
                if valor > mejor[mascara]:
                    mejor[mascara] = valor
        return mejor[(1 << n) - 1]
    orden = _orden_borda(items, borda(preparados), None)
    posiciones = [items.index(x) for x in orden]
    return sum(contribucion(j, posiciones[:i]) for i, j in enumerate(posiciones))


# --------------------------------------------------------------------------------------------------------------
# Outliers
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Atipico:
    """A reviewer far from the others' consensus (shown, never dropped)."""

    evaluador_id: str
    distancia: float  # share of comparable pairs where it contradicts the others' majority
    pares: int  # comparable pairs (pairs it ranked strictly on which the others have a strict majority)


def distancia_al_resto(evaluador_id: str, rankings: Mapping[str, RankingEntrada]) -> tuple[float, int] | None:
    """(share of contradicted pairs, comparable pairs) of one reviewer against the others' majority; None if none."""
    propio = niveles(rankings[evaluador_id])
    otros = [r for clave, r in rankings.items() if clave != evaluador_id]
    matriz = matriz_preferencias(otros)
    rangos = rangos_medios(propio)
    comparables = 0
    contrarios = 0
    for a, b in combinations(sorted(rangos), 2):
        if rangos[a] == rangos[b]:
            continue
        mejor, peor = (a, b) if rangos[a] < rangos[b] else (b, a)
        a_favor, en_contra = _n(matriz, mejor, peor), _n(matriz, peor, mejor)
        if a_favor == en_contra:
            continue
        comparables += 1
        if en_contra > a_favor:
            contrarios += 1
    if comparables == 0:
        return None
    return contrarios / comparables, comparables


def revisores_atipicos(
    rankings: Mapping[str, RankingEntrada],
    *,
    umbral: float = UMBRAL_ATIPICO,
    min_pares: int = MIN_PARES_ATIPICO,
) -> list[Atipico]:
    """Reviewers whose ranking contradicts the others' majority on more than ``umbral`` of the comparable pairs AND
    more than the typical reviewer does (above the median distance): when every reviewer is that far from the rest
    (a split council, or position-driven rankings) there is no consensus to be an outlier from, and nobody is flagged.
    """
    medidas: dict[str, tuple[float, int]] = {}
    for evaluador in sorted(rankings):
        medida = distancia_al_resto(evaluador, rankings)
        if medida is not None:
            medidas[evaluador] = medida
    if not medidas:
        return []
    distancias = sorted(d for d, _p in medidas.values())
    mitad = len(distancias) // 2
    mediana = distancias[mitad] if len(distancias) % 2 else (distancias[mitad - 1] + distancias[mitad]) / 2
    atipicos = [
        Atipico(evaluador, round(distancia, 6), pares)
        for evaluador, (distancia, pares) in medidas.items()
        if pares >= min_pares and distancia > umbral and distancia > mediana
    ]
    return sorted(atipicos, key=lambda a: (-a.distancia, a.evaluador_id))


# --------------------------------------------------------------------------------------------------------------
# The aggregation the council uses
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Agregacion:
    """Everything the aggregation found (for the chair, the ruling and the audit trail)."""

    orden: list[str]
    metodo: Metodo
    borda: dict[str, float]
    copeland: dict[str, float]
    acuerdo: float | None
    atipicos: list[Atipico] = field(default_factory=list)
    optimos: int = 1
    empate_en_cabeza: bool = False
    n_rankings: int = 0


def agregar_detallado(
    rankings: Sequence[RankingEntrada] | Mapping[str, RankingEntrada],
    *,
    semilla: int | str | None = None,
) -> Agregacion:
    """Aggregate ``rankings`` (a mapping reviewer → ranking also finds outlier reviewers)."""
    por_evaluador: Mapping[str, RankingEntrada] | None = rankings if isinstance(rankings, Mapping) else None
    lista: list[RankingEntrada] = list(rankings.values()) if isinstance(rankings, Mapping) else list(rankings)
    preparados = _preparar(lista)
    items = _todos(preparados)
    bord = borda(preparados)
    cope = copeland(preparados)
    acuerdo = acuerdo_kendall(preparados)
    atipicos = revisores_atipicos(por_evaluador) if por_evaluador is not None else []
    if len(items) <= 1:
        return Agregacion(items, "trivial", bord, cope, acuerdo, atipicos, 1, False, len(preparados))
    if len(items) <= MAX_KEMENY_EXACTO:
        resultado = kemeny(preparados, semilla=semilla)
        return Agregacion(
            resultado.orden,
            "kemeny",
            bord,
            cope,
            acuerdo,
            atipicos,
            resultado.optimos,
            resultado.empate_en_cabeza,
            len(preparados),
        )
    orden = schulze(preparados, semilla=semilla)
    p = caminos_schulze(preparados)
    empate = len(orden) > 1 and p[(orden[0], orden[1])] <= p[(orden[1], orden[0])]
    return Agregacion(orden, "schulze", bord, cope, acuerdo, atipicos, 1, empate, len(preparados))


def agregar(rankings: Sequence[RankingEntrada] | Mapping[str, RankingEntrada]) -> list[str]:
    """The consensus order of the items (best first): Kemeny–Young up to 6 items, Schulze above."""
    return agregar_detallado(rankings).orden


# --------------------------------------------------------------------------------------------------------------
# Votes: consensus label and the council's confidence
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Votacion:
    consenso: Consenso
    recomendacion: str  # the leading recommendation, or «depende» when the council is split
    cuota: float  # share of members behind the leading recommendation (0 with no votes)
    votos: dict[str, int]  # recommendation -> members
    lideres: list[str]  # the recommendation(s) with most votes (several = tie for the top)


def consenso_de(recomendaciones: Sequence[str], *, minimo: int = MIN_MIEMBROS) -> Votacion:
    """The consensus label of the members' final recommendations (deterministic; see the module docstring)."""
    votos: dict[str, int] = {}
    for r in recomendaciones:
        votos[r] = votos.get(r, 0) + 1
    total = len(recomendaciones)
    if not total:
        return Votacion("insuficiente", DEPENDE, 0.0, {}, [])
    maximo = max(votos.values())
    lideres = sorted(r for r, v in votos.items() if v == maximo)
    cuota = maximo / total
    if total < minimo:
        consenso: Consenso = "insuficiente"
    elif maximo == total:
        consenso = "unanime"
    elif len(lideres) == 1 and maximo * 2 > total:
        consenso = "mayoria"
    else:
        consenso = "dividido"
    recomendacion = lideres[0] if consenso in ("unanime", "mayoria") else DEPENDE
    return Votacion(consenso, recomendacion, cuota, dict(sorted(votos.items())), lideres)


def acuerdo_compuesto(cuota: float, acuerdo: float | None) -> float:
    """``A = p`` without W, ``(2p + W) / 3`` with it (both in [0, 1])."""
    p = min(1.0, max(0.0, cuota))
    if acuerdo is None:
        return p
    return (2 * p + min(1.0, max(0.0, acuerdo))) / 3


def confianza_del_consejo(confianzas: Sequence[float], cuota: float, acuerdo: float | None) -> float:
    """``c̄ × A`` (see the module docstring), rounded to 3 decimals; 0 with no confidences."""
    if not confianzas:
        return 0.0
    media = sum(min(1.0, max(0.0, c)) for c in confianzas) / len(confianzas)
    return round(media * acuerdo_compuesto(cuota, acuerdo), 3)


__all__ = [
    "DEPENDE",
    "MAX_ITEMS_ACUERDO_EXACTO",
    "MAX_KEMENY_EXACTO",
    "MIN_PARES_ATIPICO",
    "MIN_RANKINGS_ACUERDO",
    "UMBRAL_ATIPICO",
    "Agregacion",
    "Atipico",
    "ResultadoKemeny",
    "Votacion",
    "acuerdo_compuesto",
    "acuerdo_kendall",
    "agregar",
    "agregar_detallado",
    "borda",
    "caminos_schulze",
    "clave_desempate",
    "confianza_del_consejo",
    "consenso_de",
    "copeland",
    "distancia_al_resto",
    "distancia_kendall",
    "kemeny",
    "matriz_preferencias",
    "niveles",
    "puntuacion_kemeny",
    "rangos_medios",
    "revisores_atipicos",
    "schulze",
]
