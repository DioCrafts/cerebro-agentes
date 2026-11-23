"""Deterministic extraction of dates, amounts and a few named fields from a message's visible text.

Every extracted value keeps its LITERAL text and a literal snippet (≤ 200 characters) of the line it was
read from: the snippet is a substring of the message, the literal is a substring of the snippet. Nothing
is guessed: a field that cannot be read with its text is left out.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import re

MAX_FRAGMENTO = 200

_MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}
_MES_RE = "|".join(sorted(_MESES, key=len, reverse=True))
_FECHA_RE = re.compile(
    r"(?<!\d)(?P<d1>\d{1,2})[/\-.](?P<m1>\d{1,2})[/\-.](?P<a1>\d{4})(?!\d)"
    r"|(?<!\d)(?P<a2>\d{4})-(?P<m2>\d{2})-(?P<d2>\d{2})(?!\d)"
    rf"|(?<!\d)(?P<d3>\d{{1,2}})\s+(?:de\s+)?(?P<m3>{_MES_RE})\.?(?:\s+(?:de(?:l)?\s+)?(?P<a3>\d{{4}}))?(?![a-záéíóú])",
    re.IGNORECASE,
)
_NUMERO = r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_IMPORTE_RE = re.compile(
    rf"(?<![\d.,])(?P<n1>{_NUMERO})\s?(?:€|eur(?:os?)?\b)|(?:€|\beur\b)\s?(?P<n2>{_NUMERO})(?![\d])",
    re.IGNORECASE,
)
_PLAZO_RE = re.compile(
    r"antes del?|hasta el|fecha l[ií]mite|vence(?:rá)? el|vencimiento|a m[aá]s tardar|no m[aá]s tarde del?|"
    r"[uú]ltimo d[ií]a|caduca(?:rá)? el|expira(?:rá)? el|plazo|tienes hasta|tiene hasta",
    re.IGNORECASE,
)
_TOTAL_RE = re.compile(r"(?<!sub)total|importe|a pagar|cargo|cobrad|adeud|pagad", re.IGNORECASE)
_POTENCIA_RE = re.compile(r"potencia[^\n]{0,40}?(?P<n>\d{1,2}(?:[.,]\d{1,3})?)\s?kw(?!h)", re.IGNORECASE)


@dataclass(frozen=True)
class Extraido:
    campo: str
    valor: str
    literal: str
    fragmento: str


def fragmento(texto: str, inicio: int, fin: int) -> str:
    """The line holding ``texto[inicio:fin]``, cut to ≤ 200 characters around it (literal)."""
    principio = texto.rfind("\n", 0, inicio) + 1
    final = texto.find("\n", fin)
    final = len(texto) if final < 0 else final
    if final - principio <= MAX_FRAGMENTO:
        return texto[principio:final].strip()
    margen = max(0, (MAX_FRAGMENTO - (fin - inicio)) // 2)
    a = max(principio, inicio - margen)
    b = min(final, a + MAX_FRAGMENTO)
    return texto[a:b].strip()


def _anio_probable(dia: int, mes: int, referencia: date) -> int | None:
    """A date without a year: the next one from 30 days before the message (a deadline is in the future)."""
    for anio in (referencia.year, referencia.year + 1):
        try:
            candidata = date(anio, mes, dia)
        except ValueError:
            return None
        if candidata >= referencia - timedelta(days=30):
            return anio
    return None


def fechas(texto: str, referencia: date) -> Iterator[tuple[date, re.Match[str]]]:
    for m in _FECHA_RE.finditer(texto):
        try:
            if m.group("d1"):
                yield date(int(m.group("a1")), int(m.group("m1")), int(m.group("d1"))), m
            elif m.group("a2"):
                yield date(int(m.group("a2")), int(m.group("m2")), int(m.group("d2"))), m
            else:
                dia, mes = int(m.group("d3")), _MESES[m.group("m3").lower()]
                anio = int(m.group("a3")) if m.group("a3") else _anio_probable(dia, mes, referencia)
                if anio is not None:
                    yield date(anio, mes, dia), m
        except ValueError, KeyError:
            continue


def importe_decimal(literal: str) -> Decimal | None:
    numero = re.sub(r"[^\d.,]", "", literal)
    if "." in numero and "," in numero:
        numero = (
            numero.replace(".", "").replace(",", ".")
            if numero.rfind(",") > numero.rfind(".")
            else numero.replace(",", "")
        )
    elif "," in numero:
        entero, _, dec = numero.rpartition(",")
        numero = f"{entero.replace(',', '')}.{dec}" if len(dec) <= 2 else numero.replace(",", "")
    elif "." in numero:
        entero, _, dec = numero.rpartition(".")
        numero = f"{entero.replace('.', '')}.{dec}" if len(dec) <= 2 else numero.replace(".", "")
    try:
        valor = Decimal(numero).quantize(Decimal("0.01"))
    except InvalidOperation, ValueError:
        return None
    return valor if Decimal(0) < valor < Decimal(1_000_000) else None


def importes(texto: str) -> Iterator[tuple[Decimal, re.Match[str]]]:
    for m in _IMPORTE_RE.finditer(texto):
        valor = importe_decimal(m.group("n1") or m.group("n2") or "")
        if valor is not None:
            yield valor, m


def importe_total(texto: str) -> Extraido | None:
    """The amount on a «total / importe / a pagar / cargo» line (the last such line), else the only amount."""
    candidatos: list[tuple[int, int, Decimal, re.Match[str]]] = []
    for valor, m in importes(texto):
        linea = fragmento(texto, m.start(), m.end())
        candidatos.append((1 if _TOTAL_RE.search(linea) else 0, m.start(), valor, m))
    if not candidatos:
        return None
    con_total = [c for c in candidatos if c[0]]
    if con_total:
        _, _, valor, m = con_total[-1]
    elif len({c[2] for c in candidatos}) == 1:
        _, _, valor, m = candidatos[0]
    else:
        return None
    return Extraido("importe", f"{valor}", m.group(0).strip(), fragmento(texto, m.start(), m.end()))


def plazo(texto: str, referencia: date) -> Extraido | None:
    """The earliest date on or after the message date that a deadline phrase announces (≤ 60 chars before)."""
    mejor: tuple[date, re.Match[str]] | None = None
    for dia, m in fechas(texto, referencia):
        previo = texto[max(0, m.start() - 60) : m.start()]
        if not _PLAZO_RE.search(previo) or dia < referencia:
            continue
        if mejor is None or dia < mejor[0]:
            mejor = (dia, m)
    if mejor is None:
        return None
    dia, m = mejor
    return Extraido("plazo", dia.isoformat(), m.group(0).strip(), fragmento(texto, m.start(), m.end()))


def fecha_tras(texto: str, referencia: date, patron: str, campo: str) -> Extraido | None:
    """The first date within 40 characters after a label matching ``patron`` («fecha de emisión»…)."""
    for etiqueta in re.finditer(patron, texto, re.IGNORECASE):
        tramo = texto[etiqueta.end() : etiqueta.end() + 40]
        for dia, m in fechas(tramo, referencia):
            inicio = etiqueta.end() + m.start()
            return Extraido(
                campo, dia.isoformat(), m.group(0).strip(), fragmento(texto, inicio, inicio + len(m.group(0)))
            )
    return None


def periodo(texto: str, referencia: date) -> tuple[Extraido, Extraido] | None:
    """«Periodo de facturación: del 01/10/2026 al 31/10/2026» -> (start, end)."""
    for etiqueta in re.finditer(r"periodo(?: de (?:facturaci[oó]n|consumo))?", texto, re.IGNORECASE):
        tramo = texto[etiqueta.end() : etiqueta.end() + 80]
        encontradas = list(fechas(tramo, referencia))
        if len(encontradas) >= 2 and encontradas[0][0] <= encontradas[1][0]:
            salida = []
            for campo, (dia, m) in zip(("periodo_inicio", "periodo_fin"), encontradas[:2], strict=True):
                inicio = etiqueta.end() + m.start()
                salida.append(
                    Extraido(
                        campo, dia.isoformat(), m.group(0).strip(), fragmento(texto, inicio, inicio + len(m.group(0)))
                    )
                )
            return salida[0], salida[1]
    return None


def potencia_kw(texto: str) -> Extraido | None:
    m = _POTENCIA_RE.search(texto)
    if not m:
        return None
    try:
        valor = Decimal(m.group("n").replace(",", "."))
    except InvalidOperation:
        return None
    if not Decimal("0.1") <= valor <= Decimal(15):  # 2.0TD domestic supplies, up to 15 kW
        return None
    literal = texto[m.start("n") : m.end()].strip()
    return Extraido("potencia_kw", f"{valor.normalize():f}", literal, fragmento(texto, m.start(), m.end()))


_NUMERO_LARGO = re.compile(r"\d{4,}")
_CODIGO = re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{5,}\b")
_ANIO = re.compile(r"(19|20)\d\d")
# An e-mail address (a person's, or a company's: either way not the user's data to keep).
_DIRECCION = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
TAPA_DIRECCION = "[dirección de correo]"
# A phone number written in groups («600 123 456», «+34 912 34 56 78», «91-123-45-67»): at least 9 digits.
# Only spaces and hyphens separate the groups, so amounts («1.234.567,89») and dates are never taken for one.
_TELEFONO = re.compile(r"(?<![\w+])(?:\+\d{1,3}[ \-]?)?\d{2,4}(?:[ \-]\d{2,4}){2,4}(?![\w])")


def enmascarar(fragmento: str, literal: str) -> str:
    """The snippet with identifiers masked (card or account digits, references, booking codes, e-mail
    addresses, phone numbers), except the extracted literal itself and years: what is stored is the value and
    its context, not third-party ids."""
    inicio = fragmento.find(literal) if literal else -1
    fin = inicio + len(literal) if inicio >= 0 else -1

    def _es_el_literal(m: re.Match[str]) -> bool:
        return inicio >= 0 and m.start() < fin and m.end() > inicio

    def _tapar(m: re.Match[str]) -> str:
        if _es_el_literal(m) or _ANIO.fullmatch(m.group(0)):
            return m.group(0)
        return "•" * len(m.group(0))

    def _tapar_telefono(m: re.Match[str]) -> str:
        if _es_el_literal(m) or sum(c.isdigit() for c in m.group(0)) < 9:
            return m.group(0)
        return "".join("•" if c.isdigit() else c for c in m.group(0))

    # Addresses first (whole, so neither half survives), then phones, then the digit runs and codes. Masking
    # keeps lengths, so the literal's position stays valid for every pass.
    texto = _DIRECCION.sub(lambda m: m.group(0) if _es_el_literal(m) else "•" * len(m.group(0)), fragmento)
    texto = _TELEFONO.sub(_tapar_telefono, texto)
    texto = _CODIGO.sub(_tapar, _NUMERO_LARGO.sub(_tapar, texto))
    return _con_tapa_de_direccion(texto, fragmento)


def _con_tapa_de_direccion(texto: str, original: str) -> str:
    """Each masked address replaced by a readable label (after the other passes, which rely on equal lengths)."""
    salida, ultimo = [], 0
    for m in _DIRECCION.finditer(original):
        if texto[m.start() : m.end()] == "•" * len(m.group(0)):
            salida.append(texto[ultimo : m.start()])
            salida.append(TAPA_DIRECCION)
            ultimo = m.end()
    salida.append(texto[ultimo:])
    return "".join(salida)


def aparece(literal: str, texto: str) -> bool:
    """Whether ``literal`` is in ``texto`` (whitespace-insensitive): the gate for any extracted value."""
    return bool(literal.strip()) and " ".join(literal.split()) in " ".join(texto.split())


__all__ = [
    "MAX_FRAGMENTO",
    "Extraido",
    "aparece",
    "enmascarar",
    "fecha_tras",
    "fechas",
    "fragmento",
    "importe_decimal",
    "importe_total",
    "importes",
    "periodo",
    "plazo",
    "potencia_kw",
]
