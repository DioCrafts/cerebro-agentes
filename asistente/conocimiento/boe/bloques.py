"""A consolidated law as the BOE publishes it: blocks (articles, provisions, headings), each with every version.

The open-data API (``/datosabiertos/api/legislacion-consolidada/id/<ID>/texto``) returns one ``<bloque>`` per
structural unit of the law. A ``precepto`` block is an article or a provision; an ``encabezado`` block is a heading
(título, capítulo, sección). Every block carries all its ``<version>`` elements: the text as each amending law left
it, with the date it came into force. So the unit of retrieval is the article, exactly as the law numbers it, and
the text applicable on a given date is a lookup, not a guess.

This module only parses and cuts; it does no network and no model call:

* :func:`leer_norma` — XML bytes to a :class:`Norma` (blocks in order, versions sorted, heading path per block);
* :func:`trocear` — one version of a block to fragments that fit the embedder, cut at paragraph boundaries, each
  prefixed with its norm and the title of its article;
* :func:`remisiones` — the articles a text refers to («artículo 23 de esta Ley», «artículo 20 de la Ley 37/1992»).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
import re
from typing import Any

#: A fragment's body, in characters (about 230 tokens of Spanish legal text with the Qwen3 tokenizer; the embedder
#: reads up to 512). Measured on the benchmark's 77 queries (dense search, one fragment per article): 800
#: characters puts the right article first in 56 % of them, against 49 % with 600, 51 % with 1,000 and 45 % with
#: 1,400. See docs/cerebro/benchmark-recuperacion.md.
MAX_CARACTERES = 800

_ESPACIOS = re.compile(r"\s+")
_ORDINALES_LATINOS = "bis|ter|quater|quáter|quinquies|sexies|septies|octies|nonies|decies"
_NUMERO_ARTICULO = re.compile(rf"art[íi]culo\s+(\d+)(?:[\s.]*({_ORDINALES_LATINOS}))?\b", re.IGNORECASE)
# «1. » opens an apartado; «1.º » is a numbered item inside one.
_APARTADO = re.compile(r"^(\d{1,3})\.\s")
_FRASE = re.compile(r"(?<=[.;:])\s+")
# A version that only says the article is gone.
_SIN_CONTENIDO = re.compile(r"^\W*\(?\s*(derogad|suprimid|sin contenido|anulad|sin efecto)", re.IGNORECASE)

# Heading levels by the CSS class the BOE gives the paragraphs of an ``encabezado`` block.
_NIVELES = (("libro", 0), ("titulo", 1), ("capitulo", 2), ("seccion", 3), ("subseccion", 4))
_NIVEL_DESCONOCIDO = 5

_REMISION = re.compile(
    rf"art[íi]culos?\s+(\d+(?:[\s.]*(?:{_ORDINALES_LATINOS}))?(?:(?:\s*,\s*|\s+y\s+|\s+a\s+|\s+o\s+)\d+"
    rf"(?:[\s.]*(?:{_ORDINALES_LATINOS}))?)*)",
    re.IGNORECASE,
)
_NUMERO_SUELTO = re.compile(rf"(\d+)(?:[\s.]*({_ORDINALES_LATINOS}))?", re.IGNORECASE)
# What follows a reference when it points at another norm: «de la Ley 37/1992», «del Real Decreto 439/2007»,
# «del texto refundido de la Ley …». «de esta Ley» and «de este Reglamento» are the norm itself.
_OTRA_NORMA = re.compile(
    # Only a short connector may stand between the number and the other norm («, apartado 2, de la Ley…»): a new
    # «artículo» or «de esta Ley» in between means the other norm belongs to a later reference.
    r"^(?:(?!art[íi]culo|\best[ae]\b|\bpresente\b)[^.;]){0,40}?\bde(?:l| la)\s+((?:Ley(?: Orgánica)?|Real Decreto(?:-ley| Legislativo)?|Reglamento|Orden|"
    r"texto refundido)[^.;,]{0,120}?\d+/\d{4})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Version:
    """A block's text between two amendments."""

    id_norma: str  # the BOE id of the law that wrote this version
    publicada: str  # ISO date of that law's publication
    vigente_desde: str  # ISO date this text came into force
    vigente_hasta: str | None  # ISO date the next version came into force; None: still in force
    rotulo: str  # «Artículo 68. Deducciones.» (the block's title when the version has no heading paragraph)
    parrafos: tuple[str, ...]  # the body, without the heading paragraph and without the amendment notes
    notas: tuple[str, ...] = ()  # the BOE's notes on what each amendment changed and since when

    @property
    def texto(self) -> str:
        return "\n".join(self.parrafos)

    @property
    def sin_contenido(self) -> bool:
        """The version only records that the article was repealed or left empty."""
        cuerpo = self.texto.strip()
        return not cuerpo or (len(cuerpo) < 200 and bool(_SIN_CONTENIDO.match(cuerpo)))

    def vigente_en(self, fecha: str) -> bool:
        return self.vigente_desde <= fecha and (self.vigente_hasta is None or fecha < self.vigente_hasta)


@dataclass(frozen=True)
class Bloque:
    bloque_id: str  # the BOE's own id within the law: «a68», «dacuarta», «ti»
    tipo: str  # precepto | encabezado | preambulo | firma | nota_inicial…
    titulo: str  # «Artículo 68», «Disposición adicional cuarta», «TÍTULO I»
    orden: int  # position in the law
    jerarquia: tuple[str, ...]  # the headings above it: («TÍTULO VI. Cálculo…», «CAPÍTULO I. …»)
    versiones: tuple[Version, ...]  # oldest first
    numero: str | None = None  # «68», «7 bis»; None for what is not an article

    @property
    def vigente(self) -> Version | None:
        """The version in force today (the last one), or None for a block with no text."""
        return self.versiones[-1] if self.versiones else None

    def version_en(self, fecha: str) -> Version | None:
        """The version in force on ``fecha`` (ISO), or None when the block did not exist yet."""
        return next((v for v in reversed(self.versiones) if v.vigente_en(fecha)), None)


@dataclass(frozen=True)
class Norma:
    boe_id: str
    bloques: tuple[Bloque, ...]
    titulo: str = ""  # «Ley 35/2006, de 28 de noviembre, del Impuesto sobre la Renta…» (from the sidecar)
    sigla: str = ""  # «LIRPF» (curated; empty when the norm has none)
    actualizada: str | None = None  # ISO date of the last amendment the BOE consolidated

    @property
    def preceptos(self) -> tuple[Bloque, ...]:
        return tuple(b for b in self.bloques if b.tipo == "precepto")

    @property
    def nombre_corto(self) -> str:
        """How a fragment names its norm: the sigla, else the title up to its first comma, else the BOE id."""
        return self.sigla or self.titulo.split(",")[0].strip() or self.boe_id


@dataclass(frozen=True)
class Trozo:
    """A fragment of one version of a block, ready to embed."""

    texto: str  # header line + body
    cuerpo: str  # the body alone
    apartados: tuple[str, ...] = field(default_factory=tuple)  # the numbered apartados it covers: («1», «2»)


def es_xml_de_bloques(cabecera: bytes) -> bool:
    """Whether the first bytes of a file look like the API's consolidated text (cheap probe, no parsing)."""
    return b"<response>" in cabecera and b"<bloque" in cabecera


def _fecha(valor: str | None) -> str | None:
    """``20070101`` → ``2007-01-01`` (the API's dates); None when missing or malformed."""
    if not valor or len(valor) != 8 or not valor.isdigit():
        return None
    return f"{valor[:4]}-{valor[4:6]}-{valor[6:]}"


def _limpio(texto: str) -> str:
    return _ESPACIOS.sub(" ", texto).strip()


def _texto_de(elemento: Any) -> str:
    return _limpio("".join(elemento.itertext()))


def _filas_de_tabla(tabla: Any) -> list[str]:
    """A table as one line per row, cells joined with « | »: a row stays readable when the table is cut."""
    filas = []
    for fila in tabla.iter("tr"):
        celdas = [_texto_de(c) for c in fila if c.tag in ("td", "th")]
        if any(celdas):
            filas.append(" | ".join(celdas))
    return filas


def _leer_version(elemento: Any, titulo_bloque: str) -> tuple[str, list[str], list[str], list[str]]:
    """(rótulo, párrafos del cuerpo, notas, clases de los párrafos) de un ``<version>``."""
    rotulo = ""
    parrafos: list[str] = []
    notas: list[str] = []
    clases: list[str] = []
    for hijo in elemento:
        if not isinstance(hijo.tag, str):
            continue
        if hijo.tag == "blockquote":
            # The BOE puts the amendment notes («Se modifica el apartado 2 por…») in a blockquote after the text.
            notas.extend(t for t in (_texto_de(p) for p in hijo.iter("p")) if t)
            continue
        if hijo.tag == "table":
            parrafos.extend(_filas_de_tabla(hijo))
            continue
        clase = str(hijo.get("class") or "")
        texto = _texto_de(hijo)
        if not texto:
            continue
        clases.append(clase)
        if clase.startswith("nota_pie"):
            notas.append(texto)
        elif clase == "articulo" and not rotulo:
            rotulo = texto
        else:
            parrafos.append(texto)
    return rotulo or titulo_bloque, parrafos, notas, clases


def _nivel(clases: Sequence[str]) -> int:
    for clase in clases:
        for prefijo, nivel in _NIVELES:
            # «seccion» must not match «subseccion», nor «titulo» the «capitulo_tit» suffix: test the prefix.
            if clase.startswith(prefijo):
                return nivel
    return _NIVEL_DESCONOCIDO


def numero_de_articulo(titulo: str) -> str | None:
    """«Artículo 7 bis» → «7 bis»; None for a provision, a heading or anything that is not a numbered article."""
    m = _NUMERO_ARTICULO.match(titulo.strip())
    if m is None:
        return None
    return f"{m.group(1)} {m.group(2).lower().replace('á', 'a')}" if m.group(2) else m.group(1)


def leer_norma(xml: bytes, *, boe_id: str, titulo: str = "", sigla: str = "", actualizada: str | None = None) -> Norma:
    """Parse the API's consolidated text. Raises ``ValueError`` when it is not that document."""
    from lxml import etree  # ty: ignore[missing-module-member]  # compiled module without stubs

    # No entities, no network, no DTD: the file comes from outside the process.
    analizador = etree.XMLParser(resolve_entities=False, no_network=True, dtd_validation=False, huge_tree=True)
    try:
        raiz = etree.fromstring(xml, parser=analizador)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"{boe_id}: XML mal formado ({exc}).") from exc
    elementos = raiz.findall(".//bloque[@id]")
    if raiz.tag != "response" or not elementos:
        raise ValueError(f"{boe_id}: no es el texto consolidado por bloques de la API del BOE.")

    bloques: list[Bloque] = []
    # The headings above the current block: (level, text), outermost first.
    pila: list[tuple[int, str]] = []
    for orden, elemento in enumerate(elementos):
        bloque_id = str(elemento.get("id"))
        tipo = str(elemento.get("tipo") or "")
        titulo_bloque = _limpio(str(elemento.get("titulo") or ""))
        crudas = []
        for v in elemento.findall("version"):
            desde = _fecha(v.get("fecha_vigencia")) or _fecha(v.get("fecha_publicacion"))
            if desde is None:
                continue
            rotulo, parrafos, notas, clases = _leer_version(v, titulo_bloque)
            crudas.append((desde, v, rotulo, parrafos, notas, clases))
        # Stable sort by the date in force: the API lists them in that order, but nothing promises it.
        crudas.sort(key=lambda c: c[0])
        versiones = tuple(
            Version(
                id_norma=str(v.get("id_norma") or ""),
                publicada=_fecha(v.get("fecha_publicacion")) or desde,
                vigente_desde=desde,
                vigente_hasta=crudas[i + 1][0] if i + 1 < len(crudas) else None,
                rotulo=rotulo,
                parrafos=tuple(parrafos),
                notas=tuple(notas),
            )
            for i, (desde, v, rotulo, parrafos, notas, _clases) in enumerate(crudas)
        )
        if tipo == "encabezado":
            if crudas:
                *_, rotulo, parrafos, _notas, clases = crudas[-1]
                nivel = _nivel(clases)
                encabezado = ". ".join(p.rstrip(".") for p in parrafos) or titulo_bloque
                pila = [(n, t) for n, t in pila if n < nivel]
                pila.append((nivel, encabezado))
        elif tipo == "precepto" and titulo_bloque.lower().startswith("disposici"):
            # Provisions close the articulated text: the last chapter's heading is not theirs.
            pila = []
        bloques.append(
            Bloque(
                bloque_id=bloque_id,
                tipo=tipo,
                titulo=titulo_bloque,
                orden=orden,
                jerarquia=tuple(t for _n, t in pila) if tipo == "precepto" else (),
                versiones=versiones,
                numero=numero_de_articulo(titulo_bloque) if tipo == "precepto" else None,
            )
        )
    return Norma(boe_id=boe_id, bloques=tuple(bloques), titulo=titulo, sigla=sigla, actualizada=actualizada)


# --------------------------------------------------------------------------------------------------------------
# Cutting a version into fragments
# --------------------------------------------------------------------------------------------------------------


def _partir_parrafo(parrafo: str, maximo: int) -> Iterator[str]:
    """A paragraph longer than a fragment, cut at sentence ends (at words when one sentence is too long)."""
    actual = ""
    for frase in _FRASE.split(parrafo):
        while len(frase) > maximo:
            corte = frase.rfind(" ", 0, maximo)
            corte = corte if corte > 0 else maximo
            if actual:
                yield actual
                actual = ""
            yield frase[:corte]
            frase = frase[corte:].lstrip()
        if actual and len(actual) + 1 + len(frase) > maximo:
            yield actual
            actual = frase
        else:
            actual = f"{actual} {frase}".strip()
    if actual:
        yield actual


def cabecera(norma: Norma, version: Version) -> str:
    """The line every fragment starts with: its norm and its article, «LIRPF · Artículo 68. Deducciones».

    Short on purpose. With the heading path in it (título, capítulo…) the fragments of a whole chapter start
    alike and the embedding tells them apart worse: measured, the short header puts the right article first in
    56 % of the benchmark's queries and the long one in 47 %. The path travels in the fragment's metadata.
    """
    return " · ".join(p for p in (norma.nombre_corto, version.rotulo.rstrip(".")) if p)


def trocear(norma: Norma, bloque: Bloque, version: Version, *, maximo: int = MAX_CARACTERES) -> list[Trozo]:
    """One version of a block as fragments of at most ``maximo`` characters of body, cut between paragraphs.

    Every fragment starts with :func:`cabecera`, so the embedding and the lexical index see the norm and the
    article's title with each piece of its body. A numbered apartado («1.», «2.») starts a new
    fragment when the current one is already half full, so an apartado is rarely split from its number.
    """
    grupos: list[tuple[list[str], list[str]]] = []  # (paragraphs, apartados covered)
    parrafos: list[str] = []
    cubiertos: list[str] = []
    largo = 0
    apartado: str | None = None

    def cerrar() -> None:
        nonlocal parrafos, cubiertos, largo
        if parrafos:
            grupos.append((parrafos, cubiertos))
        parrafos, cubiertos, largo = [], [], 0

    for parrafo in version.parrafos:
        m = _APARTADO.match(parrafo)
        if m is not None:
            apartado = m.group(1)
            if largo > maximo // 2:
                cerrar()
        for pieza in [parrafo] if len(parrafo) <= maximo else _partir_parrafo(parrafo, maximo):
            if parrafos and largo + 1 + len(pieza) > maximo:
                cerrar()
            parrafos.append(pieza)
            largo += len(pieza) + 1
            if apartado is not None and apartado not in cubiertos:
                cubiertos.append(apartado)
    cerrar()

    trozos = []
    for cuerpo_parrafos, apartados in grupos:
        cuerpo = "\n".join(cuerpo_parrafos)
        trozos.append(
            Trozo(
                texto=f"{cabecera(norma, version)}\n{cuerpo}",
                cuerpo=cuerpo,
                apartados=tuple(apartados),
            )
        )
    return trozos


# --------------------------------------------------------------------------------------------------------------
# References between articles
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Remision:
    articulo: str  # «23», «7 bis»
    norma: str | None = None  # None: the same norm; else the other norm as the text names it («Ley 37/1992»)


def remisiones(texto: str, *, propio: str | None = None) -> list[Remision]:
    """The articles ``texto`` refers to, in order of appearance and without repeats.

    A heuristic over the wording of Spanish laws: «artículo(s) N[, M y K]» followed, within the same clause, by
    «de la Ley X/AAAA» (or a decree, a regulation…) points at that norm; otherwise at the norm itself. ``propio``
    is the number of the article being read, left out of its own list.
    """
    vistas: dict[tuple[str, str | None], Remision] = {}
    for m in _REMISION.finditer(texto):
        otra = _OTRA_NORMA.match(texto[m.end() : m.end() + 220])
        norma = _limpio(otra.group(1)) if otra else None
        for n in _NUMERO_SUELTO.finditer(m.group(1)):
            numero = f"{n.group(1)} {n.group(2).lower().replace('á', 'a')}" if n.group(2) else n.group(1)
            if norma is None and numero == propio:
                continue
            vistas.setdefault((numero, norma), Remision(articulo=numero, norma=norma))
    return list(vistas.values())


__all__ = [
    "MAX_CARACTERES",
    "Bloque",
    "Norma",
    "Remision",
    "Trozo",
    "Version",
    "cabecera",
    "es_xml_de_bloques",
    "leer_norma",
    "numero_de_articulo",
    "remisiones",
    "trocear",
]
