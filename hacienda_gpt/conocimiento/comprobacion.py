"""The one checker of legal data: quotes verbatim in the law, every figure inside its quote, the right version.

Two entry points share the same primitives (:func:`cita_en_texto` over :func:`~hacienda_gpt.conocimiento.texto.
comprimido`):

* :func:`verificar` runs :class:`Comprobacion` objects — one per group and law — against the pages ``texto_de``
  returns: the weekly re-verification (``params-verify``) of the tax parameters and of every module's groups;
* :func:`comprobar_grupos` checks :class:`~hacienda_gpt.conocimiento.modelo.GrupoLegal` objects: each figure is
  spelled in one of its group's passages and that spelling means the stored value (offline), and — given the law
  texts — each passage is verbatim in the version of the law it was read from.

The re-encoding gate uses the stricter :func:`citas_no_literales` (one contiguous passage, long enough to mean
something). No model is involved anywhere here.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hacienda_gpt.conocimiento.modelo import GrupoLegal, Ortografia
from hacienda_gpt.conocimiento.texto import TextoDe, aparece, comprimido, juntas, texto_plano

Tipo = Literal["cita", "tabla", "cifras"]
# A quote shorter than this (ignoring whitespace) proves nothing: "300" is in every law.
MIN_CITA = 15
SIN_TEXTO = "sin texto"


class Comprobacion(BaseModel):
    """One check of one group against one law (and version)."""

    model_config = ConfigDict(extra="forbid")

    boe_id: str
    # AAAAMMDD of a superseded wording to check against instead of the current text; "diario" for the daily BOE.
    version: str | None = None
    # Where the parameters live in the JSON file, e.g. ["comunidades", "galicia", "escala_general"].
    ruta: list[str]
    descripcion: str
    tipo: Tipo
    cifras: list[Decimal] = Field(default_factory=list)
    citas: list[str] = Field(default_factory=list)
    # Whether "[…]" joins separate passages of the quote. False: each quote must be one passage.
    cortes: bool = True
    # Problems already found without the law (figures not spelled in their quotes): they make the check fail.
    previos: list[str] = Field(default_factory=list)


class Resultado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comprobacion: Comprobacion
    ok: bool
    # Figures or quotes no longer found; ["sin texto"] when the law could not be read.
    faltan: list[str] = Field(default_factory=list)


def partes_de(cita: str) -> list[str]:
    """The passages a quote stitched with "[…]" (or "…") is made of; long enough to mean something each."""
    return [p for p in re.split(r"\[\.\.\.\]|\[…\]|…", cita) if len(comprimido(p)) > MIN_CITA] or [cita]


def cita_en_texto(cita: str, texto_comprimido: str, *, cortes: bool = False) -> bool:
    """Is ``cita`` verbatim in a text already passed through :func:`comprimido`?"""
    if not cortes:
        return comprimido(cita) in texto_comprimido
    return all(comprimido(p) in texto_comprimido for p in partes_de(cita))


def verificar(checks: Iterable[Comprobacion], texto_de: TextoDe) -> list[Resultado]:
    """Run the checks. ``texto_de(boe_id, version)`` returns the law's page (HTML), or None when unavailable."""
    textos: dict[tuple[str, str | None], tuple[str, str] | None] = {}
    out: list[Resultado] = []
    for check in checks:
        clave = (check.boe_id, check.version)
        if clave not in textos:
            pagina = texto_de(check.boe_id, check.version)
            plano = texto_plano(pagina) if pagina else ""
            if pagina and check.version is None:
                # Format watch (never raises): a page read as empty looks like "every figure is gone".
                from hacienda_gpt.ops import formatos

                formatos.observar("boe_consolidado_html", pagina, contexto=check.boe_id)
            textos[clave] = (plano, comprimido(plano)) if plano else None
        par = textos[clave]
        if par is None:
            out.append(Resultado(comprobacion=check, ok=False, faltan=[SIN_TEXTO]))
            continue
        plano, texto_comprimido = par
        faltan: list[str] = list(check.previos)
        faltan += [cita[:120] for cita in check.citas if not cita_en_texto(cita, texto_comprimido, cortes=check.cortes)]
        if check.tipo == "tabla":
            ausentes = [str(c) for c in check.cifras if not aparece(plano, c)]
            if ausentes:
                faltan += ausentes
            elif not juntas(plano, check.cifras):
                faltan.append("las cifras ya no aparecen juntas en una tabla")
        elif check.tipo == "cifras":
            faltan += [str(c) for c in check.cifras if not aparece(plano, c)]
        out.append(Resultado(comprobacion=check, ok=not faltan, faltan=faltan))
    return out


# --------------------------------------------------------------------------- #
# The stricter rule of the re-encoding gate
# --------------------------------------------------------------------------- #


def citas_no_literales(citas: Iterable[str], texto_comprimido: str) -> list[str]:
    """Problems of ``citas`` against a text already passed through :func:`comprimido`.

    Stricter than the weekly re-verification: a quote must be ONE contiguous passage (no "[...]" stitching, which
    would let a figure travel between unrelated sentences) and long enough to mean something.
    """
    problemas: list[str] = []
    vistas = 0
    for cita in citas:
        vistas += 1
        propia = comprimido(cita)
        if len(propia) < MIN_CITA:
            problemas.append(f"cita demasiado corta para probar nada: «{cita[:60]}»")
        elif propia not in texto_comprimido:
            problemas.append(f"cita no literal (no está en el texto legal): «{cita[:90]}»")
    if not vistas:
        problemas.append("no hay ninguna cita literal")
    return problemas


def es_literal(cita: str, texto_comprimido: str) -> bool:
    propia = comprimido(cita)
    return len(propia) >= MIN_CITA and propia in texto_comprimido


# --------------------------------------------------------------------------- #
# Groups of legal data
# --------------------------------------------------------------------------- #


def _con(nombre: str, ruta: str) -> str:
    return f"{nombre}.{ruta}" if nombre else ruta


def _fecha_iso(texto: str) -> bool:
    try:
        date.fromisoformat(texto)
    except ValueError:
        return False
    return True


def comprobar_grupo(
    grupo: GrupoLegal,
    *,
    ortografia: Ortografia,
    fuentes: Collection[str] | None = None,
    textos: Mapping[str, str] | None = None,
    comprimidos: dict[str, str] | None = None,
) -> list[str]:
    """Problems of one group (empty: consistent). Messages are Spanish, prefixed by the group's (or passage's) name.

    Always checked: every figure of the group is spelled in one of its passages, as a whole token, and the spelling
    means the stored value; every source a passage names is one the file lists (``fuentes``, when given); the
    group's ``en_vigor_desde`` is a date. With ``textos`` (``<BOE-ID>`` or ``<BOE-ID>@<AAAAMMDD>`` -> plain text of
    the law, or of the quoted articles) every passage must also be verbatim in the version it was read from.
    """
    problemas: list[str] = []
    comprimidos = {} if comprimidos is None else comprimidos
    nombre = grupo.nombre
    sin_evidencia = set(grupo.cifras)
    if fuentes is not None and grupo.fuente.boe_id and grupo.fuente.boe_id not in fuentes:
        problemas.append(f"{nombre}: {grupo.fuente.boe_id} no figura en fuentes_principales")
    if grupo.fuente.en_vigor_desde is not None and not _fecha_iso(grupo.fuente.en_vigor_desde):
        problemas.append(f"{nombre}: en_vigor_desde no es una fecha")
    for cita in grupo.citas:
        prefijo = cita.nombre or nombre
        clave = cita.clave_texto
        if fuentes is not None and clave not in fuentes:
            problemas.append(f"{prefijo}: {clave} no figura en fuentes_principales")
        if textos is not None:
            if clave not in textos:
                problemas.append(f"{prefijo}: no se ha aportado el texto de {clave}")
            else:
                if clave not in comprimidos:
                    comprimidos[clave] = comprimido(textos[clave])
                if not cita_en_texto(cita.cita, comprimidos[clave], cortes=cita.cortes):
                    que = f"la cita de {cita.articulo}" if cita.articulo else f"«{cita.cita[:80]}…»"
                    problemas.append(f"{prefijo}: {que} no aparece literal en {clave}")
        de_cita = cita.articulo or "su cita"
        for ruta, literal, es_fecha in [
            *((r, lit, False) for r, lit in cita.cifras.items()),
            *((r, lit, True) for r, lit in cita.fechas.items()),
        ]:
            if ruta not in grupo.cifras:
                articulo = "un" if ortografia.sustantivo == "dato" else "una"
                problemas.append(f"{prefijo}: la evidencia cita {articulo} {ortografia.sustantivo} inexistente ({ruta})")
                continue
            if not ortografia.esta_en(literal, cita.cita):
                problemas.append(f"{_con(prefijo, ruta)}: «{literal}» no aparece en la cita de {de_cita}")
                continue
            leer = ortografia.leer_fecha if es_fecha and ortografia.leer_fecha else ortografia.leer_cifra
            try:
                leido = leer(literal)
            except (InvalidOperation, ArithmeticError, ValueError, IndexError, KeyError):
                problemas.append(f"{_con(prefijo, ruta)}: «{literal}» no se puede leer como {ortografia.sustantivo}")
                continue
            if leido != grupo.cifras[ruta]:
                problemas.append(f"{_con(prefijo, ruta)}: el dato es {grupo.cifras[ruta]} y la cita dice «{literal}»")
            # Spelled in a passage: a wrong value is reported above, not as a figure without evidence.
            sin_evidencia.discard(ruta)
    problemas.extend(f"{_con(nombre, ruta)}: {ortografia.sustantivo} sin evidencia" for ruta in sorted(sin_evidencia))
    return problemas


def comprobar_grupos(
    grupos: Iterable[GrupoLegal],
    *,
    ortografia: Ortografia,
    fuentes: Collection[str] | None = None,
    textos: Mapping[str, str] | None = None,
) -> dict[str, list[str]]:
    """:func:`comprobar_grupo` over a file's groups: group name -> its problems (every group, in order)."""
    comprimidos: dict[str, str] = {}
    return {
        grupo.nombre: comprobar_grupo(
            grupo, ortografia=ortografia, fuentes=fuentes, textos=textos, comprimidos=comprimidos
        )
        for grupo in grupos
    }


__all__ = [
    "MIN_CITA",
    "SIN_TEXTO",
    "Comprobacion",
    "Resultado",
    "Tipo",
    "cita_en_texto",
    "citas_no_literales",
    "comprobar_grupo",
    "comprobar_grupos",
    "es_literal",
    "partes_de",
    "verificar",
]
