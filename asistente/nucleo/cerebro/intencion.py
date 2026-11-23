"""Understanding: what the user asked, as a validated plan (``contrato.Intencion``).

Hybrid, cheapest first:

1. **Rules** (:func:`senales`): deterministic, explainable signals over the normalised message —

   * each ACTIVE specialist's vocabulary: a curated Spanish table per key (``data/vocabulario.json``: strong terms
     weigh 3–5, weak ones 1) plus terms derived from the module's own name, description and fact labels (a word
     that several modules use says nothing and is dropped). Terms are matched longest first and a matched span is
     consumed, so «renta mínima» counts for the benefits specialist and not also as «renta» for the tax one;
   * the module's facts in the case (a small prior that only breaks ties);
   * references to the past («lo que te dije», «la otra vez») → ``necesita_memoria``; a question ABOUT the past
     («¿qué te dije de…?») → ``memoria``;
   * greetings, thanks and «¿qué puedes hacer?» → ``charla``;
   * tasks and reminders anywhere, or an imperative at the start («prepara…», «da de baja…») → ``accion`` (a
     question like «¿cuándo se presenta…?» is not an order);
   * a short follow-up («¿y en 2024?», «sí, úsalo») with no signal of its own inherits the previous question's
     route, so an answer to the assistant's question reaches the specialist who asked it.

   The rules are CONFIDENT when one domain clearly dominates (top ≥ 3 and at least twice the runner-up), or the
   message is small talk, a memory question or a task: then no model is called (most turns; it keeps cost and
   latency low and the scripted tests stable). The confidence is a formula of the top score and the margin over the
   runner-up, so it can be explained term by term (:attr:`Senales.coincidencias`).

2. **The small model** (purpose ``cerebro:intencion``, prompt role ``cerebro_intencion``), ONE call, only when the
   rules are not confident: several strong domains (a cross-domain question that needs a plan with sub-questions and
   dependencies), a weak or no signal, an elliptical message with an ambiguous history. Its JSON is validated
   strictly (:func:`validar_del_modelo`): unknown or inactive specialists are dropped, ids renumbered, unknown
   dependencies removed, a cycle breaks every dependency, the type is made consistent with the encargos. Whatever is
   left must build a valid ``Intencion``; otherwise the rules' fallback plan is used (``origen="respaldo"``).

3. **Merge** (:func:`fusionar`): the model's plan, cross-checked with the rules (confidence lowered when they
   disagree). Below the brain's threshold the result is a clarification: ONE question to the user (``aclaracion``,
   no encargos) instead of a guess.

Nothing here raises on bad input or a failing model: understanding degrades to rules, then to a clarification.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from asistente.conocimiento.llm.security import neutralizar_inyeccion
from asistente.nucleo.cerebro.contrato import (
    CLAVE_FISCAL,
    MAX_ENCARGOS,
    Encargo,
    Intencion,
    TipoIntencion,
)
from asistente.nucleo.cerebro.prompts import INTENCION_SCHEMA, INTENCION_SYSTEM_PROMPT, prompt
from asistente.nucleo.memoria.modelo import normalizar_texto

if TYPE_CHECKING:  # pragma: no cover
    from asistente.nucleo.agent.providers.base import LLMProvider
    from asistente.nucleo.decision.schemas import CaseState

logger = logging.getLogger(__name__)

VOCABULARIO_PATH = Path(__file__).resolve().parent / "data" / "vocabulario.json"
UMBRAL_POR_DEFECTO = 0.45
# A domain is a candidate from this score; the rules are sure of ONE domain from UMBRAL_DOMINIO with this margin.
UMBRAL_CANDIDATO = 2.0
UMBRAL_DOMINIO = 3.0
MARGEN_DOMINIO = 2.0
# How many history messages back a follow-up may look for its route.
MAX_SEGUIMIENTO = 3
_MAX_PREGUNTA = 2000
_STOP = frozenset(
    "de la el los las del y o en con para por que se su sus tu tus un una al lo como mas sin hogar casa agente "
    "modulo modulos datos usuario calculo calcula avisa dice hace segun cada sobre entre otras otros tambien solo "
    "publicados oficial oficiales cuanto cual cuales este esta estos estas donde cuando ante tras desde hasta "
    "nunca siempre todo toda todos todas puede pueden propone guarda confirmas lee leer resume importante".split()
)


# --------------------------------------------------------------------------------------------------------------
# The specialists as the understanding step sees them
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FichaEspecialista:
    """What understanding knows of one ACTIVE specialist: its key, names and the texts its vocabulary derives from."""

    clave: str
    nombre: str
    descripcion: str
    etiquetas: tuple[str, ...] = ()  # labels of the module's own facts («Potencia contratada»…)
    hechos: tuple[str, ...] = ()  # names of the module's own facts (a prior when the case has them)

    @classmethod
    def de(cls, especialista: Any, modulo: Any | None = None) -> FichaEspecialista:
        """From a ``contrato.Especialista`` (and its module, when there is one)."""
        hechos = tuple(getattr(modulo, "hechos", ()) or ()) if modulo is not None else ()
        clave = str(especialista.clave)
        return cls(
            clave=clave,
            nombre=str(getattr(especialista, "nombre", clave)),
            descripcion=" ".join(str(getattr(especialista, "descripcion", "")).split()),
            etiquetas=tuple(str(getattr(h, "etiqueta", "")) for h in hechos if getattr(h, "etiqueta", "")),
            hechos=tuple(str(h.nombre) for h in hechos if str(getattr(h, "nombre", "")).startswith(f"{clave}_")),
        )


# --------------------------------------------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Termino:
    texto: str  # as written in the table («deduc*», «plan de pensiones»)
    patron: re.Pattern[str]
    peso: float
    longitud: int  # for longest-first matching


def plano(texto: str) -> str:
    """Lower case, no accents or punctuation, single spaces (the form every term is matched against)."""
    return normalizar_texto(texto or "")


def _patron(termino: str, *, inicio: bool = False) -> re.Pattern[str]:
    """A whole-word pattern; a word ending in ``*`` matches any word starting with it («deduc*» → «deducirme»)."""
    palabras = [
        re.escape(p.rstrip("*")) + (r"[a-z0-9ñ]*" if p.endswith("*") else "") for p in plano_termino(termino).split()
    ]
    antes = r"^" if inicio else r"(?<![a-z0-9ñ])"
    return re.compile(antes + r"\s+".join(palabras) + r"(?![a-z0-9ñ])")


def plano_termino(termino: str) -> str:
    """A table term normalised like the text, keeping its ``*`` stem marks."""
    return " ".join(plano(p) + ("*" if p.endswith("*") else "") for p in termino.split() if plano(p))


def _termino(entrada: str, peso_defecto: float, *, inicio: bool = False) -> Termino:
    texto, _, peso = entrada.partition("|")
    valor = float(peso) if peso else peso_defecto
    limpio = plano_termino(texto.strip())
    return Termino(texto=limpio, patron=_patron(limpio, inicio=inicio), peso=valor, longitud=len(limpio))


@dataclass(frozen=True)
class Vocabulario:
    """The curated table (``data/vocabulario.json``), compiled."""

    dominios: Mapping[str, tuple[Termino, ...]]
    nombres: Mapping[str, str]
    excluir: tuple[str, ...]
    charla_anclas: tuple[Termino, ...]
    charla_palabras: frozenset[str]
    capacidades: tuple[Termino, ...]
    pasado: tuple[Termino, ...]
    recordar: tuple[Termino, ...]
    tarea_en_memoria: tuple[Termino, ...]
    accion_tarea: tuple[Termino, ...]
    accion_imperativo: tuple[Termino, ...]  # anchored at the start of the message
    accion_prefijos: tuple[str, ...]
    seguimiento_inicios: tuple[str, ...]
    confirmaciones: frozenset[str]
    dependencias: tuple[tuple[str, str, tuple[Termino, ...]], ...]
    pesos: Mapping[str, float]


@lru_cache(maxsize=1)
def vocabulario(ruta: str | None = None) -> Vocabulario:
    """The compiled vocabulary (cached). A broken file leaves the rules with derived vocabulary only."""
    try:
        datos = json.loads(Path(ruta or VOCABULARIO_PATH).read_text(encoding="utf-8"))
    except OSError, ValueError:
        logger.exception("brain vocabulary unreadable: rules fall back to the modules' own words")
        datos = {}
    pesos = {"fuerte": 3.0, "debil": 1.0, "derivado": 1.0, "derivado_max": 2.0, "previo_hechos": 0.5}
    pesos.update({k: float(v) for k, v in (datos.get("pesos") or {}).items()})
    dominios: dict[str, tuple[Termino, ...]] = {}
    nombres: dict[str, str] = {}
    for clave, entrada in (datos.get("dominios") or {}).items():
        terminos = [_termino(t, pesos["fuerte"]) for t in entrada.get("fuertes", ())]
        terminos += [_termino(t, pesos["debil"]) for t in entrada.get("debiles", ())]
        dominios[clave] = tuple(terminos)
        nombres[clave] = str(entrada.get("nombre") or clave)
    charla = datos.get("charla") or {}
    memoria = datos.get("memoria") or {}
    accion = datos.get("accion") or {}
    seguimiento = datos.get("seguimiento") or {}
    return Vocabulario(
        dominios=dominios,
        nombres=nombres,
        excluir=tuple(plano(t) for t in datos.get("excluir", ())),
        charla_anclas=tuple(_termino(t, 1.0) for t in charla.get("anclas", ())),
        charla_palabras=frozenset(plano(" ".join(charla.get("palabras", ()))).split()),
        capacidades=tuple(_termino(t, 1.0) for t in charla.get("capacidades", ())),
        pasado=tuple(_termino(t, 1.0) for t in memoria.get("pasado", ())),
        recordar=tuple(_termino(t, 1.0) for t in memoria.get("recordar", ())),
        tarea_en_memoria=tuple(_termino(t, 1.0) for t in memoria.get("tarea", ())),
        accion_tarea=tuple(_termino(t, 1.0) for t in accion.get("tarea", ())),
        accion_imperativo=tuple(_termino(t, 1.0, inicio=True) for t in accion.get("imperativo", ())),
        accion_prefijos=tuple(plano(p) for p in accion.get("prefijos", ())),
        seguimiento_inicios=tuple(plano(p) for p in seguimiento.get("inicios", ())),
        confirmaciones=frozenset(plano(c) for c in seguimiento.get("confirmaciones", ())),
        dependencias=tuple(
            (str(d["especialista"]), str(d["espera_a"]), tuple(_termino(t, 1.0) for t in d.get("si", ())))
            for d in datos.get("dependencias", ())
            if isinstance(d, dict) and d.get("especialista") and d.get("espera_a")
        ),
        pesos=pesos,
    )


def _derivados(fichas: Sequence[FichaEspecialista], vocab: Vocabulario) -> dict[str, tuple[Termino, ...]]:
    """Terms from each module's own texts, for what the curated table does not cover.

    A module without a row in the table (a new one) gets its name's words as strong terms and its description's
    distinctive words as weak ones (a word several modules use is dropped: it tells them apart from nothing). Every
    module gets the multi-word labels of its own facts («potencia contratada p1 kw») as weak terms.
    """
    peso = vocab.pesos["derivado"]
    palabras_de: dict[str, set[str]] = {}
    for ficha in fichas:
        palabras_de[ficha.clave] = {p for p in plano(ficha.descripcion).split() if len(p) >= 5 and p not in _STOP}
    usos: dict[str, int] = {}
    for palabras in palabras_de.values():
        for palabra in palabras:
            usos[palabra] = usos.get(palabra, 0) + 1
    resultado: dict[str, tuple[Termino, ...]] = {}
    for ficha in fichas:
        terminos: dict[str, Termino] = {}
        if ficha.clave not in vocab.dominios:
            for palabra in plano(ficha.nombre).split():
                if len(palabra) >= 4 and palabra not in _STOP:
                    terminos[palabra] = _termino(palabra, vocab.pesos["fuerte"])
            for palabra in sorted(palabras_de[ficha.clave]):
                if usos[palabra] == 1:
                    terminos.setdefault(palabra, _termino(palabra, peso))
        for etiqueta in ficha.etiquetas:
            frase = plano(etiqueta)
            if len([p for p in frase.split() if p not in _STOP]) >= 2:
                terminos.setdefault(frase, _termino(frase, peso))
        resultado[ficha.clave] = tuple(terminos.values())
    return resultado


# --------------------------------------------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------------------------------------------


@dataclass
class Senales:
    """What the rules saw in one message (explainable: every score is the sum of the listed terms)."""

    puntuaciones: dict[str, float] = field(default_factory=dict)
    coincidencias: dict[str, list[str]] = field(default_factory=dict)
    # Strong signals of modules that are NOT active (the user may switch them on).
    inactivos: dict[str, float] = field(default_factory=dict)
    charla: bool = False
    capacidades: bool = False
    pasado: bool = False
    recordar: bool = False
    accion: str | None = None  # "tarea" | "imperativo"
    confirmacion: bool = False
    eliptica: bool = False

    def ranking(self) -> list[tuple[str, float]]:
        return sorted(self.puntuaciones.items(), key=lambda par: (-par[1], par[0]))

    def candidatos(self, umbral: float = UMBRAL_CANDIDATO) -> list[str]:
        return [clave for clave, valor in self.ranking() if valor >= umbral]

    def explicacion(self) -> str:
        partes = [f"{c}={v:g} ({', '.join(self.coincidencias.get(c, []))})" for c, v in self.ranking() if v > 0]
        marcas = [
            nombre
            for nombre, activo in (
                ("charla", self.charla),
                ("capacidades", self.capacidades),
                ("pasado", self.pasado),
                ("recordar", self.recordar),
                ("confirmacion", self.confirmacion),
                ("eliptica", self.eliptica),
            )
            if activo
        ]
        if self.accion:
            marcas.append(f"accion:{self.accion}")
        return "; ".join(partes + marcas) or "sin señales"


def _consumir(texto: str, terminos: Iterable[tuple[str, Termino, bool]]) -> list[tuple[str, Termino, bool]]:
    """Longest-first, non-overlapping matches (curated before derived on a tie): every (domain, term, curated)
    whose term matched a span no longer term had claimed. A term counts once."""
    ocupado = [False] * (len(texto) + 1)
    encontrados: list[tuple[str, Termino, bool]] = []
    for clave, termino, curado in sorted(terminos, key=lambda t: (-t[1].longitud, not t[2], t[0], t[1].texto)):
        for m in termino.patron.finditer(texto):
            if any(ocupado[m.start() : m.end()]):
                continue
            for i in range(m.start(), m.end()):
                ocupado[i] = True
            encontrados.append((clave, termino, curado))
            break
    return encontrados


def _alguno(texto: str, terminos: Iterable[Termino]) -> bool:
    return any(t.patron.search(texto) for t in terminos)


def _sin_prefijos(texto: str, prefijos: Sequence[str]) -> str:
    cambiado = True
    while cambiado:
        cambiado = False
        for prefijo in sorted(prefijos, key=len, reverse=True):
            if prefijo and (texto == prefijo or texto.startswith(prefijo + " ")):
                texto = texto[len(prefijo) :].strip()
                cambiado = True
    return texto


def senales(
    consulta: str,
    fichas: Sequence[FichaEspecialista],
    *,
    case: CaseState | None = None,
    vocab: Vocabulario | None = None,
) -> Senales:
    """The deterministic signals of ``consulta`` for these ACTIVE specialists."""
    vocab = vocab or vocabulario()
    texto = plano(consulta)
    # «¿En qué me puedes ayudar?» is read before the «help me» phrases are taken out of the domain matching.
    capacidades = _alguno(texto, vocab.capacidades)
    for frase in vocab.excluir:
        if frase:
            texto = re.sub(r"(?<![a-z0-9ñ])" + re.escape(frase) + r"(?![a-z0-9ñ])", " ", texto)
    texto = " ".join(texto.split())
    s = Senales()
    activos = {f.clave for f in fichas}
    derivados = _derivados(fichas, vocab)
    todos: list[tuple[str, Termino, bool]] = []
    for clave, terminos in vocab.dominios.items():
        todos += [(clave, t, True) for t in terminos]
    for clave, terminos in derivados.items():
        todos += [(clave, t, False) for t in terminos]
    # Weak derived terms add at most ``derivado_max`` per domain (a description is not a vocabulary).
    derivado_de: dict[str, float] = {}
    tope = vocab.pesos["derivado_max"]
    for clave, termino, curado in _consumir(texto, todos):
        peso = termino.peso
        if not curado and peso < vocab.pesos["fuerte"]:
            peso = min(peso, max(0.0, tope - derivado_de.get(clave, 0.0)))
            derivado_de[clave] = derivado_de.get(clave, 0.0) + peso
        if peso <= 0:
            continue
        if clave in activos:
            s.puntuaciones[clave] = s.puntuaciones.get(clave, 0.0) + peso
            s.coincidencias.setdefault(clave, []).append(termino.texto)
        else:
            s.inactivos[clave] = s.inactivos.get(clave, 0.0) + peso
    # The module's facts in the case: a small prior, only to break ties between real candidates.
    if case is not None:
        nombres = {f.name for f in getattr(case, "facts", ()) or ()}
        for ficha in fichas:
            if ficha.clave in s.puntuaciones and any(h in nombres for h in ficha.hechos):
                s.puntuaciones[ficha.clave] += vocab.pesos["previo_hechos"]
                s.coincidencias.setdefault(ficha.clave, []).append("(datos en el expediente)")
    palabras = texto.split()
    s.capacidades = capacidades
    s.charla = s.capacidades or (
        0 < len(palabras) <= 10
        and all(p in vocab.charla_palabras for p in palabras)
        and _alguno(texto, vocab.charla_anclas)
    )
    s.pasado = _alguno(texto, vocab.pasado)
    s.recordar = _alguno(texto, vocab.recordar) and not _alguno(texto, vocab.tarea_en_memoria)
    if _alguno(texto, vocab.accion_tarea):
        s.accion = "tarea"
    else:
        inicio = _sin_prefijos(texto, vocab.accion_prefijos)
        if _alguno(inicio, vocab.accion_imperativo):
            s.accion = "imperativo"
    s.confirmacion = bool(texto) and (
        texto in vocab.confirmaciones
        or (len(palabras) <= 6 and palabras[0] in vocab.confirmaciones and palabras[0] in {"si", "no", "vale", "ok"})
    )
    s.eliptica = len(palabras) <= 14 and any(
        texto == inicio.strip() or texto.startswith(inicio if inicio.endswith(" ") else inicio + " ")
        for inicio in vocab.seguimiento_inicios
    )
    return s


# --------------------------------------------------------------------------------------------------------------
# Rules → plan
# --------------------------------------------------------------------------------------------------------------


@dataclass
class Comprension:
    """The understanding of one turn: the plan, the signals behind it, and whether the model was asked."""

    intencion: Intencion
    senales: Senales
    modelo_consultado: bool = False
    motivo: str = ""
    uso: dict[str, int] = field(default_factory=dict)


def _confianza(top: float, segundo: float) -> float:
    """Explainable: strength of the top domain, discounted by how close the runner-up is."""
    fuerza = min(0.97, 0.55 + 0.08 * top)
    return round(max(0.0, fuerza * (1 - 0.5 * (segundo / top if top else 1.0))), 3)


def _historial_usuario(chat_history: Sequence[Mapping[str, Any]] | None) -> list[str]:
    salida: list[str] = []
    for mensaje in chat_history or ():
        if isinstance(mensaje, Mapping) and mensaje.get("role") == "user":
            salida.append(_texto(mensaje.get("content")))
    return [m for m in salida if m.strip()]


def _texto(contenido: Any) -> str:
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        return " ".join(str(b.get("text") or "") for b in contenido if isinstance(b, Mapping))
    return ""


def _ultimo_asistente(chat_history: Sequence[Mapping[str, Any]] | None) -> str:
    for mensaje in reversed(list(chat_history or ())):
        if isinstance(mensaje, Mapping) and mensaje.get("role") == "assistant":
            return _texto(mensaje.get("content"))
    return ""


def _pregunta(consulta: str, contexto: str | None = None) -> str:
    texto = consulta.strip()
    if contexto and contexto.strip() and contexto.strip() != texto:
        texto += f"\n\n(Contexto: antes, en esta conversación, el usuario preguntó: «{contexto.strip()[:600]}»)"
    return texto[:_MAX_PREGUNTA]


def _encargos(
    claves: Sequence[str],
    consulta: str,
    fichas: Mapping[str, FichaEspecialista],
    *,
    contexto: str | None = None,
    vocab: Vocabulario,
    motivo: Callable[[str], str] | None = None,
) -> list[Encargo]:
    """One encargo per specialist; with several, each sub-question names the part it covers and the curated
    dependencies (the tax specialist waits for the mortgage's yearly payments when deductions are at stake)."""
    ids = {clave: f"e{i}" for i, clave in enumerate(claves, start=1)}
    texto = plano(consulta)
    encargos: list[Encargo] = []
    for clave in claves:
        pregunta = _pregunta(consulta, contexto)
        if len(claves) > 1:
            otros = ", ".join(fichas[c].nombre for c in claves if c != clave)
            pregunta = (
                f"{pregunta}\n\nResponde a la parte de «{fichas[clave].nombre}»; de {otros} se ocupa otro especialista."
            )[:_MAX_PREGUNTA]
        depende = [
            ids[espera]
            for quien, espera, si in vocab.dependencias
            if quien == clave and espera in ids and (not si or _alguno(texto, si))
        ]
        encargos.append(
            Encargo(
                id=ids[clave],
                especialista=clave,
                pregunta=pregunta,
                depende_de=depende,
                motivo=(motivo(clave) if motivo else "")[:300],
            )
        )
    return encargos


def _resumen(tipo: TipoIntencion, claves: Sequence[str], fichas: Mapping[str, FichaEspecialista]) -> str:
    nombres = [fichas[c].nombre for c in claves if c in fichas]
    if tipo == "charla":
        return "Charla: respondo yo directamente"
    if tipo == "memoria":
        return "Repaso lo que me contaste en otras conversaciones"
    if tipo == "fuera_de_ambito":
        return "Fuera de lo que cubre el asistente del hogar"
    if not nombres:
        return "Lo gestiono yo directamente" if tipo == "accion" else ""
    if len(nombres) == 1:
        return f"Consulto a {nombres[0]}"
    return f"Consulto a {', '.join(nombres[:-1])} y {nombres[-1]} en paralelo"


@dataclass
class _Regla:
    """The rules' reading: a plan when they are sure, else what the model should consider."""

    intencion: Intencion | None
    candidatos: list[str]
    respaldo: Intencion
    motivo: str


def por_reglas(
    consulta: str,
    fichas: Sequence[FichaEspecialista],
    *,
    chat_history: Sequence[Mapping[str, Any]] | None = None,
    case: CaseState | None = None,
    vocab: Vocabulario | None = None,
    _profundidad: int = 0,
) -> tuple[Senales, _Regla]:
    """The rules alone: (signals, reading). ``reading.intencion`` is set only when the rules are confident."""
    vocab = vocab or vocabulario()
    por_clave = {f.clave: f for f in fichas}
    s = senales(consulta, fichas, case=case, vocab=vocab)
    ranking = s.ranking()
    top_clave, top = ranking[0] if ranking else ("", 0.0)
    segundo = ranking[1][1] if len(ranking) > 1 else 0.0
    candidatos = s.candidatos()
    fuertes = [c for c, v in ranking if v >= UMBRAL_DOMINIO]

    def intencion(tipo: TipoIntencion, claves: Sequence[str], confianza: float, **extra: Any) -> Intencion:
        contexto = extra.pop("contexto", None)
        return Intencion(
            tipo=tipo,
            encargos=_encargos(
                claves,
                consulta,
                por_clave,
                contexto=contexto,
                vocab=vocab,
                motivo=lambda c: f"Reglas: {', '.join(s.coincidencias.get(c, [])[:5]) or 'contexto'}",
            ),
            necesita_memoria=s.pasado or s.recordar,
            confianza=max(0.0, min(1.0, confianza)),
            origen="reglas",
            resumen=_resumen(tipo, claves, por_clave),
            **extra,
        )

    # Fallback when the model is not available or fails: every strong candidate (at most 3), or a clarification.
    respaldo_claves = (fuertes or candidatos)[:3]
    if respaldo_claves:
        tipo_respaldo: TipoIntencion = "accion" if s.accion else ("cruzada" if len(respaldo_claves) > 1 else "ambito")
        respaldo = intencion(tipo_respaldo, respaldo_claves, 0.5).model_copy(update={"origen": "respaldo"})
    else:
        respaldo = Intencion(
            tipo="ambito",
            confianza=0.2,
            origen="respaldo",
            necesita_memoria=s.pasado,
            aclaracion=_aclaracion([], por_clave),
        )

    if s.charla and top < UMBRAL_DOMINIO:
        return s, _Regla(intencion("charla", [], 0.95), [], respaldo, "saludo, agradecimiento o capacidades")
    if s.recordar:
        return s, _Regla(intencion("memoria", [], 0.85), candidatos, respaldo, "pregunta por lo dicho antes")
    if s.accion == "tarea":
        # Tasks and reminders are the brain's own tools; it consults a specialist itself if the task needs one.
        return s, _Regla(intencion("accion", [], 0.85), candidatos, respaldo, "tarea o recordatorio")

    # ONE domain: strong, and every other one weak and far behind (a second strong domain is a cross question).
    unico = top >= UMBRAL_DOMINIO and (
        segundo < UMBRAL_CANDIDATO or (segundo < UMBRAL_DOMINIO and top >= MARGEN_DOMINIO * segundo)
    )
    if unico:
        tipo: TipoIntencion = "accion" if s.accion else "ambito"
        confianza = _confianza(top, segundo)
        return s, _Regla(intencion(tipo, [top_clave], confianza), [top_clave], respaldo, "un ámbito claro")

    if not candidatos and s.inactivos and max(s.inactivos.values()) >= UMBRAL_DOMINIO:
        clave_off = max(s.inactivos.items(), key=lambda par: par[1])[0]
        nombre = vocab.nombres.get(clave_off, clave_off)
        fuera = Intencion(
            tipo="fuera_de_ambito",
            confianza=0.8,
            origen="reglas",
            resumen=f"Eso es del módulo «{nombre}», que no está activado",
        )
        return s, _Regla(fuera, [], respaldo, f"módulo apagado: {clave_off}")

    # A short follow-up with no signal of its own inherits the route of the previous question.
    if (not candidatos) and (s.eliptica or s.confirmacion) and _profundidad < MAX_SEGUIMIENTO:
        anteriores = _historial_usuario(chat_history)
        if anteriores:
            previo = anteriores[-1]
            historial_previo = _hasta_el_ultimo_usuario(chat_history)
            _s, lectura = por_reglas(
                previo, fichas, chat_history=historial_previo, case=case, vocab=vocab, _profundidad=_profundidad + 1
            )
            heredada = lectura.intencion
            if heredada is not None and heredada.tipo not in ("charla", "fuera_de_ambito"):
                claves = [e.especialista for e in heredada.encargos]
                nueva = intencion(
                    heredada.tipo,
                    claves,
                    heredada.confianza * 0.85,
                    contexto=previo,
                )
                nueva = nueva.model_copy(
                    update={"necesita_memoria": nueva.necesita_memoria or heredada.necesita_memoria}
                )
                return s, _Regla(nueva, claves, respaldo, "seguimiento de la pregunta anterior")
            if s.confirmacion and heredada is None:
                return s, _Regla(None, lectura.candidatos, lectura.respaldo, "respuesta a una pregunta ambigua")
    if not candidatos and s.confirmacion and _ultimo_asistente(chat_history).strip():
        # «Sí» to a question of the brain itself (a task it proposed, a decision): the brain answers.
        return s, _Regla(intencion("accion", [], 0.7), [], respaldo, "respuesta al asistente")
    motivo = "varios ámbitos" if len(fuertes) > 1 else ("señal débil" if candidatos else "sin señales")
    return s, _Regla(None, candidatos, respaldo, motivo)


def _hasta_el_ultimo_usuario(chat_history: Sequence[Mapping[str, Any]] | None) -> list[Mapping[str, Any]]:
    """The history before the last user message (the context that message was written in)."""
    historial = [m for m in chat_history or () if isinstance(m, Mapping)]
    for i in range(len(historial) - 1, -1, -1):
        if historial[i].get("role") == "user":
            return historial[:i]
    return []


def _aclaracion(candidatos: Sequence[str], fichas: Mapping[str, FichaEspecialista]) -> str:
    nombres = [fichas[c].nombre for c in candidatos if c in fichas]
    if len(nombres) >= 2:
        return f"¿Me lo concretas un poco? ¿Es sobre {', '.join(nombres[:-1])} o sobre {nombres[-1]}?"
    if len(nombres) == 1:
        return f"¿Me lo concretas un poco? ¿Te refieres a {nombres[0]}?"
    ambitos = [f.nombre for f in fichas.values()][:6]
    if ambitos:
        return (
            "¿Me lo concretas un poco? Puedo ayudarte con "
            + ", ".join(a.lower() if i else a for i, a in enumerate(ambitos[:-1]))
            + (f" y {ambitos[-1].lower()}" if len(ambitos) > 1 else "")
            + ": dime de qué se trata y qué necesitas saber."
        )
    return "¿Me lo concretas un poco? Dime de qué se trata y qué necesitas saber."


# --------------------------------------------------------------------------------------------------------------
# The model, validated
# --------------------------------------------------------------------------------------------------------------


def _mensaje_modelo(
    consulta: str,
    fichas: Sequence[FichaEspecialista],
    s: Senales,
    chat_history: Sequence[Mapping[str, Any]] | None,
) -> str:
    lineas = ["<especialistas_activos>"]
    lineas += [f"- {f.clave} — {f.nombre}: {f.descripcion[:400]}" for f in fichas]
    lineas.append("</especialistas_activos>")
    lineas.append("<senales_de_las_reglas>")
    lineas.append(s.explicacion())
    lineas.append("</senales_de_las_reglas>")
    recientes = [m for m in chat_history or () if isinstance(m, Mapping)][-4:]
    if recientes:
        lineas.append("<conversacion_reciente>")
        for m in recientes:
            quien = "usuario" if m.get("role") == "user" else "asistente"
            lineas.append(f"{quien}: {neutralizar_inyeccion(_texto(m.get('content')))[:700]}")
        lineas.append("</conversacion_reciente>")
    lineas.append("<mensaje_del_usuario>")
    lineas.append(consulta[:4000])
    lineas.append("</mensaje_del_usuario>")
    return "\n".join(lineas)


def validar_del_modelo(datos: Any, activos: Sequence[str], *, consulta: str) -> tuple[Intencion | None, bool, str]:
    """The model's JSON as a valid ``Intencion``: (intencion or None, repaired?, why).

    Repairs what can be repaired without guessing: encargos for unknown or inactive specialists are dropped, ids
    renumbered in order, unknown dependencies removed, every dependency removed if they form a cycle, the type made
    consistent with the encargos, the confidence clamped. Anything else (not an object, an unknown type, an empty
    sub-question) makes the whole answer unusable.
    """
    if not isinstance(datos, dict):
        return None, False, "no es un objeto JSON"
    tipo = datos.get("tipo")
    if tipo not in ("charla", "memoria", "ambito", "cruzada", "accion", "fuera_de_ambito"):
        return None, False, f"tipo desconocido: {tipo!r}"
    reparado = False
    crudos = datos.get("encargos") or []
    if not isinstance(crudos, list):
        return None, False, "encargos no es una lista"
    activos_set = set(activos)
    viejos_a_nuevos: dict[str, str] = {}
    limpios: list[dict[str, Any]] = []
    for crudo in crudos:
        if not isinstance(crudo, dict):
            reparado = True
            continue
        clave = str(crudo.get("especialista") or "").strip()
        pregunta = str(crudo.get("pregunta") or "").strip()
        if clave not in activos_set:
            reparado = True
            continue
        if not pregunta:
            pregunta = consulta.strip()
            reparado = True
        if len(limpios) >= MAX_ENCARGOS:
            reparado = True
            break
        nuevo = f"e{len(limpios) + 1}"
        viejo = str(crudo.get("id") or nuevo)
        if viejo != nuevo:
            reparado = True
        viejos_a_nuevos.setdefault(viejo, nuevo)
        limpios.append(
            {
                "id": nuevo,
                "especialista": clave,
                "pregunta": pregunta[:_MAX_PREGUNTA],
                "depende_de": [str(d) for d in crudo.get("depende_de") or [] if isinstance(d, str | int)],
                "motivo": str(crudo.get("motivo") or "")[:300],
            }
        )
    for item in limpios:
        traducidas = [viejos_a_nuevos[d] for d in item["depende_de"] if d in viejos_a_nuevos]
        traducidas = [d for d in dict.fromkeys(traducidas) if d != item["id"]]
        if len(traducidas) != len(item["depende_de"]):
            reparado = True
        item["depende_de"] = traducidas
    if tipo in ("charla", "memoria", "fuera_de_ambito") and limpios:
        limpios, reparado = [], True
    aclaracion = str(datos.get("aclaracion") or "").strip()[:500] or None
    if aclaracion and limpios:
        limpios, reparado = [], True
    if tipo in ("ambito", "cruzada") and not limpios and not aclaracion:
        return None, reparado, "ningún encargo válido"
    if tipo == "ambito" and len({e["especialista"] for e in limpios}) > 1:
        tipo, reparado = "cruzada", True
    if tipo == "cruzada" and len({e["especialista"] for e in limpios}) == 1:
        tipo, reparado = "ambito", True
    try:
        confianza = float(datos.get("confianza"))  # type: ignore[arg-type]  # TypeError handled below
    except TypeError, ValueError:
        confianza, reparado = 0.5, True
    if not 0.0 <= confianza <= 1.0:
        confianza, reparado = min(1.0, max(0.0, confianza)), True
    base = {
        "tipo": tipo,
        "necesita_memoria": datos.get("necesita_memoria") is True,
        "confianza": confianza,
        "origen": "modelo",
        "resumen": str(datos.get("resumen") or "")[:300],
        "aclaracion": aclaracion,
    }
    try:
        return Intencion.model_validate({**base, "encargos": limpios}), reparado, ""
    except ValidationError:
        # The only plan error left is a dependency cycle: drop every dependency.
        for item in limpios:
            item["depende_de"] = []
        try:
            return Intencion.model_validate({**base, "encargos": limpios}), True, "ciclo de dependencias"
        except ValidationError as exc:
            return None, True, f"plan no válido: {exc.errors()[0].get('msg', '')}"


def fusionar(
    del_modelo: Intencion, reparado: bool, s: Senales, regla: _Regla, fichas: Mapping[str, FichaEspecialista]
) -> Intencion:
    """The model's plan cross-checked with the rules.

    The model decides the plan (it read the whole message); the rules only move its confidence: agreeing on the
    specialists keeps the higher of both, naming specialists the rules saw no trace of costs 0.15. A past
    reference the rules saw always asks for the memory.
    """
    claves = set(del_modelo.especialistas())
    vistos = set(regla.candidatos) | set(s.puntuaciones)
    confianza = del_modelo.confianza
    if claves and not claves <= vistos and s.puntuaciones:
        confianza = max(0.0, confianza - 0.15)
    resumen = del_modelo.resumen or _resumen(del_modelo.tipo, del_modelo.especialistas(), fichas)
    return del_modelo.model_copy(
        update={
            "confianza": round(confianza, 3),
            "origen": "mixto" if reparado else "modelo",
            "necesita_memoria": del_modelo.necesita_memoria or s.pasado or s.recordar,
            "resumen": resumen[:300],
        }
    )


def _con_aclaracion(
    intencion: Intencion, candidatos: Sequence[str], fichas: Mapping[str, FichaEspecialista]
) -> Intencion:
    return intencion.model_copy(
        update={
            "encargos": [],
            "aclaracion": intencion.aclaracion or _aclaracion(candidatos, fichas),
            "resumen": "Te pido una aclaración antes de consultar a nadie",
        }
    )


class Entendimiento:
    """Understanding for one set of active specialists. :meth:`entender` never raises."""

    def __init__(
        self,
        fichas: Sequence[FichaEspecialista],
        *,
        proveedor: LLMProvider | None = None,
        umbral: float = UMBRAL_POR_DEFECTO,
        usar_modelo: bool = True,
        vocab: Vocabulario | None = None,
    ) -> None:
        self._fichas = list(fichas)
        self._por_clave = {f.clave: f for f in self._fichas}
        self._proveedor = proveedor
        self._umbral = umbral
        self._usar_modelo = usar_modelo
        self._vocab = vocab or vocabulario()

    def entender(
        self,
        consulta: str,
        *,
        chat_history: Sequence[Mapping[str, Any]] | None = None,
        case: CaseState | None = None,
    ) -> Comprension:
        s, regla = por_reglas(consulta, self._fichas, chat_history=chat_history, case=case, vocab=self._vocab)
        if regla.intencion is not None:
            return self._final(Comprension(regla.intencion, s, motivo=f"reglas: {regla.motivo}"), regla.candidatos)
        if not self._usar_modelo or self._proveedor is None:
            return self._final(Comprension(regla.respaldo, s, motivo=f"sin modelo: {regla.motivo}"), regla.candidatos)
        datos, error = self._preguntar(consulta, s, chat_history)
        if error is not None:
            from asistente.plataforma.ops.consumo import PresupuestoAgotadoError

            if isinstance(error, PresupuestoAgotadoError):
                raise error
            return self._final(
                Comprension(regla.respaldo, s, modelo_consultado=True, motivo=f"modelo falló: {error}"),
                regla.candidatos,
            )
        intencion, reparado, por_que = validar_del_modelo(datos, list(self._por_clave), consulta=consulta)
        if intencion is None:
            logger.info("intent model output unusable (%s): rules fallback", por_que)
            return self._final(
                Comprension(regla.respaldo, s, modelo_consultado=True, motivo=f"salida no válida: {por_que}"),
                regla.candidatos,
            )
        fusion = fusionar(intencion, reparado, s, regla, self._por_clave)
        motivo = f"modelo ({regla.motivo})" + (f"; reparado: {por_que}" if por_que else "")
        return self._final(Comprension(fusion, s, modelo_consultado=True, motivo=motivo), regla.candidatos)

    def _preguntar(
        self, consulta: str, s: Senales, chat_history: Sequence[Mapping[str, Any]] | None
    ) -> tuple[Any, Exception | None]:
        from asistente.nucleo.agent.providers.base import ProviderError

        assert self._proveedor is not None
        try:
            datos = self._proveedor.complete_json(
                system=prompt("cerebro_intencion", INTENCION_SYSTEM_PROMPT),
                user=_mensaje_modelo(consulta, self._fichas, s, chat_history),
                schema=INTENCION_SCHEMA,
                schema_name="intencion",
            )
        except ProviderError as exc:
            logger.warning("intent model failed: %s", exc)
            return None, exc
        except Exception as exc:  # a broken provider never breaks the turn
            logger.exception("intent model raised")
            return None, exc
        return datos, None

    def _final(self, comprension: Comprension, candidatos: Sequence[str]) -> Comprension:
        """Below the threshold, one question to the user instead of a guess."""
        intencion = comprension.intencion
        if intencion.aclaracion:
            intencion = intencion.model_copy(update={"encargos": []})
        elif intencion.confianza < self._umbral and intencion.tipo not in ("charla", "fuera_de_ambito"):
            intencion = _con_aclaracion(intencion, candidatos, self._por_clave)
        comprension.intencion = intencion
        return comprension


def es_paso_directo(intencion: Intencion) -> bool:
    """One encargo, to the tax specialist, for a plain question: its own verified answer IS the answer."""
    return (
        intencion.tipo == "ambito"
        and not intencion.aclaracion
        and len(intencion.encargos) == 1
        and intencion.encargos[0].especialista == CLAVE_FISCAL
    )


__all__ = [
    "MARGEN_DOMINIO",
    "UMBRAL_CANDIDATO",
    "UMBRAL_DOMINIO",
    "UMBRAL_POR_DEFECTO",
    "Comprension",
    "Entendimiento",
    "FichaEspecialista",
    "Senales",
    "Vocabulario",
    "es_paso_directo",
    "fusionar",
    "plano",
    "por_reglas",
    "senales",
    "validar_del_modelo",
    "vocabulario",
]
