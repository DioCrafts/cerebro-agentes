"""The deterministic gate between the synthesis model and the report. No model here.

What a claim must pass to reach the report:

1. every quote names a source of THIS research and is LITERALLY in that source's full text (compared as the
   legal-data checks compare quotes: NFKC, lower case, no whitespace or quotation marks — ``comprimido``),
   at least :data:`MIN_CITA` characters long; quotes that fail are removed, a claim left without quotes is
   dropped;
2. every figure in the claim (amounts, percentages, days, years) is printed in one of its surviving quotes;
3. neither the claim nor its quotes carry prompt-injection bait, and the claim names no web address that is not
   one of the sources (a page cannot use the report to send the user elsewhere).

Then, over the verified claims: each is marked official when one of its quotes comes from an official source;
claims of the same topic that rest on different sources and give different figures are flagged as a
conflict (the model's own conflict notes are kept only when they name two existing sources and are clean);
and the confidence follows from all that (:func:`confianza`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from asistente.capacidades.investigacion.dominios import host_de
from asistente.capacidades.investigacion.fuentes import FuenteRecuperada, plano, sospechosa
from asistente.capacidades.investigacion.modelo import Afirmacion, Cita, Confianza, Conflicto
from asistente.conocimiento.texto import aparece, comprimido

MIN_CITA = 12
MAX_CITA = 600
MAX_AFIRMACIONES = 20
_CIFRA_RE = re.compile(r"(?<![\w.,])\d{1,3}(?:\.\d{3})+(?:,\d+)?(?![\d])|(?<![\w.,])\d+(?:,\d+)?(?![\d])")
_URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s)\]]+", re.IGNORECASE)


def _cifra(token: str) -> Decimal | None:
    try:
        return Decimal(token.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def cifras(texto: str) -> list[str]:
    """The figures printed in ``texto`` (Spanish format: 1.234,56), as written."""
    return [m.group(0) for m in _CIFRA_RE.finditer(texto)]


def _cifra_respaldada(token: str, citas: str) -> bool:
    valor = _cifra(token)
    return valor is not None and aparece(citas, valor)


def cita_literal(cita: str, fuente: FuenteRecuperada) -> bool:
    """Is ``cita`` literally in the source's full text (whitespace and quotation marks aside)?"""
    buscada = comprimido(cita)
    return len(buscada) >= MIN_CITA and buscada in comprimido(fuente.texto)


@dataclass
class ResultadoPuerta:
    afirmaciones: list[Afirmacion] = field(default_factory=list)
    descartes: list[str] = field(default_factory=list)
    conflictos: list[Conflicto] = field(default_factory=list)
    no_verificable: list[str] = field(default_factory=list)
    citadas: set[str] = field(default_factory=set)
    avisos: list[str] = field(default_factory=list)


def _texto(valor: Any, maximo: int) -> str:
    return " ".join(str(valor).split())[:maximo] if isinstance(valor, str) else ""


def _urls_ajenas(texto: str, fuentes: Mapping[str, FuenteRecuperada]) -> list[str]:
    hosts = {f.dominio for f in fuentes.values() if f.dominio}
    return [u for u in _URL_RE.findall(texto) if host_de(u if "://" in u else "https://" + u) not in hosts]


def revisar_afirmacion(
    bruta: Mapping[str, Any], fuentes: Mapping[str, FuenteRecuperada]
) -> tuple[Afirmacion | None, str | None]:
    """One claim through rules 1–3: (the claim with its surviving quotes, None) or (None, why it was dropped)."""
    texto = _texto(bruta.get("texto"), 800)
    tema = _texto(bruta.get("tema"), 80) or "general"
    if not texto:
        return None, None
    if sospechosa(texto):
        return None, f"«{texto[:160]}»: contiene instrucciones incrustadas (posible inyección), descartada."
    ajenas = _urls_ajenas(texto, fuentes)
    if ajenas:
        return None, f"«{texto[:160]}»: menciona una web que no es ninguna de las fuentes ({ajenas[0][:80]})."
    citas: list[Cita] = []
    for cruda in bruta.get("citas") or []:
        if not isinstance(cruda, Mapping):
            continue
        fuente_id = _texto(cruda.get("fuente"), 10).upper()
        cita = _texto(cruda.get("cita"), MAX_CITA)
        fuente = fuentes.get(fuente_id)
        if fuente is None or not cita or sospechosa(cita) or not cita_literal(cita, fuente):
            continue
        if all(not (c.fuente == fuente_id and c.cita == cita) for c in citas):
            citas.append(Cita(fuente=fuente_id, cita=cita))
    if not citas:
        return None, f"«{texto[:160]}»: ninguna cita aparece literalmente en la fuente indicada."
    texto_citas = " ".join(c.cita for c in citas).lower()
    sin_respaldo = [c for c in cifras(texto) if not _cifra_respaldada(c, texto_citas)]
    if sin_respaldo:
        return None, f"«{texto[:160]}»: la cifra {sin_respaldo[0]} no aparece en sus citas."
    oficial = any(fuentes[c.fuente].oficial for c in citas)
    return Afirmacion(texto=texto, tema=tema, citas=citas, oficial=oficial), None


def _conflictos_por_cifras(afirmaciones: Sequence[Afirmacion]) -> list[Conflicto]:
    """Same topic, different sources, different figures."""
    por_tema: dict[str, list[Afirmacion]] = {}
    for afirmacion in afirmaciones:
        por_tema.setdefault(plano(afirmacion.tema).strip(), []).append(afirmacion)
    conflictos: list[Conflicto] = []
    for grupo in por_tema.values():
        for i, a in enumerate(grupo):
            for b in grupo[i + 1 :]:
                fuentes_a = {c.fuente for c in a.citas}
                fuentes_b = {c.fuente for c in b.citas}
                if fuentes_a & fuentes_b:
                    continue
                cifras_a = {_cifra(x) for x in cifras(a.texto)} - {None}
                cifras_b = {_cifra(x) for x in cifras(b.texto)} - {None}
                if cifras_a and cifras_b and cifras_a != cifras_b:
                    ids = sorted(fuentes_a | fuentes_b, key=lambda s: int(s[1:]) if s[1:].isdigit() else 0)
                    conflictos.append(
                        Conflicto(
                            tema=a.tema,
                            fuentes=ids,
                            descripcion=(
                                f"Las fuentes {', '.join(sorted(fuentes_a))} y {', '.join(sorted(fuentes_b))} dan "
                                f"cifras distintas sobre «{a.tema}». Comprueba cuál está vigente."
                            ),
                            origen="cifras",
                        )
                    )
    return conflictos


def revisar(salida: Mapping[str, Any], fuentes: Mapping[str, FuenteRecuperada]) -> ResultadoPuerta:
    """The synthesis output through the whole gate."""
    resultado = ResultadoPuerta()
    crudas = salida.get("afirmaciones")
    for bruta in (crudas if isinstance(crudas, list) else [])[:MAX_AFIRMACIONES]:
        if not isinstance(bruta, Mapping):
            continue
        afirmacion, motivo = revisar_afirmacion(bruta, fuentes)
        if afirmacion is not None:
            resultado.afirmaciones.append(afirmacion)
            resultado.citadas.update(c.fuente for c in afirmacion.citas)
        elif motivo:
            resultado.descartes.append(motivo)
    resultado.conflictos = _conflictos_por_cifras(resultado.afirmaciones)
    temas_con_conflicto = {plano(c.tema) for c in resultado.conflictos}
    declarados = salida.get("conflictos")
    for crudo in declarados if isinstance(declarados, list) else []:
        if not isinstance(crudo, Mapping):
            continue
        ids = sorted({_texto(f, 10).upper() for f in crudo.get("fuentes") or [] if isinstance(f, str)})
        ids = [i for i in ids if i in fuentes]
        descripcion = _texto(crudo.get("descripcion"), 400)
        tema = _texto(crudo.get("tema"), 80) or "general"
        if len(ids) < 2 or not descripcion or sospechosa(descripcion) or _urls_ajenas(descripcion, fuentes):
            continue
        if plano(tema) in temas_con_conflicto:
            continue
        resultado.conflictos.append(Conflicto(tema=tema, fuentes=ids, descripcion=descripcion, origen="modelo"))
    pendientes = salida.get("no_verificable")
    for crudo in pendientes if isinstance(pendientes, list) else []:
        texto = _texto(crudo, 300)
        if texto and not sospechosa(texto) and not _urls_ajenas(texto, fuentes):
            resultado.no_verificable.append(texto)
    for fuente_id in sorted(resultado.citadas):
        fuente = fuentes[fuente_id]
        if fuente.sospechosa:
            resultado.avisos.append(
                f"La fuente {fuente_id} contenía instrucciones incrustadas (posible manipulación): se han "
                "neutralizado; contrasta lo que se apoya en ella."
            )
        if fuente.nota and fuente.tipo == "corpus" and fuente.nota.startswith("Doctrina"):
            resultado.avisos.append(f"{fuente_id}: {fuente.nota}")
    return resultado


def confianza(resultado: ResultadoPuerta, fuentes: Mapping[str, FuenteRecuperada]) -> Confianza:
    """``alta``: verified claims, each with an official source, nothing dropped, no conflicts, no suspicious
    source cited. ``baja``: nothing verified, or every claim rests only on non-official sources. ``media``: the rest."""
    afirmaciones = resultado.afirmaciones
    if not afirmaciones or not any(a.oficial for a in afirmaciones):
        return "baja"
    sospechosas = any(fuentes[f].sospechosa for f in resultado.citadas)
    if (
        all(a.oficial for a in afirmaciones)
        and not resultado.descartes
        and not resultado.conflictos
        and not sospechosas
    ):
        return "alta"
    return "media"


def respuesta(afirmaciones: Sequence[Afirmacion]) -> str:
    """The answer: the verified claims, each followed by its source ids — nothing the gate did not see."""
    lineas = []
    for afirmacion in afirmaciones:
        ids = "".join(f"[{i}]" for i in dict.fromkeys(c.fuente for c in afirmacion.citas))
        marca = "" if afirmacion.oficial else " (solo fuentes no oficiales)"
        lineas.append(f"- {afirmacion.texto} {ids}{marca}")
    return "\n".join(lineas)


__all__ = [
    "MAX_AFIRMACIONES",
    "MIN_CITA",
    "ResultadoPuerta",
    "cifras",
    "cita_literal",
    "confianza",
    "respuesta",
    "revisar",
    "revisar_afirmacion",
]
