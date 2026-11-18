"""Anonymous presentation of proposals: who wrote what is hidden, the order is shuffled per reader, the form is uniform.

Why each measure (the biases they remove are measured by the evaluation's bias harness):

* **Nobody reviews itself** — :func:`anonimizar_para` drops the reviewer's own proposal: self-preference is the
  strongest bias of model judges.
* **Labels by position, order per reader** — labels A, B, C… are given in the order shown, and the order is a
  permutation of the other seats derived deterministically from (seed, session, reader): different readers see the
  same proposal under different labels and at different positions (position bias averages out across reviewers), and
  a session is reproducible from its seed. A label means nothing outside one reader's view; the label → seat map
  stays on the server.
* **Balanced design** — :func:`diseno_equilibrado` goes further than random orders: across the reviewers EVERY
  proposal appears EXACTLY ONCE in every position. With ``n`` members each reviewer sees ``n − 1`` proposals; the
  rows of a cyclic Latin square of order ``n`` with the diagonal removed (reviewer ``i`` sees seats ``i+1 … i+n−1``
  mod ``n``) satisfy it, and relabelling the seats with a seeded permutation and permuting the positions with
  another keeps it (so the design is not the same fixed cycle in every session). :func:`balance_de` checks it.
* **Same form for everyone** — :func:`normalizar_propuesta` gives every proposal the same schema
  (``PropuestaAnonima``) and strips what could reveal its author or sway a reader by style: self-references («como
  fiscal…», «desde mi mandato…», «como modelo de IA…»), seat names and ids, model and vendor names, signatures,
  markdown (headings dropped), emojis, line breaks; it caps every text (verbosity bias), rounds the stated confidence to 0.05 (no
  fingerprint in «0,73»), sorts the cited figure ids, and neutralises injection bait (another model will read it).
  The SUBSTANCE is untouched: the recommendation, the reasoning, the figures.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
import hashlib
import random
import re
import unicodedata

from asistente.conocimiento.llm.security import neutralizar_inyeccion, sanitize_retrieved_context
from asistente.nucleo.consejo.contrato import MAX_TEXTO_PROPUESTA, Propuesta, PropuestaAnonima, Rol
from asistente.nucleo.consejo.miembros import PRESIDENTE, ROLES_CONOCIDOS

ETIQUETAS = "ABCDEFG"
TOPE_ELEMENTO = 300  # characters of one assumption / risk / change-of-mind item
TOPE_CONDICION = 600
MAX_SUPUESTOS = 6
MAX_RIESGOS = 6
MAX_CAMBIO = 4
PASO_CONFIANZA = 0.05

Semilla = int | str | None


# --------------------------------------------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------------------------------------------


def semilla_efectiva(semilla: Semilla, sesion_id: str) -> str:
    """The seed actually used: the given one, or one derived from the session (reproducible per session)."""
    if semilla is not None and str(semilla).strip():
        return str(semilla).strip()
    return f"sesion:{sesion_id}"


def generador(*partes: str) -> random.Random:
    """A PRNG seeded from ``partes`` (SHA-256: stable across processes and Python versions, unlike ``hash``)."""
    digest = hashlib.sha256("\x1f".join(partes).encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:16], "big"))


def orden_para(evaluador_id: str | None, miembros: Iterable[str], semilla: Semilla, sesion_id: str = "") -> list[str]:
    """The order in which ``evaluador_id`` sees the OTHER seats (None: a reader with no proposal, the chair)."""
    otros = sorted({m for m in miembros if m != evaluador_id})
    rng = generador(semilla_efectiva(semilla, sesion_id), sesion_id, "lector", evaluador_id or "-")
    rng.shuffle(otros)
    return otros


def diseno_equilibrado(miembros: Iterable[str], semilla: Semilla, sesion_id: str = "") -> dict[str, list[str]]:
    """For each seat, the order in which it sees the others, such that every seat appears once in every position."""
    asientos = sorted(set(miembros))
    n = len(asientos)
    if n < 2:
        return {a: [] for a in asientos}
    rng = generador(semilla_efectiva(semilla, sesion_id), sesion_id, "diseno")
    sigma = list(asientos)
    rng.shuffle(sigma)  # seat at abstract index i
    tau = list(range(n - 1))
    rng.shuffle(tau)  # which offset goes to each position
    return {sigma[i]: [sigma[(i + tau[c] + 1) % n] for c in range(n - 1)] for i in range(n)}


def balance_de(diseno: Mapping[str, Sequence[str]]) -> dict[str, Counter[int]]:
    """How many times each seat is shown at each position (0-based) across the readers of ``diseno``."""
    cuenta: dict[str, Counter[int]] = {}
    for orden in diseno.values():
        for posicion, asiento in enumerate(orden):
            cuenta.setdefault(asiento, Counter())[posicion] += 1
    return cuenta


# --------------------------------------------------------------------------------------------------------------
# Normalisation of the text
# --------------------------------------------------------------------------------------------------------------

_MODELOS = (
    r"claude|anthropic|openai|chat\s?gpt|gpt[-\s]?[0-9][\w.\-]*|gpt|opus|sonnet|haiku|fable|llama|qwen|mistral|"
    r"mixtral|gemini|gemma|deepseek|copilot"
)
_GENERICOS = (
    r"miembros?|consejer[oa]s?|asesor(?:a|es)?|expert[oa]s?|especialistas?|analistas?|abogad[oa]s?|fiscalistas?|"
    r"economistas?|presidente|presidenta|modelos?\s+de\s+(?:lenguaje|ia|inteligencia\s+artificial)|ia|"
    r"inteligencia\s+artificial|asistentes?"
)
_INTRO = (
    r"(?:como|en\s+(?:mi\s+)?(?:calidad|condici[oó]n|papel|rol|funci[oó]n)\s+de|"
    r"desde\s+(?:mi|la|el|nuestra|nuestro)\s+(?:perspectiva|[oó]ptica|posici[oó]n|mirada|punto\s+de\s+vista|"
    r"mandato|papel|rol|asiento|enfoque)(?:\s+(?:de|del|como))?|soy|yo,?(?:\s+como)?)"
)
_ARTICULO = r"(?:\s+(?:el|la|los|las|un|una|mi|este|esta))?"
_MARKDOWN = (
    (re.compile(r"\[([^\]]{1,200})\]\((?:https?://|www\.)[^)]{1,300}\)"), r"\1"),  # links → their text
    (re.compile(r"(?m)^\s{0,3}#{1,6}[^\n]*$"), " "),  # headings: section labels (and a place for a signature)
    (re.compile(r"(?m)^\s{0,3}>\s?"), ""),  # quotes
    (re.compile(r"(?m)^\s*(?:[-*•+]|\d{1,2}[.)])\s+"), ""),  # list markers
    (re.compile(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$"), " "),  # rules
    (re.compile(r"\*\*|__|`+|~~"), ""),  # bold, code, strike
    (re.compile(r"(?<![\w*])\*(?!\s)([^*\n]{1,200}?)(?<!\s)\*(?![\w*])"), r"\1"),  # *italics*
)
# A trailing signature: «— Fiscal», «-- Prudencia Financiera» (capitalised words only, so «— Conviene esperar.» stays),
# or a closing formula («Atentamente, …»).
_FIRMA = re.compile(r"\s*(?:—|–|--)\s*(?:[A-ZÁÉÍÓÚÑ][\wáéíóúñ]*\s*){1,4}\.?\s*$")
_DESPEDIDA = re.compile(
    r"\s*\b(?:firmado|atentamente|un saludo|saludos cordiales|saludos)\b[^.\n]{0,60}\.?\s*$", re.IGNORECASE
)
# «Desde mi punto de vista, …»: the clause adds nothing but a speaker.
_DESDE_MI = re.compile(
    r"\b(?:desde|seg[uú]n)\s+mi\s+(?:perspectiva|[oó]ptica|posici[oó]n|mirada|punto\s+de\s+vista|mandato|papel|rol|"
    r"asiento|enfoque|criterio)\b[^,.;:\n]{0,60}[,;:]\s*",
    re.IGNORECASE,
)
_PROPIO = re.compile(r"\b(?:mi|nuestro|nuestra)\s+(?:mandato|asiento|rol|papel)\b", re.IGNORECASE)


def _patron_literal(texto: str) -> str:
    """A seat name or id as a pattern: its words with any spacing (an id keeps its underscores literally)."""
    if "_" in texto and " " not in texto:
        return re.escape(texto)
    partes = [re.escape(p) for p in texto.split() if p]
    return r"\s+".join(partes)


def identidades_por_defecto() -> tuple[Rol, ...]:
    return (*ROLES_CONOCIDOS.values(), PRESIDENTE)


class Limpiador:
    """Strips identity markers and styling from a text (built once per set of roles; thread-safe, stateless)."""

    def __init__(self, roles: Iterable[Rol] | None = None) -> None:
        lista = list(roles) if roles is not None else list(identidades_por_defecto())
        # Seat names (distinctive phrases, chosen so as not to be everyday wording) and ids with underscores.
        frases = sorted(
            {t for rol in lista for t in (rol.nombre, rol.id) if (" " in t or "_" in t)},
            key=len,
            reverse=True,
        )
        palabras = sorted({rol.id for rol in lista if "_" not in rol.id}, key=len, reverse=True)
        terminos = "|".join([*(_patron_literal(f) for f in frases), *(re.escape(p) for p in palabras), _GENERICOS])
        self._autorreferencia = re.compile(
            rf"\b{_INTRO}{_ARTICULO}\s+(?:{terminos})\b[^,.;:\n]{{0,60}}[,;:]?\s*", re.IGNORECASE
        )
        self._nombres = (
            re.compile(r"\b(?:" + "|".join(_patron_literal(f) for f in frases) + r")\b", re.IGNORECASE)
            if frases
            else None
        )
        # A bare seat id is a common word («fiscal», «prudente»): removed only as a tag, «(Fiscal)», «Fiscal:».
        self._etiquetas = (
            re.compile(
                r"[(\[]\s*(?:" + "|".join(re.escape(p) for p in palabras) + r")\s*[)\]]|"
                r"^\s*(?:" + "|".join(re.escape(p) for p in palabras) + r")\s*[:—–-]\s*",
                re.IGNORECASE | re.MULTILINE,
            )
            if palabras
            else None
        )
        self._modelos = re.compile(rf"\b(?:{_MODELOS})\b", re.IGNORECASE)

    def __call__(self, texto: str | None, tope: int) -> str:
        if not texto:
            return ""
        limpio = neutralizar_inyeccion(sanitize_retrieved_context(texto)).replace("<", "‹").replace(">", "›")
        for patron, sustituto in _MARKDOWN:
            limpio = patron.sub(sustituto, limpio)
        limpio = _DESPEDIDA.sub("", _FIRMA.sub("", limpio.rstrip()))
        limpio = self._autorreferencia.sub("", limpio)
        limpio = _DESDE_MI.sub("", limpio)
        limpio = _PROPIO.sub("este análisis", limpio)
        if self._etiquetas is not None:
            limpio = self._etiquetas.sub(" ", limpio)
        if self._nombres is not None:
            limpio = self._nombres.sub("", limpio)
        limpio = self._modelos.sub("", limpio)
        limpio = "".join(c for c in limpio if unicodedata.category(c) not in ("So", "Cs", "Co"))
        limpio = re.sub(r"!{2,}", "!", limpio)
        limpio = re.sub(r"\s+", " ", limpio).strip()
        limpio = re.sub(r"\s+([,.;:!?])", r"\1", limpio)
        limpio = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", limpio)
        limpio = re.sub(r"^[\s,;:.\-–—]+", "", limpio)
        limpio = re.sub(r"([.!?]\s+)([a-záéíóúñ])", lambda m: m.group(1) + m.group(2).upper(), limpio)
        if limpio and limpio[0].islower():
            limpio = limpio[0].upper() + limpio[1:]
        return recortar(limpio, tope)


def recortar(texto: str, tope: int) -> str:
    """``texto`` cut at ``tope`` characters on a word boundary, with «…» when cut."""
    if len(texto) <= tope:
        return texto
    corte = texto[: max(1, tope - 1)]
    espacio = corte.rfind(" ")
    if espacio > tope * 0.6:
        corte = corte[:espacio]
    return corte.rstrip(" ,;:") + "…"


_LIMPIADOR_POR_DEFECTO: Limpiador | None = None


def limpiador_por_defecto() -> Limpiador:
    global _LIMPIADOR_POR_DEFECTO
    if _LIMPIADOR_POR_DEFECTO is None:
        _LIMPIADOR_POR_DEFECTO = Limpiador()
    return _LIMPIADOR_POR_DEFECTO


def limpiar_texto(texto: str | None, tope: int = MAX_TEXTO_PROPUESTA, roles: Iterable[Rol] | None = None) -> str:
    """``texto`` without identity markers or styling (see the module docstring), capped at ``tope``."""
    limpiador = Limpiador(roles) if roles is not None else limpiador_por_defecto()
    return limpiador(texto, tope)


def _lista(elementos: Sequence[str], limpiador: Limpiador, maximo: int) -> list[str]:
    salida: list[str] = []
    for elemento in elementos:
        limpio = limpiador(elemento, TOPE_ELEMENTO)
        if limpio and limpio not in salida:
            salida.append(limpio)
        if len(salida) >= maximo:
            break
    return salida


def _clave_cifra(cifra_id: str) -> tuple[int, str]:
    digitos = re.sub(r"\D", "", cifra_id)
    return (int(digitos) if digitos else 0, cifra_id)


def redondear_confianza(confianza: float) -> float:
    pasos = round(min(1.0, max(0.0, confianza)) / PASO_CONFIANZA)
    return round(pasos * PASO_CONFIANZA, 2)


def normalizar_propuesta(propuesta: Propuesta, etiqueta: str, limpiador: Limpiador | None = None) -> PropuestaAnonima:
    """``propuesta`` as another member sees it under ``etiqueta`` (same schema, no author, uniform form)."""
    limpia = limpiador or limpiador_por_defecto()
    condicion = limpia(propuesta.condicion, TOPE_CONDICION) or None
    return PropuestaAnonima(
        etiqueta=etiqueta,
        recomendacion=propuesta.recomendacion,
        condicion=condicion,
        confianza=redondear_confianza(propuesta.confianza),
        razonamiento=limpia(propuesta.razonamiento, MAX_TEXTO_PROPUESTA),
        supuestos=_lista(propuesta.supuestos, limpia, MAX_SUPUESTOS),
        riesgos=_lista(propuesta.riesgos, limpia, MAX_RIESGOS),
        que_me_haria_cambiar=_lista(propuesta.que_me_haria_cambiar, limpia, MAX_CAMBIO),
        cifras_usadas=sorted(set(propuesta.cifras_usadas), key=_clave_cifra),
    )


def anonimizar_para(
    evaluador_id: str | None,
    propuestas: Sequence[Propuesta],
    semilla: Semilla,
    *,
    sesion_id: str = "",
    orden: Sequence[str] | None = None,
    limpiador: Limpiador | None = None,
) -> tuple[list[PropuestaAnonima], dict[str, str]]:
    """The proposals ``evaluador_id`` reviews (its own excluded), labelled A, B, C… in the order it sees them.

    ``orden``: the seats in the order to show them (e.g. a row of :func:`diseno_equilibrado`); by default the
    reader's seeded permutation (:func:`orden_para`). ``evaluador_id`` None = a reader with no proposal (the chair).
    Returns the anonymous proposals in that order and the label → seat map (kept on the server, never sent).
    """
    por_asiento: dict[str, Propuesta] = {}
    for propuesta in propuestas:
        if propuesta.miembro_id in por_asiento:
            raise ValueError(f"dos propuestas del mismo miembro: {propuesta.miembro_id}")
        por_asiento[propuesta.miembro_id] = propuesta
    candidatos = [a for a in por_asiento if a != evaluador_id]
    if orden is None:
        secuencia = orden_para(evaluador_id, candidatos, semilla, sesion_id)
    else:
        secuencia = list(orden)
        if sorted(secuencia) != sorted(candidatos):
            raise ValueError(f"el orden {secuencia} no es una permutación de las propuestas de los demás {candidatos}")
    if len(secuencia) > len(ETIQUETAS):
        raise ValueError(f"como mucho {len(ETIQUETAS)} propuestas por lector")
    anonimas: list[PropuestaAnonima] = []
    mapa: dict[str, str] = {}
    for etiqueta, asiento in zip(ETIQUETAS, secuencia, strict=False):
        anonimas.append(normalizar_propuesta(por_asiento[asiento], etiqueta, limpiador))
        mapa[etiqueta] = asiento
    return anonimas, mapa


__all__ = [
    "ETIQUETAS",
    "MAX_CAMBIO",
    "MAX_RIESGOS",
    "MAX_SUPUESTOS",
    "PASO_CONFIANZA",
    "TOPE_CONDICION",
    "TOPE_ELEMENTO",
    "Limpiador",
    "Semilla",
    "anonimizar_para",
    "balance_de",
    "diseno_equilibrado",
    "generador",
    "identidades_por_defecto",
    "limpiador_por_defecto",
    "limpiar_texto",
    "normalizar_propuesta",
    "orden_para",
    "recortar",
    "redondear_confianza",
    "semilla_efectiva",
]
