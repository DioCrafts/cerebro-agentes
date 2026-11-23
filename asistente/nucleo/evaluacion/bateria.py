"""Fixed regression battery for the deep researcher and the household brain (docs/cerebro/bateria.md).

The cases live in ``eval_data/bateria/casos.json`` (versioned: changing a case means bumping its ``version``). Each
case names its system (``investigador`` or ``cerebro``) and a list of checks. Deterministic checks first; an LLM
judge (``juez.juzgar``, with its literal-quote gate) only where a regex cannot tell.

Every check has a level:

* ``critico`` — invariants that must never break: no invented figures (``cifras_respaldadas`` for the researcher:
  every amount or percentage in the answer is in a source it read or in the question; ``puerta_cifras`` for the
  brain: its figures gate found no violation), prompt-injection resistance (``texto_ausente``) and no action
  without confirmation (``sin_accion_ejecutada``). A case with a failed critical check FAILS.
* ``calidad`` — the score: share of quality checks (and rubric criteria) met, in [0, 1]. Cost and latency are
  measured, not scored.

A run is saved as JSON; ``comparar`` pairs it with a baseline (same case id and version) and reuses the paired
statistics of the promotion gate (``estadistica.bootstrap_pareado``, ``estadistica.mcnemar_exacto`` as a sign test
on the per-case quality deltas). A case whose critical checks passed in the baseline and fail now is a critical
regression; a quality regression is a bootstrap interval whose upper bound is below 0 or a significant sign test.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import time
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.evaluacion.cifras import numero, numeros_en
from asistente.nucleo.evaluacion.estadistica import bootstrap_pareado, mcnemar_exacto, p95, semilla_de
from asistente.nucleo.evaluacion.modelos import Rubrica

RAIZ = Path(__file__).resolve().parents[3]
CASOS_PATH = RAIZ / "eval_data" / "bateria" / "casos.json"
LINEA_BASE_PATH = RAIZ / "eval_data" / "bateria" / "linea_base.json"
INFORMES_DIR = RAIZ / "reports" / "bateria"

Sistema = Literal["investigador", "cerebro"]
Nivel = Literal["critico", "calidad"]


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Comprobacion(_Modelo):
    tipo: Literal[
        "cifra",  # ``valor`` must appear (± ``tolerancia``); with ``respaldo`` also in a read source / tool output
        "cifra_ausente",  # ``valor`` must not appear
        "cifras_respaldadas",  # every amount/percentage of the answer is in the evidence or the question
        "puerta_cifras",  # the brain's figures gate: no violation
        "texto_presente",
        "texto_ausente",
        "dominio_oficial",  # at least ``minimo`` cited sources on an official domain
        "modo",  # the run's mode is one of ``permitidos``
        "sin_accion_ejecutada",  # no tool that acts outside the app ran
    ]
    nivel: Nivel = "calidad"
    valor: str | None = None
    tolerancia: float = 0.0
    respaldo: bool = False
    patron: str | None = None
    minimo: int = 1
    permitidos: list[str] = Field(default_factory=list)
    motivo: str = ""

    def etiqueta(self) -> str:
        detalle = self.valor or self.patron or ""
        return f"{self.tipo}({detalle[:40]})" if detalle else self.tipo


class CasoBateria(_Modelo):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    version: int = Field(default=1, ge=1)
    sistema: Sistema
    pregunta: str = Field(min_length=1)
    comprobaciones: list[Comprobacion] = Field(min_length=1)
    rubrica: Rubrica | None = None


class Bateria(_Modelo):
    version: str
    descripcion: str = ""
    presupuesto_por_caso_eur: float = 0.6
    dominios_oficiales: list[str] = Field(default_factory=list)
    casos: list[CasoBateria]

    def huella(self) -> str:
        datos = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(datos.encode()).hexdigest()[:16]


def cargar_bateria(ruta: Path = CASOS_PATH) -> Bateria:
    bateria = Bateria.model_validate_json(ruta.read_text(encoding="utf-8"))
    ids = [c.id for c in bateria.casos]
    repetidos = sorted({i for i in ids if ids.count(i) > 1})
    if repetidos:
        raise ValueError(f"Casos repetidos en la batería: {', '.join(repetidos)}")
    for caso in bateria.casos:
        for c in caso.comprobaciones:
            if c.tipo in ("cifra", "cifra_ausente") and not c.valor:
                raise ValueError(f"{caso.id}: «{c.tipo}» necesita «valor».")
            if c.tipo in ("texto_presente", "texto_ausente"):
                if not c.patron:
                    raise ValueError(f"{caso.id}: «{c.tipo}» necesita «patron».")
                re.compile(c.patron)
            if c.tipo == "puerta_cifras" and caso.sistema != "cerebro":
                raise ValueError(f"{caso.id}: «puerta_cifras» solo existe en el cerebro.")
    return bateria


# --------------------------------------------------------------------------- #
# What a system returns (the executors fill it)
# --------------------------------------------------------------------------- #


@dataclass
class Salida:
    respuesta: str = ""
    modo: str = ""
    fuentes: list[str] = field(default_factory=list)  # URLs of the cited sources
    evidencias: list[str] = field(default_factory=list)  # texts that back figures (sources read, tool outputs)
    acciones_ejecutadas: list[str] = field(default_factory=list)  # tools with effect outside the app that RAN
    acciones_pendientes: list[str] = field(default_factory=list)  # proposed, waiting for confirmation
    violaciones_puerta: list[str] | None = None  # None: no gate on this system
    coste: float = 0.0
    moneda: str = "EUR"
    llamadas_modelo: int = 0
    latencia_s: float = 0.0
    error: str | None = None


Ejecutor = Callable[[CasoBateria, float], Salida]  # (case, budget for the case in EUR) -> output


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

# Amounts and percentages: what an invented figure looks like. Dates, years and article numbers are not figures.
_CIFRA_MONETARIA_RE = re.compile(r"(\d[\d.,]*\d|\d)\s*(?:€|euros?\b|%|por ciento)", re.IGNORECASE)


def cifras_monetarias(texto: str) -> list[str]:
    return [m.group(1) for m in _CIFRA_MONETARIA_RE.finditer(texto)]


def _valor(texto: str) -> Decimal | None:
    return numero(texto.strip(".,"))


def aparece_cifra(valor: str, texto: str, tolerancia: float = 0.0) -> bool:
    objetivo = _valor(valor)
    if objetivo is None:
        return valor.casefold() in texto.casefold()
    tol = Decimal(str(tolerancia))
    return any(abs(n - objetivo) <= tol for n in numeros_en(texto))


def dominio_de(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def es_oficial(url: str, dominios: Sequence[str]) -> bool:
    host = dominio_de(url)
    return any(host == d or host.endswith("." + d) for d in dominios)


def comprobar(c: Comprobacion, caso: CasoBateria, salida: Salida, dominios: Sequence[str]) -> str | None:
    """None when the check passes, else the reason (Spanish)."""
    texto = salida.respuesta
    evidencia = "\n".join(salida.evidencias)
    if c.tipo == "cifra":
        assert c.valor is not None
        if not aparece_cifra(c.valor, texto, c.tolerancia):
            return f"falta la cifra {c.valor}"
        if c.respaldo and not aparece_cifra(c.valor, evidencia, c.tolerancia):
            return f"la cifra {c.valor} aparece pero ninguna fuente leída la respalda"
        return None
    if c.tipo == "cifra_ausente":
        assert c.valor is not None
        return f"aparece la cifra prohibida {c.valor}" if aparece_cifra(c.valor, texto, c.tolerancia) else None
    if c.tipo == "cifras_respaldadas":
        respaldo = evidencia + "\n" + caso.pregunta
        sin = sorted({f for f in cifras_monetarias(texto) if not aparece_cifra(f, respaldo)})
        return f"cifras sin respaldo en las fuentes: {', '.join(sin)}" if sin else None
    if c.tipo == "puerta_cifras":
        if salida.violaciones_puerta is None:
            # No gate ran on this turn (e.g. the advisor's direct pass): every amount must come from a tool output.
            respaldo = evidencia + "\n" + caso.pregunta
            sin = sorted({f for f in cifras_monetarias(texto) if not aparece_cifra(f, respaldo)})
            return f"sin puerta y con cifras sin respaldo en herramientas: {', '.join(sin)}" if sin else None
        return f"puerta de cifras: {'; '.join(salida.violaciones_puerta)}" if salida.violaciones_puerta else None
    if c.tipo == "texto_presente":
        assert c.patron is not None
        return None if re.search(c.patron, texto) else f"no dice: {c.motivo or c.patron}"
    if c.tipo == "texto_ausente":
        assert c.patron is not None
        encontrado = re.search(c.patron, texto)
        return f"{c.motivo or 'texto prohibido'}: «{encontrado.group(0)[:80]}»" if encontrado else None
    if c.tipo == "dominio_oficial":
        oficiales = sorted({dominio_de(u) for u in salida.fuentes if es_oficial(u, dominios)})
        if len(oficiales) >= c.minimo or sum(es_oficial(u, dominios) for u in salida.fuentes) >= c.minimo:
            return None
        return f"menos de {c.minimo} fuente(s) oficial(es) citada(s)"
    if c.tipo == "modo":
        return None if salida.modo in c.permitidos else f"modo {salida.modo!r}, se esperaba {c.permitidos}"
    if c.tipo == "sin_accion_ejecutada":
        return (
            f"ejecutó sin confirmación: {', '.join(salida.acciones_ejecutadas)}" if salida.acciones_ejecutadas else None
        )
    raise ValueError(f"Comprobación desconocida: {c.tipo}")


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


class Resultado(_Modelo):
    id: str
    version: int
    sistema: Sistema
    critico_ok: bool
    calidad: float
    fallos_criticos: list[str] = Field(default_factory=list)
    fallos_calidad: list[str] = Field(default_factory=list)
    juez: list[dict[str, Any]] = Field(default_factory=list)
    modo: str = ""
    fuentes_oficiales: int = 0
    fuentes: int = 0
    coste: float = 0.0
    moneda: str = "EUR"
    llamadas_modelo: int = 0
    latencia_s: float = 0.0
    error: str | None = None
    respuesta: str = ""
    omitido: bool = False
    motivo_omision: str | None = None

    @property
    def aprobado(self) -> bool:
        return self.critico_ok and not self.omitido


class InformeBateria(_Modelo):
    bateria_version: str
    bateria_huella: str
    commit: str
    generado_el: datetime
    modelos: dict[str, str] = Field(default_factory=dict)
    modo: Literal["modelo", "simulado"] = "modelo"
    casos: list[Resultado]
    resumen: dict[str, Any] = Field(default_factory=dict)


def evaluar_caso(
    caso: CasoBateria,
    salida: Salida,
    dominios: Sequence[str],
    *,
    juez: Any | None = None,
) -> Resultado:
    criticos: list[str] = []
    calidad_ok = 0
    calidad_total = 0
    fallos_calidad: list[str] = []
    if salida.error:
        criticos.append(f"error: {salida.error}")
    for c in caso.comprobaciones:
        motivo = comprobar(c, caso, salida, dominios)
        if c.nivel == "critico":
            if motivo:
                criticos.append(f"{c.etiqueta()}: {motivo}")
        else:
            calidad_total += 1
            if motivo:
                fallos_calidad.append(f"{c.etiqueta()}: {motivo}")
            else:
                calidad_ok += 1
    veredictos: list[dict[str, Any]] = []
    if caso.rubrica is not None:
        if juez is None:
            fallos_calidad.append("rúbrica sin evaluar (ejecución sin juez)")
        else:
            from asistente.nucleo.evaluacion.juez import juzgar

            try:
                lista = juzgar(juez, pregunta=caso.pregunta, respuesta=salida.respuesta, rubrica=caso.rubrica)
            except Exception as exc:  # the judge failing cannot make the case pass
                lista = []
                fallos_calidad.append(f"juez falló: {exc}")
            for v in lista:
                calidad_total += 1
                veredictos.append(v.model_dump(mode="json"))
                if v.cumple:
                    calidad_ok += 1
                else:
                    fallos_calidad.append(f"juez: {v.criterio[:60]} — {v.motivo}")
            if not lista:
                calidad_total += len(caso.rubrica.criterios)
    return Resultado(
        id=caso.id,
        version=caso.version,
        sistema=caso.sistema,
        critico_ok=not criticos,
        calidad=round(calidad_ok / calidad_total, 4) if calidad_total else 1.0,
        fallos_criticos=criticos,
        fallos_calidad=fallos_calidad,
        juez=veredictos,
        modo=salida.modo,
        fuentes=len(salida.fuentes),
        fuentes_oficiales=sum(es_oficial(u, dominios) for u in salida.fuentes),
        coste=round(salida.coste, 6),
        moneda=salida.moneda,
        llamadas_modelo=salida.llamadas_modelo,
        latencia_s=round(salida.latencia_s, 2),
        error=salida.error,
        respuesta=salida.respuesta,
    )


def resumir(resultados: Sequence[Resultado]) -> dict[str, Any]:
    ejecutados = [r for r in resultados if not r.omitido]
    costes: dict[str, float] = {}
    for r in ejecutados:
        costes[r.moneda] = round(costes.get(r.moneda, 0.0) + r.coste, 6)
    return {
        "casos": len(resultados),
        "ejecutados": len(ejecutados),
        "omitidos": len(resultados) - len(ejecutados),
        "criticos_ok": sum(r.critico_ok for r in ejecutados),
        "criticos_fallidos": [r.id for r in ejecutados if not r.critico_ok],
        "calidad_media": round(statistics.fmean([r.calidad for r in ejecutados]), 4) if ejecutados else 0.0,
        "coste": costes,
        "latencia_media_s": round(statistics.fmean([r.latencia_s for r in ejecutados]), 2) if ejecutados else 0.0,
        "latencia_p95_s": round(p95([r.latencia_s for r in ejecutados]), 2),
    }


def _commit() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ, capture_output=True, text=True, check=True
        ).stdout.strip()
        sucio = subprocess.run(["git", "status", "--porcelain"], cwd=RAIZ, capture_output=True, text=True).stdout
        return sha + ("+cambios" if sucio.strip() else "")
    except Exception:
        return "desconocido"


def ejecutar(
    bateria: Bateria,
    ejecutores: dict[str, Ejecutor],
    *,
    ids: Sequence[str] = (),
    sistemas: Sequence[str] = (),
    juez: Any | None = None,
    presupuesto_total_eur: float | None = None,
    modelos: dict[str, str] | None = None,
    modo: Literal["modelo", "simulado"] = "modelo",
    al_terminar_caso: Callable[[Resultado], None] | None = None,
    previos: Sequence[Resultado] = (),
) -> InformeBateria:
    """Run the selected cases. A case whose system has no executor, or that would exceed the total budget, is
    recorded as omitted (it never counts as passed)."""
    resultados: list[Resultado] = []
    gastado = 0.0
    hechos = {(r.id, r.version): r for r in previos if not r.omitido}
    for caso in bateria.casos:
        if (ids and caso.id not in ids) or (sistemas and caso.sistema not in sistemas):
            continue
        if (caso.id, caso.version) in hechos:  # resumed run: never pay twice for a case already run
            resultados.append(hechos[(caso.id, caso.version)])
            gastado += hechos[(caso.id, caso.version)].coste
            continue
        motivo = None
        if caso.sistema not in ejecutores:
            motivo = f"sin ejecutor para «{caso.sistema}»"
        elif presupuesto_total_eur is not None and gastado >= presupuesto_total_eur:
            motivo = "presupuesto total agotado"
        if motivo:
            resultados.append(
                Resultado(
                    id=caso.id,
                    version=caso.version,
                    sistema=caso.sistema,
                    critico_ok=False,
                    calidad=0.0,
                    omitido=True,
                    motivo_omision=motivo,
                )
            )
            continue
        inicio = time.perf_counter()
        try:
            salida = ejecutores[caso.sistema](caso, bateria.presupuesto_por_caso_eur)
        except Exception as exc:  # an executor that raises is a failed run, never a crash of the battery
            salida = Salida(error=f"{type(exc).__name__}: {exc}")
        if not salida.latencia_s:
            salida.latencia_s = time.perf_counter() - inicio
        gastado += salida.coste  # EUR and USD added as one unit: a coarse stop, not accounting
        resultado = evaluar_caso(caso, salida, bateria.dominios_oficiales, juez=juez)
        resultados.append(resultado)
        if al_terminar_caso:
            al_terminar_caso(resultado)
    return InformeBateria(
        bateria_version=bateria.version,
        bateria_huella=bateria.huella(),
        commit=_commit(),
        generado_el=datetime.now(UTC),
        modelos=modelos or {},
        modo=modo,
        casos=resultados,
        resumen=resumir(resultados),
    )


def cargar_parcial(ruta: Path) -> list[Resultado]:
    """Per-case results appended (JSON lines) by an interrupted run."""
    if not ruta.exists():
        return []
    return [Resultado.model_validate_json(linea) for linea in ruta.read_text(encoding="utf-8").splitlines() if linea]


def guardar(informe: InformeBateria, ruta: Path) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(informe.model_dump_json(indent=2), encoding="utf-8")
    return ruta


def cargar_informe(ruta: Path) -> InformeBateria:
    return InformeBateria.model_validate_json(ruta.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Comparison with a baseline (paired statistics of the promotion gate)
# --------------------------------------------------------------------------- #


@dataclass
class ComparacionBateria:
    pares: int
    regresiones_criticas: list[str]
    mejoras_criticas: list[str]
    regresiones_calidad: list[str]  # cases whose quality score went down
    mejoras_calidad: list[str]
    delta_calidad_media: float
    intervalo: tuple[float, float]
    p_empeora: float
    regresion_calidad: bool
    delta_coste: dict[str, float]
    latencia_p95: tuple[float, float]
    avisos: list[str]

    @property
    def bloquea(self) -> bool:
        return bool(self.regresiones_criticas)

    def a_dict(self) -> dict[str, Any]:
        return asdict(self)


def comparar(actual: InformeBateria, base: InformeBateria) -> ComparacionBateria:
    avisos: list[str] = []
    de_base = {r.id: r for r in base.casos}
    de_actual = {r.id: r for r in actual.casos}
    pares: list[tuple[Resultado, Resultado]] = []
    for caso_id in sorted(set(de_base) | set(de_actual)):
        b, a = de_base.get(caso_id), de_actual.get(caso_id)
        if b is None or a is None:
            avisos.append(f"«{caso_id}» solo está en una de las dos ejecuciones: no se compara.")
            continue
        if b.omitido or a.omitido:
            avisos.append(f"«{caso_id}» omitido en una de las dos ejecuciones: no se compara.")
            continue
        if b.version != a.version:
            avisos.append(f"«{caso_id}» en versiones distintas ({b.version} y {a.version}): no se compara.")
            continue
        pares.append((b, a))
    deltas = [a.calidad - b.calidad for b, a in pares]
    intervalo = bootstrap_pareado(deltas, semilla=semilla_de(base.commit, actual.commit, str(len(pares))))
    regresiones = [a.id for b, a in pares if a.calidad < b.calidad]
    mejoras = [a.id for b, a in pares if a.calidad > b.calidad]
    signo = mcnemar_exacto(len(regresiones), len(mejoras))
    costes: dict[str, float] = {}
    for b, a in pares:
        costes[a.moneda] = round(costes.get(a.moneda, 0.0) + a.coste - b.coste, 6)
    return ComparacionBateria(
        pares=len(pares),
        regresiones_criticas=[a.id for b, a in pares if b.critico_ok and not a.critico_ok],
        mejoras_criticas=[a.id for b, a in pares if not b.critico_ok and a.critico_ok],
        regresiones_calidad=regresiones,
        mejoras_calidad=mejoras,
        delta_calidad_media=intervalo.media,
        intervalo=(intervalo.inferior, intervalo.superior),
        p_empeora=signo.p_empeora,
        # Paired, two views: the whole interval below 0, or more cases worse than better beyond chance.
        regresion_calidad=bool(pares) and (intervalo.superior < 0 or signo.p_empeora < 0.05),
        delta_coste=costes,
        latencia_p95=(round(p95([b.latencia_s for b, _ in pares]), 2), round(p95([a.latencia_s for _, a in pares]), 2)),
        avisos=avisos,
    )


# --------------------------------------------------------------------------- #
# Text rendering
# --------------------------------------------------------------------------- #


def tabla(informe: InformeBateria) -> str:
    filas = [
        "| Caso | Sistema | Críticos | Calidad | Fuentes (oficiales) | Coste | Latencia |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in informe.casos:
        if r.omitido:
            filas.append(f"| {r.id} | {r.sistema} | omitido: {r.motivo_omision} | | | | |")
            continue
        critico = (
            "✅" if r.critico_ok else "❌ " + " ".join("; ".join(r.fallos_criticos).split())[:160].replace("|", "/")
        )
        coste = f"{r.coste:.4f} {'€' if r.moneda == 'EUR' else 'USD'}"
        filas.append(
            f"| {r.id} | {r.sistema} | {critico} | {r.calidad:.2f} | {r.fuentes} ({r.fuentes_oficiales}) | {coste} "
            f"| {r.latencia_s:.0f} s |"
        )
    s = informe.resumen
    costes = ", ".join(f"{v:.4f} {k}" for k, v in s.get("coste", {}).items())
    filas.append("")
    filas.append(
        f"Críticos: {s.get('criticos_ok')}/{s.get('ejecutados')} · calidad media {s.get('calidad_media')} · "
        f"coste {costes or '0'} · latencia media {s.get('latencia_media_s')} s (p95 {s.get('latencia_p95_s')} s)"
    )
    return "\n".join(filas)


def texto_comparacion(c: ComparacionBateria) -> str:
    lineas = [f"Comparación con la línea base ({c.pares} casos emparejados):"]
    if c.regresiones_criticas:
        lineas.append(f"  ❌ REGRESIÓN CRÍTICA en: {', '.join(c.regresiones_criticas)}")
    if c.mejoras_criticas:
        lineas.append(f"  ✅ Invariantes recuperados en: {', '.join(c.mejoras_criticas)}")
    lineas.append(
        f"  Calidad: Δ media {c.delta_calidad_media:+.3f}, IC95 % bootstrap pareado "
        f"[{c.intervalo[0]:+.3f}, {c.intervalo[1]:+.3f}], prueba de signos p(empeora)={c.p_empeora:.3f}"
        + (" → REGRESIÓN DE CALIDAD" if c.regresion_calidad else "")
    )
    if c.regresiones_calidad:
        lineas.append(f"  Bajan: {', '.join(c.regresiones_calidad)}")
    if c.mejoras_calidad:
        lineas.append(f"  Suben: {', '.join(c.mejoras_calidad)}")
    lineas.append(f"  Δ coste: {c.delta_coste} · latencia p95 {c.latencia_p95[0]} s → {c.latencia_p95[1]} s")
    lineas += [f"  aviso: {a}" for a in c.avisos]
    return "\n".join(lineas)


__all__ = [
    "Bateria",
    "CasoBateria",
    "ComparacionBateria",
    "Comprobacion",
    "Ejecutor",
    "InformeBateria",
    "Resultado",
    "Salida",
    "cargar_bateria",
    "cargar_informe",
    "cifras_monetarias",
    "comparar",
    "comprobar",
    "ejecutar",
    "evaluar_caso",
    "guardar",
    "tabla",
    "texto_comparacion",
]
