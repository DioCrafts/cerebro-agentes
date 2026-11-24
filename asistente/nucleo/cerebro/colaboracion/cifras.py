"""Figures in Spanish text: which numbers are figures, which are not, and how precisely each one was written.

Both sides of the figures gate read numbers through this module:

* a specialist's TOOL output (:func:`extraer_cifras`): every figure a tool produced becomes a ``Cifra`` tied to that
  tool and to its ledger fragment, so the brain's final answer can be checked against it;
* the brain's final ANSWER and the USER's own messages (:func:`escanear`): every number is classified, and only the
  real figures are checked by the gate (``puerta.py``).

Reading rules (es-ES first, because every text here is Spanish):

* ``1.234,56`` / ``1 234,56`` / ``1234,56``: a dot or a space groups thousands, a comma marks the decimals. A
  non-breaking or thin space always groups; a plain space only when decimals or a unit follow (``en 2 300 casos``
  is read as two numbers, ``2 300 €`` as one);
* ``1.234`` is one thousand two hundred and thirty-four and ``1,234`` is one point two three four: the en-US reading
  is used only when it is the ONLY possible one — ``1,234.56``, ``1,234,567``, ``12.5``, ``3.45``, ``0.234`` (a dot
  not followed by exactly three digits, or after a leading zero, cannot group thousands);
* sign: ``-150,00 €`` / ``−150`` only after a space, a bracket, ``:`` or the start (``2024-2025``, ``10-15 %`` are
  not negative);
* scale: «1,2 millones», «3 mil», «mil millones», ``1,2 M€``, ``3 k€``;
* unit (``contrato.Unidad``): ``€`` / EUR / euros, then «al mes» «/mes» «mensuales» «cada mes» (→ ``EUR/mes``) or
  «al año» «/año» «anuales» (→ ``EUR/año``); ``%`` / «por ciento» / «puntos porcentuales»; kWh; kW; días; meses;
  años; unidades. A unit glued to the digits (``12%``, ``3kW``, ``200kWh``) counts.

NOT figures (returned with the reason, so the gate can show what it skipped and why): citations ``[F3]``, URLs,
IBANs, postcodes, dates (``15/03/2025``, ``2025-03-15``, «15 de marzo de 2025», «marzo de 2025», ``15/03``,
«del 01-11 al 31-10»), times, year ranges (``2024-2025``), norms («Ley 35/2006», «Real Decreto 439/2007», «Orden
HAC/1234/2025», ``35/2006``), article-like references («art. 68.1», «artículos 19 y 20», «modelo 100», «casilla
0505», «disposición adicional 4.ª»), ordinals («1.º», «3.ª», list markers), phone numbers, identifiers (``BOE-A-2006-20764``, ``V0058-25``,
``F3``, NIFs), bare years (an integer 1900–2100 without unit, grouping or periodicity) and bare small counts (an
integer of absolute value ≤ 10 without unit: «tus 3 recibos»). A year or a count WITH a unit is a figure
(``2025 €``, ``3 meses``).

Every scan is linear in the text (one pass per pattern, no backtracking blow-up: the only nested quantifiers are
atomic). Money is ``Decimal`` everywhere; nothing here uses floats.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
import re
from typing import Literal, get_args

from asistente.nucleo.cerebro.contrato import Cifra, Unidad

TipoNumero = Literal[
    "cifra",
    "cita",
    "url",
    "identificador",
    "codigo_postal",
    "fecha",
    "hora",
    "rango_años",
    "norma",
    "articulo",
    "telefono",
    "ordinal",
    "año",
    "numero_pequeno",
]

# Spanish reason of each kind of ignored number (for the gate's trace).
MOTIVOS: dict[str, str] = {
    "cita": "cita",
    "url": "dirección web",
    "identificador": "identificador",
    "codigo_postal": "código postal",
    "fecha": "fecha",
    "hora": "hora",
    "rango_años": "periodo de años",
    "norma": "número de norma",
    "articulo": "referencia normativa",
    "telefono": "teléfono",
    "ordinal": "ordinal",
    "año": "año",
    "numero_pequeno": "recuento pequeño",
}

UNIDADES: tuple[str, ...] = get_args(Unidad)
FAMILIA_EUR = frozenset({"EUR", "EUR/mes", "EUR/año"})
_SUFIJO_UNIDAD: dict[str, str] = {
    "EUR": " €",
    "EUR/mes": " €/mes",
    "EUR/año": " €/año",
    "%": " %",
    "kWh": " kWh",
    "kW": " kW",
    "dias": " días",
    "meses": " meses",
    "años": " años",
    "unidades": " unidades",
}
MAX_LITERAL = 80
MAX_CONCEPTO = 200


@dataclass(frozen=True)
class Numero:
    """One number found in a text, classified.

    For a figure (``tipo == "cifra"``): ``valor`` = sign × ``mantisa`` × ``escala``; ``decimales`` is how many
    decimals it was WRITTEN with (the gate compares to that precision), ``unidad`` its unit when it had one and
    ``periodico`` whether «al mes / al año» followed it without a currency. For any other kind ``valor`` is None.
    """

    inicio: int
    fin: int
    literal: str
    tipo: TipoNumero
    valor: Decimal | None = None
    mantisa: Decimal | None = None
    decimales: int = 0
    escala: Decimal = Decimal(1)
    unidad: Unidad | None = None
    periodico: bool = False

    @property
    def es_cifra(self) -> bool:
        return self.tipo == "cifra"

    @property
    def motivo(self) -> str:
        return MOTIVOS.get(self.tipo, self.tipo)


# --------------------------------------------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------------------------------------------

# One-to-one (same length) accent folding: positions found in the folded text are positions in the original. «ñ» is
# kept («año» is not «ano»).
_PLIEGUE = str.maketrans("áéíóúüàèìòùâêîôûÁÉÍÓÚÜÀÈÌÒÙÂÊÎÔÛ", "aeiouuaeiouaeiouAEIOUUAEIOUAEIOU")


def plano(texto: str) -> str:
    """``texto`` lowercased and without accents, with exactly the same length (positions are preserved)."""
    plegado = texto.translate(_PLIEGUE)
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in plegado)


def formatear_valor(valor: Decimal, unidad: str | None = None) -> str:
    """``valor`` written the Spanish way with the decimals it carries (1234.5 → «1.234,5»), plus its unit."""
    try:
        texto = format(valor, "f")
    except ValueError, InvalidOperation:  # pragma: no cover - a Decimal always formats
        texto = str(valor)
    signo = ""
    if texto.startswith("-"):
        signo, texto = "−", texto[1:]
    entero, _, decimales = texto.partition(".")
    grupos: list[str] = []
    while len(entero) > 3:
        grupos.insert(0, entero[-3:])
        entero = entero[:-3]
    grupos.insert(0, entero)
    cuerpo = ".".join(grupos) + ("," + decimales if decimales else "")
    return f"{signo}{cuerpo}{_SUFIJO_UNIDAD.get(unidad or '', '')}"


def coincide(numero: Numero, valor: Decimal) -> bool:
    """Is ``valor`` what ``numero`` shows, at the precision it was written with?

    True when ``valor`` (scaled like ``numero``) rounded half-up OR truncated to ``numero.decimales`` decimals equals
    what is written: «1.234 €» shows 1234,40 (rounded) and 1234,56 (truncated), never 1235,50; «1,2 millones» shows
    1.234.567. Signs are ignored: prose carries them in words («ahorras 150 €» for a difference of −150,00 €).
    """
    if numero.mantisa is None:
        return False
    try:
        paso = Decimal(1).scaleb(-numero.decimales)
        escalado = abs(valor) / numero.escala
        escrito = abs(numero.mantisa)
        return escalado.quantize(paso, ROUND_HALF_UP) == escrito or escalado.quantize(paso, ROUND_DOWN) == escrito
    except InvalidOperation:  # beyond the decimal context's precision: not the same figure
        return False


def unidades_compatibles(escrita: str | None, de_cifra: str | None) -> bool:
    """Can a number written with unit ``escrita`` be the figure whose unit is ``de_cifra``?

    No unit on either side: yes (the prose may drop it, or the tool did not say). Same unit: yes. A plain «€» in the
    answer may be a monthly or yearly amount and vice versa (the periodicity may be said elsewhere in the sentence),
    but «€/mes» and «€/año» never match each other, and no money matches a percentage or an energy.
    """
    if escrita is None or de_cifra is None or escrita == de_cifra:
        return True
    return escrita in FAMILIA_EUR and de_cifra in FAMILIA_EUR and "EUR" in (escrita, de_cifra)


def familia(unidad: str | None) -> str | None:
    """The unit family a figure can be added within (money of any periodicity is one family)."""
    if unidad is None:
        return None
    return "EUR" if unidad in FAMILIA_EUR else unidad


# --------------------------------------------------------------------------------------------------------------
# What is not a figure (masks, applied in this order; a later mask never overlaps an earlier one)
# --------------------------------------------------------------------------------------------------------------

_I = re.IGNORECASE
_MESES = r"(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|se?ptiembre|octubre|noviembre|diciembre)"
_DIA = r"(?:0?[1-9]|[12]\d|3[01])"
_MES_NUM = r"(?:0?[1-9]|1[0-2])"
_ANIO = r"(?:19|20)\d{2}"
# A unit right after a number: whatever carries it is a figure, never a reference.
_TRAS_UNIDAD = (
    r"(?!\s*(?:€|eur\b|euros?\b|%|por\s+ciento|kwh\b|kw\b|d[ií]as?\b|mes(?:es)?\b|a[ñn]os?\b|"
    r"mil\b|millones\b|mill[oó]n\b))"
)
# A reference number (68, 68.1, 4.ª, 7 bis, 7 h)), atomic so that a failing unit check never backtracks into it.
_REF = rf"(?>\d+(?:\.\d+)*)(?:\s?\.?[ºª°])?(?:\s?(?:bis|ter|quater|quinquies|sexies)\b)?(?:\s?[a-z]\))?{_TRAS_UNIDAD}"

_MASCARAS: tuple[tuple[TipoNumero, re.Pattern[str]], ...] = (
    ("cita", re.compile(r"\[\s*F\d+(?:\s*[,;y]\s*F\d+)*\s*\]", _I)),
    ("url", re.compile(r"\b(?:https?://|www\.)[^\s<>()\[\]]+", _I)),
    ("identificador", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")),  # IBAN
    ("codigo_postal", re.compile(r"\b(?:c\.\s?p\.|cp|c[oó]digo\s+postal)\s*:?\s*\d{5}\b", _I)),
    ("fecha", re.compile(rf"(?<![\w.,/-]){_ANIO}-{_MES_NUM}-{_DIA}(?![\w/-])")),
    ("fecha", re.compile(rf"(?<![\w.,/-]){_DIA}([/.-]){_MES_NUM}\1(?:{_ANIO}|\d{{2}})(?![\w/-]|[.,]\d)")),
    (
        "fecha",
        re.compile(
            rf"\b(?:{_DIA}(?:\s?\.?º)?\s+de\s+{_MESES}(?:\s+(?:de|del)\s+{_ANIO})?|{_MESES}\s+(?:de\s+|del\s+)?{_ANIO})\b",
            _I,
        ),
    ),
    ("fecha", re.compile(rf"(?<![\w.,/-]){_DIA}/{_MES_NUM}(?![\w/]|[.,]\d)")),
    # dd-mm without a year («[del 01-11 al 31-10]», a bill's period): only with a zero-padded part or right after
    # «del», «al», «desde», «hasta», «el» or «día», and never with a unit after it, so a range («10-15 %»,
    # «1-2 %», «de 3-5 años») stays a figure.
    (
        "fecha",
        re.compile(
            rf"(?<![\w.,/-])(?:0[1-9]-{_MES_NUM}|{_DIA}-0[1-9]"
            r"|(?:(?<=\bdel )|(?<=\bal )|(?<=\bdesde )|(?<=\bhasta )|(?<=\bel )|(?<=\bdia )|(?<=\bdía ))"
            rf"{_DIA}-{_MES_NUM})(?![\w/-]|[.,]\d){_TRAS_UNIDAD}",
            _I,
        ),
    ),
    ("hora", re.compile(r"(?<![\w.,:])(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?![\w:])(?:\s?h\b)?", _I)),
    ("hora", re.compile(r"(?<![\w.,:])(?:[01]?\d|2[0-3])\s?h(?:oras)?\s?[0-5]\d\b", _I)),
    ("rango_años", re.compile(rf"(?<![\w.,/]){_ANIO}\s?[-–/]\s?(?:{_ANIO}|\d{{2}})(?![\w/]|[.,]\d)")),
    (
        "norma",
        re.compile(
            r"\b(?:ley(?:\s+org[aá]nica)?|real\s+decreto(?:[\s-]+ley)?|r\.?\s?d\.?(?:\s?-?\s?ley|\s?l)?|"
            r"decreto(?:[\s-]+ley)?|orden(?:\s+ministerial)?|directiva|reglamento(?:\s+(?:\(ue\)|ue|delegado))?|"
            r"resoluci[oó]n|instrucci[oó]n|circular|sentencia|stc|sts|stjue|auto|consulta(?:\s+vinculante)?)"
            r"\s+(?:(?:n\.?\s?º|n[uú]m\.?|n[uú]mero)\s*)?(?:\(ue\)\s*)?(?:[a-z]{2,6}/)?\d+(?:/\d+)+(?:/[a-z]{1,4})?\b",
            _I,
        ),
    ),
    ("norma", re.compile(rf"(?<![\w/.,])\d{{1,4}}/{_ANIO}(?:/[A-Za-z]{{1,4}})?(?![\w/])")),
    (
        "articulo",
        re.compile(
            r"\b(?:art(?:[íi]culo)?s?\.?|apartados?|apdos?\.|p[áa]rrafos?|letras?|n[úu]meros?|n[úu]m\.|n\.?\s?º|"
            r"disposici[óo]n(?:es)?\s+(?:adicional|transitoria|final|derogatoria)(?:es)?|cap[íi]tulos?|t[íi]tulos?|"
            r"secci[óo]n(?:es)?|anexos?|casillas?|modelos?|normas?|reglas?|ep[íi]grafes?|cl[áa]usulas?|puntos?|l[íi]neas?)"
            rf"\s*{_REF}(?:\s*(?:,|y|e|o|u|a|al|-|–)\s*{_REF})*",
            _I,
        ),
    ),
    (
        "telefono",
        re.compile(
            r"(?<![\w.,+])(?:\+\s?34[\s.-]?)?(?:[6789]\d{2}(?:[\s.-]\d{3}){2}|[6789]\d{2}(?:[\s.-]\d{2}){3}|[6789]\d[\s.-]\d{3}(?:[\s.-]\d{2}){2}"
            r"|[6789]\d(?:[\s.-]\d{2}){3}|[6789]\d{8})"
            r"(?![\w]|[.,]\d)(?!\s*(?:€|eur\b|euros?\b|%|kwh\b|kw\b))",
            _I,
        ),
    ),
    ("ordinal", re.compile(r"(?m)^[ \t]*(?:[-*•>][ \t]*)?\d{1,2}[.)](?=[ \t])")),
    ("ordinal", re.compile(r"(?<![\w.,])\d{1,3}\s?\.?\s?[ºª°](?!\w)")),
    ("ordinal", re.compile(r"(?<![\w.,])\d{1,3}(?:er|era|ero|do|da|ro|ra|to|ta|vo|va|mo|ma|no|na)\b", _I)),
    (
        "identificador",
        re.compile(
            r"(?<![\w-])(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+(?![\w-])"
            r"|(?<![\w.,])[A-Za-z]+\d[A-Za-z0-9]*(?!\w)"
            r"|(?<![\w.,])\d{8}[A-Za-z](?!\w)"
        ),
    ),
)

# --------------------------------------------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------------------------------------------

_NUMERO_RE = re.compile(
    r"(?<![\w.,])(?:"
    r"(?P<es>[1-9]\d{0,2}(?:\.\d{3})+(?:,\d+)?)(?![\d])"  # 1.234 / 1.234,56
    r"|(?P<en>[1-9]\d{0,2}(?:,\d{3})+\.\d+)"  # 1,234.56
    r"|(?P<en2>[1-9]\d{0,2}(?:,\d{3}){2,})(?![\d.,])"  # 1,234,567
    r"|(?P<sp>[1-9]\d{0,2}(?:[   ]\d{3})+(?:,\d+)?)(?!\d)"  # 1 234,56
    r"|(?P<pt>\d+\.\d+)"  # 12.5 (only reading possible: en decimal)
    r"|(?P<pl>\d+(?:,\d+)?)"  # 1234 / 1234,56
    r")"
)
_ESCALA_RE = re.compile(
    r"[   ]?(?:(?P<palabra>mil\s+millones|millones|mill[oó]n|mil(?![a-zñ]))(?P<de>\s+de)?"
    r"|(?-i:(?P<simbolo>[Mk]))(?=\s?(?:€|EUR\b)))",
    _I,
)
_UNIDAD_RE = re.compile(
    r"[   ]?(?P<u>€|eur(?:os?)?\b|euros?\b|%|por\s?ciento\b|por\s+cien\b|puntos?\s+porcentuales\b|"
    r"kwh\b|kw\b|d[íi]as?\b|mes(?:es)?\b|a[ñn]os?\b|unidad(?:es)?\b)",
    _I,
)
_PERIODO_RE = re.compile(
    r"\s?(?:(?:brutos?|netos?)\s+)?(?:"
    r"(?P<mes>/\s?mes\b|al\s+mes\b|mensual(?:es)?\b|por\s+mes\b|cada\s+mes\b|/\s?mensual\b)"
    r"|(?P<anio>/\s?a[ñn]o\b|al\s+a[ñn]o\b|anual(?:es)?\b|por\s+a[ñn]o\b|cada\s+a[ñn]o\b|/\s?anual\b))",
    _I,
)
_MONEDA_ANTES_RE = re.compile(r"(?:€|\bEUR|\beur)[   ]?$")
_SIGNOS = "-−+"
_ANTES_DE_SIGNO = " \t\n([{:;=¿¡«\"'"
_ESCALAS = {"mil millones": Decimal(10) ** 9, "millones": Decimal(10) ** 6, "millon": Decimal(10) ** 6}


def _unidad_de(token: str) -> Unidad | None:
    t = plano(token).replace(" ", "")
    if t in ("€", "eur", "euro", "euros"):
        return "EUR"
    if t in ("%", "porciento", "porcien") or t.startswith("punto"):
        return "%"
    if t == "kwh":
        return "kWh"
    if t == "kw":
        return "kW"
    if t.startswith("dia"):
        return "dias"
    if t.startswith("mes"):
        return "meses"
    if t.startswith("año") or t.startswith("ano"):
        return "años"
    if t.startswith("unidad"):
        return "unidades"
    return None


def _leer(m: re.Match[str]) -> tuple[Decimal, int, bool]:
    """(mantissa, written decimals, grouped) of a ``_NUMERO_RE`` match."""
    if m.group("es"):
        entero, _, dec = m.group("es").partition(",")
        entero, agrupado = entero.replace(".", ""), True
    elif m.group("en"):
        entero, _, dec = m.group("en").rpartition(".")
        entero, agrupado = entero.replace(",", ""), True
    elif m.group("en2"):
        entero, dec, agrupado = m.group("en2").replace(",", ""), "", True
    elif m.group("sp"):
        entero, _, dec = m.group("sp").partition(",")
        entero, agrupado = re.sub(r"[   ]", "", entero), True
    elif m.group("pt"):
        entero, _, dec = m.group("pt").partition(".")
        agrupado = False
    else:
        entero, _, dec = m.group("pl").partition(",")
        agrupado = False
    return Decimal(f"{entero}.{dec}" if dec else entero), len(dec), agrupado


def _mascaras(texto: str) -> list[tuple[int, int, TipoNumero]]:
    ocupado = bytearray(len(texto))
    spans: list[tuple[int, int, TipoNumero]] = []
    for tipo, patron in _MASCARAS:
        for m in patron.finditer(texto):
            ini, fin = m.span()
            if fin <= ini or any(ocupado[ini:fin]):
                continue
            ocupado[ini:fin] = b"\x01" * (fin - ini)
            spans.append((ini, fin, tipo))
    spans.sort()
    return spans


def escanear(texto: str) -> list[Numero]:
    """Every number of ``texto``, classified (figures and ignorables), in reading order."""
    resultado: list[Numero] = [
        Numero(inicio=i, fin=f, literal=texto[i:f].strip()[:MAX_LITERAL], tipo=t) for i, f, t in _mascaras(texto)
    ]
    ocupado = bytearray(len(texto))
    for numero in resultado:
        ocupado[numero.inicio : numero.fin] = b"\x01" * (numero.fin - numero.inicio)
    pos = ultimo = 0
    while True:
        m = _NUMERO_RE.search(texto, pos)
        if m is None:
            break
        ini, fin = m.span()
        if any(ocupado[ini:fin]):
            pos = fin
            continue
        hallado, pos = _numero(texto, m, ocupado, ultimo)
        if hallado is not None:
            resultado.append(hallado)
            ultimo = hallado.fin
    resultado.sort(key=lambda n: (n.inicio, n.fin))
    return _rangos(texto, resultado)


_ENTRE_RANGO_RE = re.compile(r"\s?[-–]\s?|\s+(?:y|a|o)\s+", _I)


def _rangos(texto: str, numeros: list[Numero]) -> list[Numero]:
    """«10-15 %», «entre 300 y 400 €»: the first number of a range is a figure with the second one's unit."""
    for i in range(len(numeros) - 1, 0, -1):
        primero, segundo = numeros[i - 1], numeros[i]
        if (
            primero.tipo not in ("cifra", "numero_pequeno")
            or primero.unidad is not None
            or segundo.tipo != "cifra"
            or segundo.unidad is None
            or not _ENTRE_RANGO_RE.fullmatch(texto, primero.fin, segundo.inicio)
        ):
            continue
        m = _NUMERO_RE.fullmatch(texto, primero.inicio, primero.fin)
        if m is None:
            continue
        mantisa, decimales, _agrupado = _leer(m)
        numeros[i - 1] = Numero(
            inicio=primero.inicio,
            fin=primero.fin,
            literal=primero.literal,
            tipo="cifra",
            valor=mantisa * segundo.escala,
            mantisa=mantisa,
            decimales=decimales,
            escala=segundo.escala,
            unidad=segundo.unidad,
        )
    return numeros


def _numero(texto: str, m: re.Match[str], ocupado: bytearray, desde: int) -> tuple[Numero | None, int]:
    """Classify one match (``desde``: where the previous number ended); returns it and where to go on."""
    ini, fin = m.span()
    mantisa, decimales, agrupado = _leer(m)
    if m.group("sp") and " " in m.group("sp") and "," not in m.group("sp"):
        # A plain space groups only when a unit follows: «2 300 €» is one figure, «en 2 300 casos» two numbers.
        if not _UNIDAD_RE.match(texto, fin) and not _ESCALA_RE.match(texto, fin):
            fin = ini + m.group("sp").index(" ")
            mantisa, decimales, agrupado = Decimal(texto[ini:fin]), 0, False
    # Glued letters that are not a unit: an identifier («12345678Z», «2B»).
    if fin < len(texto) and texto[fin].isalpha() and not _UNIDAD_RE.match(texto, fin):
        if not _ESCALA_RE.match(texto, fin):
            final = fin
            while final < len(texto) and (texto[final].isalnum() or texto[final] in "-_"):
                final += 1
            return Numero(inicio=ini, fin=final, literal=texto[ini:final][:MAX_LITERAL], tipo="identificador"), final
    inicio = ini
    negativo = False
    if ini > 0 and texto[ini - 1] in _SIGNOS and (ini == 1 or texto[ini - 2] in _ANTES_DE_SIGNO):
        inicio, negativo = ini - 1, texto[ini - 1] != "+"
    escala = Decimal(1)
    unidad: Unidad | None = None
    final = fin
    e = _ESCALA_RE.match(texto, final)
    if e is not None:
        if e.group("simbolo"):
            escala = Decimal(10) ** (6 if e.group("simbolo") == "M" else 3)
            final = e.end()
        else:
            palabra = plano(" ".join(e.group("palabra").split()))
            escala = _ESCALAS.get(palabra, Decimal(1000))
            final = e.end("palabra")
            u = _UNIDAD_RE.match(texto, e.end())
            if u is not None and _unidad_de(u.group("u")) == "EUR":
                unidad, final = "EUR", u.end()
    if unidad is None:
        u = _UNIDAD_RE.match(texto, final)
        if u is not None:
            unidad, final = _unidad_de(u.group("u")), u.end()
    if unidad is None:
        antes = _MONEDA_ANTES_RE.search(texto, max(desde, inicio - 5), inicio)
        if antes is not None and not any(ocupado[antes.start() : inicio]):
            unidad, inicio = "EUR", antes.start()
    periodico = False
    if unidad in (None, "EUR"):
        p = _PERIODO_RE.match(texto, final)
        if p is not None:
            periodico = True
            if unidad == "EUR":
                unidad = "EUR/mes" if p.group("mes") else "EUR/año"
            final = p.end()
    literal = texto[inicio:final].strip()[:MAX_LITERAL]
    if unidad is None and not periodico and escala == 1 and decimales == 0 and not agrupado and not negativo:
        if 1900 <= mantisa <= 2100:
            return Numero(inicio=ini, fin=fin, literal=texto[ini:fin], tipo="año", valor=None), fin
        if mantisa <= 10:
            return Numero(inicio=ini, fin=fin, literal=texto[ini:fin], tipo="numero_pequeno"), fin
    valor = mantisa * escala * (-1 if negativo else 1)
    return (
        Numero(
            inicio=inicio,
            fin=final,
            literal=literal,
            tipo="cifra",
            valor=valor,
            mantisa=mantisa,
            decimales=decimales,
            escala=escala,
            unidad=unidad,
            periodico=periodico,
        ),
        final,
    )


def numeros_de(texto: str) -> list[Numero]:
    """The figures of ``texto`` only."""
    return [n for n in escanear(texto) if n.es_cifra]


# Spanish cardinals from 0 to 199, for the figures a source writes in words («menores de cuarenta años»).
_UNIDADES_LETRA: dict[str, int] = {
    "cero": 0,
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
}
_SUELTOS_LETRA: dict[str, int] = {
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiun": 21,
    "veintiuno": 21,
    "veintiuna": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
}
_DECENAS_LETRA: dict[str, int] = {
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
}


def _alternativas(palabras: dict[str, int]) -> str:
    return "|".join(sorted(palabras, key=len, reverse=True))


_HASTA_99 = (
    rf"(?:(?:{_alternativas(_DECENAS_LETRA)})(?:\s+y\s+(?:{_alternativas(_UNIDADES_LETRA)}))?"
    rf"|{_alternativas(_SUELTOS_LETRA)}|{_alternativas(_UNIDADES_LETRA)})"
)
# Matched on ``plano(texto)`` (lowercase, no accents, same positions): «cuarenta años», «treinta y cinco años»,
# «ciento veinte días», «cien por cien», «el cinco por ciento».
_EN_LETRAS_RE = re.compile(
    rf"(?<![\w-])(?P<n>cien|ciento\s+{_HASTA_99}|{_HASTA_99})"
    r"\s+(?P<u>a[ñn]os?|mes(?:es)?|dias?|por\s+ciento|por\s+cien)(?!\w)"
)


def _valor_en_letras(palabras: str) -> int:
    total = 0
    for palabra in palabras.split():
        if palabra in ("cien", "ciento"):
            total += 100
        elif palabra != "y":
            total += _DECENAS_LETRA.get(palabra) or _SUELTOS_LETRA.get(palabra) or _UNIDADES_LETRA.get(palabra, 0)
    return total


def numeros_en_letras(texto: str) -> list[Numero]:
    """The figures ``texto`` writes in words before a unit («menores de cuarenta años» → 40 años), 0 to 199.

    Only for the SOURCE side of the gate (a cited fragment): the BOE writes ages, terms and rates in words, and the
    answer's «40 años [F2]» is that literal. The answer itself is still read digits-only (:func:`escanear`).
    """
    hallados: list[Numero] = []
    for m in _EN_LETRAS_RE.finditer(plano(texto)):
        valor = Decimal(_valor_en_letras(m.group("n")))
        hallados.append(
            Numero(
                inicio=m.start(),
                fin=m.end(),
                literal=texto[m.start() : m.end()][:MAX_LITERAL],
                tipo="cifra",
                valor=valor,
                mantisa=valor,
                unidad=_unidad_de(m.group("u")),
            )
        )
    return hallados


def ignorables(texto: str) -> list[Numero]:
    """The numbers of ``texto`` that are not figures (dates, years, references…), with their reason."""
    return [n for n in escanear(texto) if not n.es_cifra]


# --------------------------------------------------------------------------------------------------------------
# Figures of a tool's output
# --------------------------------------------------------------------------------------------------------------

_DELIMITADORES = ".;\n(|"
_SOBRAN_FINAL = {
    "de",
    "del",
    "es",
    "son",
    "un",
    "una",
    "el",
    "la",
    "los",
    "las",
    "en",
    "por",
    "a",
    "al",
    "y",
    "o",
    "con",
    "total",
    "unos",
    "unas",
    "sera",
    "será",
    "seria",
    "sería",
    "queda",
    "quedan",
    "asciende",
    "sale",
    "salen",
    "resulta",
}
_SOBRAN_INICIO = {"y", "e", "o", "u", "de", "del", "el", "la", "los", "las", "un", "una", "al", "a", "en", "con", "que"}
_ANIO_RE = re.compile(r"(?<![\d.,])(?:19[9]\d|20\d{2}|2100)(?![\d.,])")


def _limpiar_concepto(texto: str) -> str:
    texto = re.sub(r"[*_`#>|«»\"“”\[\]]+", " ", texto)
    texto = re.sub(r"^\s*(?:[-•]\s*)+", "", texto)
    palabras = [p.strip(",-–") for p in texto.replace("=", " ").replace(":", " ").split() if p.strip(",-–")]
    while palabras and plano(palabras[-1]) in _SOBRAN_FINAL:
        palabras.pop()
    palabras = palabras[-8:]
    while palabras and plano(palabras[0]) in _SOBRAN_INICIO:
        palabras.pop(0)
    return " ".join(palabras)


def _concepto(texto: str, numero: Numero, desde: int) -> str:
    izquierda = texto[desde : numero.inicio]
    corte = max(izquierda.rfind(d) for d in _DELIMITADORES)
    concepto = _limpiar_concepto(izquierda[corte + 1 :])
    if concepto:
        return concepto
    derecha = texto[numero.fin : numero.fin + 120]
    cortes = [i for i in (derecha.find(d) for d in ".;\n),") if i >= 0]
    derecha = derecha[: min(cortes)] if cortes else derecha
    palabras = [p for p in re.sub(r"[*_`#>|«»\"“”]+", " ", derecha).split()]
    while palabras and plano(palabras[0]) in _SOBRAN_FINAL:
        palabras.pop(0)
    return " ".join(palabras[:8])


def extraer_cifras(
    texto: str,
    *,
    especialista: str,
    fragment_id: str | None,
    herramienta: str | None,
    primera: int = 1,
) -> list[Cifra]:
    """The figures of a tool's output as ``Cifra``, attributed to ``especialista`` and the fragment it is in.

    ``concepto`` is read from the words before the figure (back to the previous figure or the start of its clause:
    «Cuota mensual: 812,34 €» → «Cuota mensual»), or after it when nothing precedes; ``ejercicio`` is a year said in
    that concept («Cuota íntegra 2025: …»). Ids are provisional (``C<primera>``…): the collaboration runtime renumbers
    every figure of the turn so that ids are unique and stable.
    """
    cifras: list[Cifra] = []
    previo = 0
    anterior = ""
    for numero in escanear(texto):
        if not numero.es_cifra or numero.valor is None:
            continue
        concepto = _concepto(texto, numero, previo) or anterior or (herramienta or "cifra")
        segmento = texto[previo : numero.inicio]
        anios = _ANIO_RE.findall(segmento)
        ejercicio = int(anios[-1]) if anios else None
        cifras.append(
            Cifra(
                id=f"C{primera + len(cifras)}",
                especialista=especialista,
                concepto=concepto[:MAX_CONCEPTO],
                valor=numero.valor,
                unidad=numero.unidad,
                fragment_id=fragment_id,
                herramienta=herramienta[:80] if herramienta else None,
                ejercicio=ejercicio if ejercicio is not None and 1990 <= ejercicio <= 2100 else None,
                literal=numero.literal[:MAX_LITERAL],
            )
        )
        previo, anterior = numero.fin, concepto
    return cifras


def clave_de_cifra(cifra: Cifra) -> tuple[str, ...]:
    """What makes two ``Cifra`` the same figure (everything but the id)."""
    try:
        valor = format(cifra.valor.normalize(), "f")
    except InvalidOperation:  # pragma: no cover - finite Decimals always normalise
        valor = str(cifra.valor)
    return (
        cifra.especialista,
        cifra.concepto,
        valor,
        cifra.unidad or "",
        cifra.fragment_id or "",
        cifra.herramienta or "",
        str(cifra.ejercicio or ""),
        cifra.literal,
    )


def unicas(cifras: Sequence[Cifra]) -> list[Cifra]:
    """``cifras`` without repeats (the same figure under two ids keeps the first), order kept."""
    vistas: set[tuple[str, ...]] = set()
    resultado: list[Cifra] = []
    for cifra in cifras:
        clave = clave_de_cifra(cifra)
        if clave not in vistas:
            vistas.add(clave)
            resultado.append(cifra)
    return resultado


__all__ = [
    "FAMILIA_EUR",
    "MOTIVOS",
    "UNIDADES",
    "Numero",
    "TipoNumero",
    "clave_de_cifra",
    "coincide",
    "escanear",
    "extraer_cifras",
    "familia",
    "formatear_valor",
    "ignorables",
    "numeros_de",
    "numeros_en_letras",
    "plano",
    "unicas",
    "unidades_compatibles",
]
