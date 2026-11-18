"""Masking personal data before anything becomes a candidate case — keeping the amounts, which are the point.

Why a gate of its own: a candidate case is read by a reviewer (the user, or the operator) and, once accepted, is
re-run again and again by the evaluation. Neither needs to know WHO asked; both need the figures exactly as they
were (a wrong figure is usually the bug). So every text that travels into a candidate passes, in this order:

1. **identifiers** — e-mail, IBAN, CUPS, DNI/NIE, card and phone numbers (:data:`IDENTIFICADORES`, plus phones
   written in other groupings) → ``[correo]``, ``[iban]``, ``[cups]``, ``[documento]``, ``[tarjeta]``,
   ``[teléfono]`` (first: an e-mail address holds names, an IBAN holds digit groups);
2. **street addresses** (``C/ Mayor 12, 3º B``, ``Avda. de la Paz 4``…, with the postal code next to them) →
   ``[dirección]``;
3. **names from the profile** (and any extra names given, e.g. the account's e-mail local part) → ``[nombre]``;
4. what is left goes through the mail module's own masker (:func:`asistente.capacidades.correo.extraccion.
   enmascarar`: reference and booking codes, long digit runs, phones written in groups) with the amounts, dates
   and short numbers PROTECTED first, so ``1.234,56 €``, ``30000 €``, ``18,5 %``, ``4,6 kW``, ``15/01/2020`` or a
   plazo of ``300`` meses come out untouched.

Every replacement is recorded as a :class:`CambioRedaccion` (where, what kind, the placeholder and an abbreviated
view of the original — never the original itself), so the reviewer sees exactly what was masked: that list is
the diff. Masking is a best effort by patterns; the reviewer's acceptance (and :func:`identificadores_en` run
again on the final case) is the last gate.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import re
from typing import Any
import unicodedata

from pydantic import BaseModel, ConfigDict

# --------------------------------------------------------------------------- #
# Identifiers (moved here from captura.py, which re-exports them)
# --------------------------------------------------------------------------- #

IDENTIFICADORES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[correo]"),
    (re.compile(r"\bES\d{2}(?:[ -]?\d{4}){5}\b", re.IGNORECASE), "[iban]"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]{4}){3,7}\b"), "[iban]"),
    (re.compile(r"\bES\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?[A-Z]{2}(?:\s?\d[A-Z])?\b", re.IGNORECASE), "[cups]"),
    (re.compile(r"\b[XYZ]-?\d{7}-?[A-Z]\b", re.IGNORECASE), "[documento]"),
    (re.compile(r"\b\d{8}-?[A-Z]\b", re.IGNORECASE), "[documento]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[tarjeta]"),
    (re.compile(r"(?<!\d)(?<!\d[.,])(?:\+34[ -]?)?[6789]\d{2}[ -]?\d{3}[ -]?\d{3}(?!\d)(?![.,]\d)"), "[teléfono]"),
)
# A phone written in other groupings («+34 912 34 56 78», «91-123-45-67»): at least 9 digits, groups separated by
# spaces or hyphens only (the mail module's rule), so amounts («1.234.567,89») and dates are never taken for one.
_TELEFONO_AGRUPADO_RE = re.compile(r"(?<![\w+])(?:\+\d{1,3}[ \-]?)?\d{2,4}(?:[ \-]\d{2,4}){2,4}(?![\w])")
_TIPO_DE_SUSTITUTO = {
    "[correo]": "correo",
    "[iban]": "iban",
    "[cups]": "cups",
    "[documento]": "documento",
    "[tarjeta]": "tarjeta",
    "[teléfono]": "telefono",
}


def seudonimizar(texto: str) -> str:
    """Replace identifiers (e-mail, IBAN, CUPS, DNI/NIE, card and phone numbers) by placeholders."""
    for patron, sustituto in IDENTIFICADORES:
        texto = patron.sub(sustituto, texto)
    return texto


def identificadores_en(texto: str) -> list[str]:
    """Kinds of identifier still present in ``texto`` (empty = none found by the patterns)."""
    return sorted({sustituto for patron, sustituto in IDENTIFICADORES if patron.search(texto)})


# --------------------------------------------------------------------------- #
# Addresses, protected figures
# --------------------------------------------------------------------------- #

_VIA = (
    r"(?:c/|c\.|calle|avda\.?|av\.|avenida|plaza|pza\.?|pl\.|paseo|p[ºo]\.?|camino|carretera|ctra\.?|ronda|"
    r"traves[ií]a|glorieta|urbanizaci[oó]n|urb\.|callej[oó]n|rambla|v[ií]a)"
)
_DIRECCION_RE = re.compile(
    r"(?<![\w])" + _VIA + r"\s*(?:de\s+(?:la\s+|los\s+|las\s+)?|del\s+)?"
    r"[A-ZÁÉÍÓÚÑa-záéíóúñ][\wÁÉÍÓÚÑáéíóúñ.'ºª-]*(?:\s+[\wÁÉÍÓÚÑáéíóúñ.'ºª-]+){0,5}?"
    r",?\s*(?:n\.?\s?º|nº|n[uú]m\.?|n[uú]mero)?\s*\d{1,4}[A-Za-z]?"
    r"(?:[,\s]+(?:\d{1,2}\s?[ºª]\s?[A-Za-z]?|piso\s+\d+|bajo|[aá]tico|entresuelo|puerta\s+\w+|esc(?:alera|\.)\s*\w+))*"
    r"(?:[,\s]+(?:0[1-9]|[1-4]\d|5[0-2])\d{3}\b)?",
    re.IGNORECASE,
)
_CODIGO_POSTAL_RE = re.compile(
    r"\b(?:c\.?\s?p\.?|c[oó]digo postal)\s*:?\s*(?:0[1-9]|[1-4]\d|5[0-2])\d{3}\b", re.IGNORECASE
)

# Amounts, percentages, quantities with a unit, dates and short numbers: kept as they are.
_NB = r"(?<![A-Za-z0-9])"
_NA = r"(?![A-Za-z0-9])"
_PROTEGIDO_RE = re.compile(
    "|".join(
        (
            _NB + r"\d{1,2}/\d{1,2}/\d{2,4}" + _NA,  # 15/01/2020
            _NB + r"\d{4}-\d{2}-\d{2}" + _NA,  # 2026-03-20
            _NB + r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?" + _NA,  # 1.234,56 · 30.000
            _NB + r"-?\d+,\d+" + _NA,  # 474,21 · 0,15
            _NB + r"-?\d+(?:\.\d+)?(?=\s?(?:€|euros?\b|eur\b|%|kwh\b|kw\b|meses\b|años\b|días\b|dias\b))",
            _NB + r"\d{1,6}" + _NA,  # a short plain number: an amount, a count, a year
        )
    ),
    re.IGNORECASE,
)
_CENTINELA_INICIO, _CENTINELA_FIN = "", ""
_CENTINELA_RE = re.compile(_CENTINELA_INICIO + r"([a-z]+)" + _CENTINELA_FIN)


def _clave(n: int) -> str:
    letras = ""
    n += 1
    while n:
        n, resto = divmod(n - 1, 26)
        letras = chr(ord("a") + resto) + letras
    return letras


def _numero_de_clave(clave: str) -> int:
    n = 0
    for letra in clave:
        n = n * 26 + (ord(letra) - ord("a") + 1)
    return n - 1


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #

_PARTICULAS = frozenset({"de", "del", "la", "las", "los", "y", "e", "da", "dos", "van", "von"})
_NOMBRE_RE = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' .-]{2,80}$")
_VOCALES = {
    "a": "aáàä",
    "e": "eéèë",
    "i": "iíìï",
    "o": "oóòö",
    "u": "uúùü",
    "n": "nñ",
}


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _patron_nombre(nombre: str) -> str:
    partes = []
    for caracter in _sin_acentos(nombre).casefold():
        variantes = _VOCALES.get(caracter)
        partes.append(f"[{variantes}]" if variantes else re.escape(caracter))
    return "".join(partes)


def nombres_de_valor(valor: Any, *, clave: str = "") -> list[str]:
    """Person names found in a fact's value: strings under a key that names a person (``nombre``, ``apellidos``,
    ``nombre_conyuge``…), split into their words (a first name alone is how it shows up in a question)."""
    encontrados: list[str] = []
    if isinstance(valor, str):
        if clave and ("nombre" in clave or "apellido" in clave) and _NOMBRE_RE.match(valor.strip()):
            completo = " ".join(valor.split())
            encontrados.append(completo)
            encontrados.extend(
                p for p in re.split(r"[ .-]+", completo) if len(p) >= 3 and p.casefold() not in _PARTICULAS
            )
    elif isinstance(valor, Mapping):
        for k, v in valor.items():
            encontrados.extend(nombres_de_valor(v, clave=str(k).casefold()))
    elif isinstance(valor, list):
        for v in valor:
            encontrados.extend(nombres_de_valor(v, clave=clave))
    return encontrados


def nombres_de_perfil(hechos: Iterable[tuple[str, Any]], *, extra: Iterable[str] = ()) -> list[str]:
    """Names to mask: from the profile's facts (``(name, value)`` pairs; JSON text is parsed) and ``extra``
    (e.g. the local part of the account's e-mail, split at dots), longest first."""
    nombres: set[str] = set()
    for nombre, valor in hechos:
        if isinstance(valor, str) and valor[:1] in "[{":
            try:
                valor = json.loads(valor)
            except ValueError:
                pass
        nombres.update(nombres_de_valor(valor, clave=str(nombre).casefold()))
    for texto in extra:
        for parte in re.split(r"[ ._+-]+", str(texto)):
            if len(parte) >= 3 and parte.isalpha() and parte.casefold() not in _PARTICULAS:
                nombres.add(parte)
    return sorted(nombres, key=lambda n: (-len(n), n))


# --------------------------------------------------------------------------- #
# The masker
# --------------------------------------------------------------------------- #


class CambioRedaccion(BaseModel):
    """One masked span: where, what kind, the placeholder, and an abbreviated view (never the original)."""

    model_config = ConfigDict(extra="forbid")

    campo: str
    tipo: str
    sustituto: str
    vista: str


def vista_abreviada(original: str) -> str:
    """«A…z (11)»: enough to recognise what was masked, not enough to recover it."""
    limpio = original.strip()
    if len(limpio) <= 3:
        return "•" * len(limpio) + f" ({len(limpio)})"
    return f"{limpio[0]}…{limpio[-1]} ({len(limpio)})"


class Enmascarador:
    """Masks texts (and JSON values) of one candidate, recording every change in :attr:`cambios`."""

    def __init__(self, nombres: Iterable[str] = ()) -> None:
        unicos = sorted({n.strip() for n in nombres if n and len(n.strip()) >= 2}, key=lambda n: (-len(n), n))
        self._nombres = (
            re.compile(
                r"(?<![\wÁÉÍÓÚÑáéíóúñ])(?:" + "|".join(_patron_nombre(n) for n in unicos) + r")(?![\wÁÉÍÓÚÑáéíóúñ])",
                re.IGNORECASE,
            )
            if unicos
            else None
        )
        self.cambios: list[CambioRedaccion] = []

    def _anotar(self, campo: str, tipo: str, sustituto: str, original: str) -> None:
        self.cambios.append(
            CambioRedaccion(campo=campo, tipo=tipo, sustituto=sustituto, vista=vista_abreviada(original))
        )

    def texto(self, texto: str, campo: str) -> str:
        if not texto:
            return texto
        texto = texto.replace(_CENTINELA_INICIO, "").replace(_CENTINELA_FIN, "")
        # Identifiers first (an e-mail address holds names; an IBAN holds digit groups), then addresses, names.
        for patron, sustituto in IDENTIFICADORES:

            def identificador(m: re.Match[str], sustituto: str = sustituto) -> str:
                self._anotar(campo, _TIPO_DE_SUSTITUTO[sustituto], sustituto, m.group(0))
                return sustituto

            texto = patron.sub(identificador, texto)

        def telefono(m: re.Match[str]) -> str:
            if sum(c.isdigit() for c in m.group(0)) < 9:
                return m.group(0)
            self._anotar(campo, "telefono", "[teléfono]", m.group(0))
            return "[teléfono]"

        texto = _TELEFONO_AGRUPADO_RE.sub(telefono, texto)
        for patron in (_DIRECCION_RE, _CODIGO_POSTAL_RE):

            def direccion(m: re.Match[str]) -> str:
                self._anotar(campo, "direccion", "[dirección]", m.group(0))
                return "[dirección]"

            texto = patron.sub(direccion, texto)
        if self._nombres is not None:

            def nombre(m: re.Match[str]) -> str:
                self._anotar(campo, "nombre", "[nombre]", m.group(0))
                return "[nombre]"

            texto = self._nombres.sub(nombre, texto)
        return self._resto(texto, campo)

    def _resto(self, texto: str, campo: str) -> str:
        """The mail module's masker over what is left, with the figures protected by sentinels."""
        from asistente.capacidades.correo.extraccion import enmascarar

        protegidos: list[str] = []

        def proteger(m: re.Match[str]) -> str:
            protegidos.append(m.group(0))
            return _CENTINELA_INICIO + _clave(len(protegidos) - 1) + _CENTINELA_FIN

        cubierto = _PROTEGIDO_RE.sub(proteger, texto)
        tapado = enmascarar(cubierto, "")
        if tapado != cubierto and len(tapado) == len(cubierto):
            for m in re.finditer(r"•+", tapado):
                original = cubierto[m.start() : m.end()]
                if "•" not in original:
                    self._anotar(campo, "codigo", "•" * len(original), original)
        elif tapado != cubierto:  # a length change (the mail masker labels e-mails): record it coarsely
            self._anotar(campo, "codigo", "•", cubierto)
        return _CENTINELA_RE.sub(lambda m: protegidos[_numero_de_clave(m.group(1))], tapado)

    def valor(self, valor: Any, campo: str) -> Any:
        """``valor`` with every string masked (numbers, booleans and None untouched)."""
        if isinstance(valor, str):
            return self.texto(valor, campo)
        if isinstance(valor, list):
            return [self.valor(v, f"{campo}[{i}]") for i, v in enumerate(valor)]
        if isinstance(valor, Mapping):
            return {k: self.valor(v, f"{campo}.{k}") for k, v in valor.items()}
        return valor


def enmascarar_texto(
    texto: str, *, nombres: Iterable[str] = (), campo: str = "texto"
) -> tuple[str, list[CambioRedaccion]]:
    """One text masked, with its diff."""
    enmascarador = Enmascarador(nombres)
    return enmascarador.texto(texto, campo), enmascarador.cambios


def nombres_en(texto: str, nombres: Iterable[str]) -> list[str]:
    """Which of ``nombres`` still appear in ``texto`` (whole words, case and accents aside)."""
    presentes = []
    for nombre in nombres:
        if nombre and re.search(
            r"(?<![\wÁÉÍÓÚÑáéíóúñ])" + _patron_nombre(nombre) + r"(?![\wÁÉÍÓÚÑáéíóúñ])", texto, re.IGNORECASE
        ):
            presentes.append(nombre)
    return presentes


__all__ = [
    "IDENTIFICADORES",
    "CambioRedaccion",
    "Enmascarador",
    "enmascarar_texto",
    "identificadores_en",
    "nombres_de_perfil",
    "nombres_de_valor",
    "nombres_en",
    "seudonimizar",
    "vista_abreviada",
]
