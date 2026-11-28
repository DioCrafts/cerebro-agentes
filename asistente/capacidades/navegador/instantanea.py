"""The page as the mission agent's model sees it: Playwright's ARIA snapshot, reduced and made inert.

``page.aria_snapshot(mode="ai")`` returns the accessibility tree as indented text, each element with a reference
(``- button "Consultar" [ref=e12]``) that the ``aria-ref=`` selector engine resolves back to the element. That
text is what the page says about itself, so before it reaches a model:

* identifiers that look personal (CUPS, IBAN, NIF/NIE, e-mail, phone numbers, long digit runs, the name after a
  greeting) are masked. Dates and amounts are kept, unlike in the redacted skeleton of ``seguridad.py``: a
  mission often has to read a figure, and a script's diagnosis never does;
* it is cut to a budget of characters, whole lines only, and says so;
* only references of lines that were KEPT are valid targets: the model cannot act on what it was not shown.

Deterministic and independent of Playwright: every rule has a unit test.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from asistente.capacidades.navegador.personales import IDENTIFICADORES, MASCARA, SALUDO

REF = re.compile(r"\[ref=([a-z0-9]{1,16})\]")
REF_VALIDA = re.compile(r"^[a-z0-9]{1,16}$")
_NOMBRE = re.compile(r'^\s*-\s+(?P<rol>[a-z]+)(?:\s+"(?P<nombre>(?:[^"\\]|\\.)*)")?')
_URL = re.compile(r"^(\s*-\s+/url:\s*)(\S+)")
# The identifiers come from ``personales`` so the snapshot and the skeleton mask them alike. The last two
# patterns are this side's own: they catch a phone number and a long account or card number while leaving a date
# or an amount readable, which a mission usually needs.
_PERSONALES = (
    *IDENTIFICADORES,
    re.compile(r"(?<![\d/.,-])(?:\+\d{2}\s?)?[6-9]\d{2}[ .]?\d{3}[ .]?\d{3}(?!\d)(?![.,/-]\d)"),  # Spanish phone
    re.compile(r"(?<![\d/.,-])\d{10,}(?!\d)(?![.,/-]\d)"),  # account, card and reference numbers
)
_TRAS_SALUDO = re.compile(rf"\b({SALUDO})\b[,:]?\s+[^\[\"]*", re.IGNORECASE)
_ROLES = {
    "button": "el botón",
    "link": "el enlace",
    "textbox": "el campo",
    "searchbox": "el buscador",
    "combobox": "el desplegable",
    "listbox": "la lista",
    "checkbox": "la casilla",
    "radio": "la opción",
    "tab": "la pestaña",
    "menuitem": "la opción de menú",
    "cell": "la celda",
    "row": "la fila",
}
MAX_LINEA = 400
AVISO_RECORTE = "- (la página sigue: no se muestra el resto)"


@dataclass(frozen=True)
class Elemento:
    ref: str
    rol: str
    nombre: str


@dataclass(frozen=True)
class Instantanea:
    """The reduced snapshot: its text, the elements that may be targeted and whether it was cut."""

    texto: str
    elementos: dict[str, Elemento]
    recortada: bool = False

    def nombre_de(self, ref: str) -> str:
        """The element in Spanish, for the live step text (el botón «Consultar»); "" when unknown."""
        elemento = self.elementos.get(ref)
        if elemento is None:
            return ""
        rol = _ROLES.get(elemento.rol, "el elemento")
        return f"{rol} «{elemento.nombre}»" if elemento.nombre else rol


def redactar(linea: str) -> str:
    """Mask what looks personal in one snapshot line. References (``[ref=e12]``) and quotes are never touched."""
    salida = linea
    for patron in _PERSONALES:
        salida = patron.sub(MASCARA, salida)
    return _TRAS_SALUDO.sub(lambda m: f"{m.group(1)} {MASCARA} ", salida).rstrip()


def _sin_consulta(linea: str) -> str:
    """A link's URL without its query string and fragment (they may carry identifiers)."""
    encontrado = _URL.match(linea)
    if encontrado is None:
        return linea
    url = encontrado.group(2).split("?", 1)[0].split("#", 1)[0]
    return f"{encontrado.group(1)}{url}"


def preparar(crudo: str, *, maximo: int, enmascarar: bool = True) -> Instantanea:
    """Reduce a raw ARIA snapshot to at most ``maximo`` characters of whole lines."""
    lineas: list[str] = []
    elementos: dict[str, Elemento] = {}
    usado = 0
    recortada = False
    for cruda in (crudo or "").splitlines():
        if not cruda.strip():
            continue
        linea = _sin_consulta(cruda.rstrip())
        if enmascarar:
            linea = redactar(linea)
        if len(linea) > MAX_LINEA:
            linea = linea[: MAX_LINEA - 1] + "…"
        if usado + len(linea) + 1 > maximo:
            recortada = True
            break
        lineas.append(linea)
        usado += len(linea) + 1
        referencia = REF.search(linea)
        if referencia is not None:
            forma = _NOMBRE.match(linea)
            elementos[referencia.group(1)] = Elemento(
                ref=referencia.group(1),
                rol=forma.group("rol") if forma else "elemento",
                nombre=(forma.group("nombre") or "")[:80] if forma else "",
            )
    if recortada:
        lineas.append(AVISO_RECORTE)
    return Instantanea(texto="\n".join(lineas), elementos=elementos, recortada=recortada)


__all__ = ["AVISO_RECORTE", "MASCARA", "REF", "REF_VALIDA", "Elemento", "Instantanea", "preparar", "redactar"]
