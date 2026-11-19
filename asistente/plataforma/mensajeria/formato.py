"""Rendering the brain's answer for a chat: the channel's rich text, readable citations, one line of who was consulted,
the figures' provenance and the sources on demand, and parts that fit the channel.

The brain writes Markdown for the web app (headings, ``**bold**``, lists, tables, links, code). A chat renders far
less: SimpleX shows ``*bold*``, ``_italic_``, ``~strike~``, `` `code` `` and plain links. So:

* bold/italic/strike/code are translated to the channel's markers (``formato="simplex"``), kept as they are
  (``"markdown"``) or dropped (``"texto"``);
* a heading becomes a bold line, a list item a «•» line, a quote a «›» line, a rule a blank line;
* a table becomes one line per row («• Concepto: valor · Otro: valor») — never a grid of pipes on a phone;
* a link ``[texto](url)`` becomes «texto (url)»; HTML tags are dropped;
* ``[F2]`` citations stay as they are: short, readable, and ``/fuentes`` lists them.

:func:`partir` splits at the channel's ``max_texto`` on paragraph boundaries (then lines, sentences, words), never
in the middle of a word unless a single word is longer than the limit.
"""

from __future__ import annotations

from collections.abc import Sequence
import re
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover
    from asistente.nucleo.agent.agent import AgentCitation
    from asistente.nucleo.cerebro.contrato import InformeCerebro

Formato = Literal["simplex", "markdown", "texto"]

_NEGRITA = "\x01"
_CODIGO = "\x02"
_FILA_TABLA = re.compile(r"^\|.*\|$")
_SEPARADOR_TABLA = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_ENCABEZADO = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_VINETA = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_CITA = re.compile(r"^\s*>\s?(.*)$")
_REGLA = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_ENLACE = re.compile(r"!?\[([^\]\n]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_CODIGO_EN_LINEA = re.compile(r"`([^`\n]+)`")
_NEGRITA_MD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_CURSIVA_ASTERISCO = re.compile(r"(?<![*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![*\w])")
_TACHADO = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_HTML = re.compile(r"</?[a-zA-Z][^>\n]*>")
_SALTO_HTML = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _enlaces(texto: str) -> str:
    def sustituir(m: re.Match[str]) -> str:
        etiqueta, url = m.group(1).strip(), m.group(2).strip()
        if not etiqueta or etiqueta == url:
            return url
        return f"{etiqueta} ({url})"

    return _ENLACE.sub(sustituir, texto)


def _en_linea(texto: str, formato: Formato) -> str:
    """Inline markup of one line, translated to ``formato``."""
    codigos: list[str] = []

    def guardar(m: re.Match[str]) -> str:
        codigos.append(m.group(1))
        return f"{_CODIGO}{len(codigos) - 1}{_CODIGO}"

    texto = _CODIGO_EN_LINEA.sub(guardar, texto)
    texto = _SALTO_HTML.sub("\n", texto)
    texto = _HTML.sub("", texto)
    texto = _enlaces(texto)
    if formato == "texto":
        texto = _NEGRITA_MD.sub(lambda m: m.group(2), texto)
        texto = _CURSIVA_ASTERISCO.sub(lambda m: m.group(1), texto)
        texto = _TACHADO.sub(lambda m: m.group(1), texto)
    else:  # simplex
        texto = _NEGRITA_MD.sub(lambda m: f"{_NEGRITA}{m.group(2)}{_NEGRITA}", texto)
        texto = _CURSIVA_ASTERISCO.sub(lambda m: f"_{m.group(1)}_", texto)
        texto = _TACHADO.sub(lambda m: f"~{m.group(1)}~", texto)
        texto = texto.replace(_NEGRITA, "*")

    def restaurar(m: re.Match[str]) -> str:
        contenido = codigos[int(m.group(1))]
        return contenido if formato == "texto" else f"`{contenido}`"

    return re.sub(f"{_CODIGO}(\\d+){_CODIGO}", restaurar, texto)


def _celdas(fila: str) -> list[str]:
    return [c.strip() for c in fila.strip().strip("|").split("|")]


def _tabla(filas: list[str], formato: Formato) -> list[str]:
    """A Markdown table as one line per row («• Encabezado: valor · …»)."""
    datos = [_celdas(f) for f in filas if not _SEPARADOR_TABLA.match(f.strip())]
    if not datos:
        return []
    if len(datos) == 1:
        return [" · ".join(_en_linea(c, formato) for c in datos[0] if c)]
    cabecera, *cuerpo = datos
    salida: list[str] = []
    for fila in cuerpo:
        partes: list[str] = []
        for i, celda in enumerate(fila):
            if not celda:
                continue
            titulo = cabecera[i] if i < len(cabecera) else ""
            valor = _en_linea(celda, formato)
            partes.append(f"{_en_linea(titulo, formato)}: {valor}" if titulo else valor)
        if partes:
            salida.append("• " + " · ".join(partes))
    return salida


def a_canal(texto: str, formato: Formato) -> str:
    """The brain's Markdown answer, rendered for a channel that shows ``formato``."""
    if formato == "markdown":
        return texto.strip()
    salida: list[str] = []
    tabla: list[str] = []
    en_bloque = False

    def volcar_tabla() -> None:
        if tabla:
            salida.extend(_tabla(tabla, formato))
            tabla.clear()

    for linea in texto.replace("\r\n", "\n").split("\n"):
        limpia = linea.strip()
        if limpia.startswith("```"):
            volcar_tabla()
            en_bloque = not en_bloque
            continue
        if en_bloque:  # code block: its lines, as plain text
            salida.append(linea.rstrip())
            continue
        if _FILA_TABLA.match(limpia):
            tabla.append(limpia)
            continue
        volcar_tabla()
        if _REGLA.match(linea):
            salida.append("")
            continue
        if encabezado := _ENCABEZADO.match(linea):
            contenido = _en_linea(encabezado.group(1), "texto")
            salida.append(contenido if formato == "texto" else f"*{contenido}*")
            continue
        if vineta := _VINETA.match(linea):
            sangria = "  " if len(vineta.group(1)) >= 2 else ""
            salida.append(f"{sangria}• {_en_linea(vineta.group(2), formato)}")
            continue
        if cita := _CITA.match(linea):
            salida.append(f"› {_en_linea(cita.group(1), formato)}")
            continue
        salida.append(_en_linea(linea.rstrip(), formato))
    volcar_tabla()
    resultado = "\n".join(salida)
    return re.sub(r"\n{3,}", "\n\n", resultado).strip()


# --------------------------------------------------------------------------------------------------------------
# Splitting
# --------------------------------------------------------------------------------------------------------------


def _cortar(texto: str, maximo: int, separador: str) -> list[str]:
    """Greedy recombination of ``texto`` split by ``separador`` into pieces of at most ``maximo``."""
    if separador == "":
        return [texto[i : i + maximo] for i in range(0, len(texto), maximo)]
    if separador == " ":
        trozos = texto.split(" ")
    elif separador == "frases":
        trozos = re.split(r"(?<=[.!?…;:])\s+", texto)
    else:
        trozos = texto.split(separador)
    union = " " if separador in (" ", "frases") else separador
    siguiente = {"\n": "frases", "frases": " ", " ": ""}[separador]
    salida: list[str] = []
    actual = ""
    for trozo in trozos:
        piezas = [trozo] if len(trozo) <= maximo else _cortar(trozo, maximo, siguiente)
        for pieza in piezas:
            candidato = f"{actual}{union}{pieza}" if actual else pieza
            if len(candidato) <= maximo:
                actual = candidato
            else:
                if actual:
                    salida.append(actual)
                actual = pieza
    if actual:
        salida.append(actual)
    return salida


def partir(texto: str, maximo: int) -> list[str]:
    """``texto`` in parts of at most ``maximo`` characters, cut at paragraphs, then lines, sentences and words."""
    maximo = max(20, int(maximo))
    texto = texto.strip()
    if not texto:
        return []
    if len(texto) <= maximo:
        return [texto]
    partes: list[str] = []
    actual = ""
    for parrafo in re.split(r"\n{2,}", texto):
        parrafo = parrafo.strip()
        if not parrafo:
            continue
        trozos = [parrafo] if len(parrafo) <= maximo else _cortar(parrafo, maximo, "\n")
        for trozo in trozos:
            candidato = f"{actual}\n\n{trozo}" if actual else trozo
            if len(candidato) <= maximo:
                actual = candidato
            else:
                if actual:
                    partes.append(actual)
                actual = trozo
    if actual:
        partes.append(actual)
    return partes


# --------------------------------------------------------------------------------------------------------------
# Who was consulted, where each figure comes from, the sources
# --------------------------------------------------------------------------------------------------------------


def enumerar(nombres: Sequence[str]) -> str:
    """«A», «A y B», «A, B y C» — and «e» before an /i/ sound («Hipoteca e Impuestos»)."""
    lista = [n for n in nombres if n]
    if len(lista) <= 1:
        return "".join(lista)
    conjuncion = "e" if re.match(r"(?i)h?[ií](?![aeoáéó])", lista[-1]) else "y"
    return ", ".join(lista[:-1]) + f" {conjuncion} " + lista[-1]


def consultados(informe: InformeCerebro | None) -> str | None:
    """«Consulté a: Hipoteca y Impuestos.» — the specialists that reported in this turn (None when none did)."""
    if informe is None:
        return None
    nombres = list(dict.fromkeys(i.nombre for i in informe.informes if i.nombre))
    if not nombres:
        return None
    return f"Consulté a: {enumerar(nombres)}."


def _importe(literal: str) -> str:
    return " ".join(literal.split())


def texto_cifras(informe: InformeCerebro | None) -> str:
    """``/cifras``: every figure of the last answer and where it came from (the figures gate's attributions)."""
    puerta = informe.puerta if informe is not None else None
    if informe is None or puerta is None or not puerta.atribuciones:
        return "La última respuesta no tiene cifras calculadas que explicar."
    cifras = {c.id: c for r in informe.informes for c in r.cifras}
    nombres = {r.especialista: r.nombre for r in informe.informes}
    lineas = ["**De dónde sale cada cifra** de mi última respuesta:"]
    for atribucion in puerta.atribuciones[:30]:
        if atribucion.del_usuario:
            origen = "lo dijiste tú"
        else:
            cifra = cifras.get(atribucion.cifra_id or "")
            quien = nombres.get(atribucion.especialista or "", atribucion.especialista or "un especialista")
            origen = quien
            if cifra is not None:
                origen += f": {cifra.concepto}"
                if cifra.herramienta:
                    origen += f" (cálculo `{cifra.herramienta}`)"
            if atribucion.fragment_id:
                origen += f" [{atribucion.fragment_id}]"
        lineas.append(f"• {_importe(atribucion.literal)} — {origen}")
    return "\n".join(lineas)


def texto_fuentes(citas: Sequence[AgentCitation]) -> str:
    """``/fuentes``: the sources the last answer cites, by their ``[F#]``."""
    if not citas:
        return "La última respuesta no cita fuentes."
    lineas = ["**Fuentes** de mi última respuesta:"]
    for cita in citas[:20]:
        titulo = " ".join(cita.title.split())[:200]
        linea = f"• [{cita.fragment_id}] {titulo}"
        if cita.section:
            linea += f", {' '.join(cita.section.split())[:120]}"
        if cita.locator and cita.locator != cita.title:
            linea += f" — {cita.locator}"
        if cita.vigencia and cita.vigencia != "vigente":
            linea += f" (_{cita.vigencia}_)"
        lineas.append(linea)
    return "\n".join(lineas)


__all__ = ["Formato", "a_canal", "consultados", "enumerar", "partir", "texto_cifras", "texto_fuentes"]
