"""Reading dates of the modules' legal data: which files take part, which groups a changed article reaches, and how
a new reading is written without reformatting a file.

The tax parameter files and the modules' data files record a law's reading the same way — a node with ``boe_id``,
``consolidado_a``, ``consultado_el`` and ``sha256_html`` (``fuentes_principales`` of each file) — so the reading
log of :mod:`hacienda_gpt.tax.recodificacion.procedencia` covers both. This module holds what is specific to the
modules' files.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
from pathlib import Path
import re
from typing import Any

from hacienda_gpt.conocimiento.modelo import GrupoLegal
from hacienda_gpt.conocimiento.registro import FicheroLegal, con_datos


def ficheros_de_modulos(data_dir: Path, modulos: Sequence[FicheroLegal] | None = None) -> list[FicheroLegal]:
    """The modules' data files a run over ``data_dir`` covers.

    Given explicitly (``modulos``), those. Otherwise every declared file — but only when ``data_dir`` is the package's
    own data directory: a run over a copy of the tax files (a test, a dry run) never touches the real module files.
    """
    if modulos is not None:
        return con_datos(modulos)
    from hacienda_gpt.tax.params import DATA_DIR

    if Path(data_dir).resolve() != Path(DATA_DIR).resolve():
        return []
    return con_datos()


def bloques_de_grupo(grupo: GrupoLegal, boe_id: str, pagina: str | None) -> set[str]:
    """Blocks of the law ``boe_id`` a module group is read from ("5", "da 61"): the articles its passages are
    labelled with and those where today's page prints them."""
    from hacienda_gpt.tax.recodificacion.adaptadores_otros import bloques_de
    from hacienda_gpt.tax.recodificacion.control import articulos_que_contienen, partir_base

    propias = [c for c in grupo.citas if c.boe_id == boe_id and c.version is None]
    if not propias:
        return set()
    bloques = {partir_base(b)[0] for c in propias for b in bloques_de(c.articulo)}
    if pagina:
        bloques |= {partir_base(b)[0] for b in articulos_que_contienen(pagina, [c.cita for c in propias])}
    return bloques


def grupos_en_bloques(
    boe_id: str,
    pagina: str | None,
    bloques: Iterable[str],
    ficheros: Iterable[FicheroLegal],
    *,
    incluir_recodificables: bool,
) -> list[str]:
    """Labels of the module groups read from one of ``bloques`` of ``boe_id``: a new wording there may change them
    outside the passages they quote, so a person (or the loop, for a re-encodable file) must re-read them.

    ``incluir_recodificables``: also the files the loop maintains (when the loop's own target search does not reach
    them).
    """
    buscados = set(bloques)
    if not buscados:
        return []
    out: list[str] = []
    for fichero in ficheros:
        if fichero.recodificable is not None and not incluir_recodificables:
            continue
        try:
            grupos = fichero.grupos(fichero.leer())
        except Exception as exc:  # a file that does not load blocks the reading, it does not crash it
            out.append(f"{fichero.nombre}: no se pudo cargar ({type(exc).__name__})")
            continue
        for grupo in grupos:
            tocados = sorted(bloques_de_grupo(grupo, boe_id, pagina) & buscados)
            if tocados:
                out.append(f"{fichero.nombre}: {grupo.nombre or grupo.descripcion} (art. {', '.join(tocados)})")
    return out


# --------------------------------------------------------------------------- #
# Writing a reading without reformatting the file
# --------------------------------------------------------------------------- #


def _objeto_que_contiene(texto: str, posicion: int) -> tuple[int, int] | None:
    """Span of the innermost JSON object around ``posicion`` (strings are skipped when matching braces)."""
    pila: list[int] = []
    pares: list[tuple[int, int]] = []
    dentro = escape = False
    for i, ch in enumerate(texto):
        if dentro:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                dentro = False
            continue
        if ch == '"':
            dentro = True
        elif ch == "{":
            pila.append(i)
        elif ch == "}" and pila:
            inicio = pila.pop()
            if inicio <= posicion <= i:
                pares.append((inicio, i + 1))
    return min(pares, key=lambda p: p[1] - p[0]) if pares else None


def _nodo(raw: Any, ruta: Sequence[str]) -> Any:
    for paso in ruta:
        raw = raw[int(paso)] if isinstance(raw, list) else raw[paso]
    return raw


def anotar_en_texto(texto: str, cambios: Mapping[tuple[str, ...], Mapping[str, str]]) -> str | None:
    """``texto`` with the new values of some fields of some source nodes (path -> {field: value}), every other byte
    unchanged; None when that cannot be done safely (the caller then rewrites the whole file).

    Each node is found by its own ``sha256_html`` (unique in a file) and only its fields are replaced; the result
    must parse to exactly the expected data.
    """
    esperado = json.loads(texto)
    nuevo = texto
    for ruta, campos in cambios.items():
        nodo = _nodo(esperado, ruta)
        if not isinstance(nodo, dict) or not isinstance(nodo.get("sha256_html"), str):
            return None
        ancla = f'"sha256_html": "{nodo["sha256_html"]}"'
        if nuevo.count(ancla) != 1:
            return None
        tramo = _objeto_que_contiene(nuevo, nuevo.index(ancla))
        if tramo is None:
            return None
        inicio, fin = tramo
        objeto = nuevo[inicio:fin]
        for campo, valor in campos.items():
            patron = re.compile(rf'("{re.escape(campo)}"\s*:\s*)"[^"\\]*"')
            if len(patron.findall(objeto)) != 1:
                return None
            objeto = patron.sub(lambda m, v=valor: m.group(1) + json.dumps(v, ensure_ascii=False), objeto)
        nuevo = nuevo[:inicio] + objeto + nuevo[fin:]
        nodo.update(campos)
    try:
        return nuevo if json.loads(nuevo) == esperado else None
    except ValueError:
        return None


__all__ = ["anotar_en_texto", "bloques_de_grupo", "ficheros_de_modulos", "grupos_en_bloques"]
