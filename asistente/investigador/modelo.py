"""Data of a deep research: budget, plan, sources, findings, critiques, claims, report and progress events."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

Veredicto = Literal["sostenido", "dudoso", "refutado"]
Estado = Literal["verificada", "sin_fuente"]


@dataclass(frozen=True)
class Presupuesto:
    """Caps for one research. ``max_euros`` None = no money cap (tokens still cap)."""

    max_tokens: int = 200_000
    # A full research costs 1,5–2,5 € once the web searcher's calls are counted (see Contador.registrar_gasto).
    max_euros: float | None = 2.5
    max_profundidad: int = 2  # rounds of plan → search → critique (1 = no follow-up round)
    max_subpreguntas: int = 5
    max_fuentes_por_subpregunta: int = 3
    # Room for a gap-filling round after a full first one (5 subquestions × 3 sources).
    max_lecturas: int = 25
    paralelismo: int = 4
    # Price used when the provider reports no cost (estimated tokens × price).
    euros_por_millon_tokens: float = 5.0


@dataclass(frozen=True)
class Subpregunta:
    id: str
    texto: str
    consultas: tuple[str, ...]
    profundidad: int = 1


@dataclass
class Fuente:
    """A page actually read. ``texto`` is the full raw text (quotes are checked against it, never rewritten)."""

    id: str
    url: str
    titulo: str
    texto: str
    subpregunta: str
    sospechosa: bool = False  # carried prompt-injection bait (neutralised before any model read it)
    leida_el: datetime | None = None


@dataclass
class Hallazgo:
    id: str
    subpregunta: str
    texto: str
    fuente: str
    cita: str  # literal quote from ``fuente``


@dataclass
class Critica:
    hallazgo: str
    veredicto: Veredicto
    motivo: str


@dataclass
class Afirmacion:
    texto: str
    fuentes: list[str]
    citas: list[str]
    estado: Estado = "verificada"
    nota: str | None = None


@dataclass
class Coste:
    tokens: int = 0
    euros: float = 0.0
    llamadas: int = 0
    lecturas: int = 0


@dataclass
class Informe:
    pregunta: str
    resumen: str
    afirmaciones: list[Afirmacion]
    fuentes: list[Fuente]
    plan: list[Subpregunta]
    hallazgos: list[Hallazgo]
    criticas: list[Critica]
    eliminadas: list[Afirmacion] = field(default_factory=list)
    coste: Coste = field(default_factory=Coste)
    profundidad_alcanzada: int = 0
    presupuesto_agotado: bool = False
    # Which cap stopped the research («tokens (…)», «euros (…)»), when one did.
    motivo_presupuesto: str | None = None
    avisos: list[str] = field(default_factory=list)

    def fuentes_citadas(self) -> list[Fuente]:
        usadas = {f for a in self.afirmaciones for f in a.fuentes}
        return [f for f in self.fuentes if f.id in usadas]

    def a_dict(self) -> dict[str, Any]:
        datos = asdict(self)
        for f in datos["fuentes"]:
            f.pop("texto", None)
            if f.get("leida_el"):
                f["leida_el"] = f["leida_el"].isoformat()
        return datos

    def markdown(self) -> str:
        lineas = [f"# {self.pregunta}", "", self.resumen, ""]
        for a in self.afirmaciones:
            refs = "".join(f"[{f}]" for f in a.fuentes)
            marca = " ⚠️ (sin fuente verificada)" if a.estado == "sin_fuente" else ""
            nota = f" — {a.nota}" if a.nota else ""
            lineas.append(f"- {a.texto} {refs}{marca}{nota}".rstrip())
        citadas = self.fuentes_citadas()
        if citadas:
            lineas += ["", "## Fuentes", ""]
            lineas += [f"- [{f.id}] {f.titulo} — {f.url}" for f in citadas]
        if self.avisos:
            lineas += ["", "## Avisos", ""] + [f"- {a}" for a in self.avisos]
        c = self.coste
        lineas += ["", f"_Coste: {c.tokens} tokens, {c.euros:.4f} €, {c.llamadas} llamadas, {c.lecturas} lecturas._"]
        return "\n".join(lineas)


TipoEvento = Literal[
    "plan", "busqueda", "fuente", "hallazgo", "critica", "afirmacion_eliminada", "presupuesto", "aviso", "fin"
]


@dataclass(frozen=True)
class Evento:
    """Observable progress: what the army is doing, as it happens."""

    tipo: TipoEvento
    mensaje: str
    datos: dict[str, Any] = field(default_factory=dict)
