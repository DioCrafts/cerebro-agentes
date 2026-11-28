"""Identifiers that must be masked the same way everywhere a page's text leaves the browser.

Two places redact what a page says: ``seguridad.py`` for the skeleton a script's diagnosis reads, and
``instantanea.py`` for the ARIA snapshot a mission's model reads. They used to carry their own copy of these
patterns and the copies drifted apart: the IBAN was ``(?:\\s?\\d{4}){5}`` in one and ``{3,7}`` in the other, so a
22-character German IBAN was masked whole in the snapshot and left with its ``DE89`` showing in the skeleton.
A CUPS, an IBAN, a NIF and an e-mail are the same thing on both sides, so they live here and both import them.

What stays apart, on purpose: how each side treats bare numbers. The skeleton masks any long run of digits,
because a selector diagnosis never needs to read a figure. The snapshot keeps dates and amounts, because a
mission usually has to (``test_el_texto_de_la_pagina_llega_enmascarado_y_recortado`` pins both). Adding a
pattern here therefore tightens both sides at once; adding one to a number policy tightens only that side.
"""

from __future__ import annotations

import re

MASCARA = "•••"

# Words after which a page tends to print the person's name ("Hola, María Ejemplo"). Each side applies its own
# tail: the skeleton eats the rest of the string, the snapshot stops before a quote or a ``[ref=...]``.
SALUDO = r"hola|bienvenid[oa]s?|usuario|usuaria|titular|sesi[oó]n de"

CUPS = re.compile(r"\bES\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?[A-Z]{2}(?:\s?\d[A-Z])?\b", re.IGNORECASE)
# Any IBAN, not just the 24-character Spanish one: 2 letters, 2 check digits and 11 to 30 more characters.
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?\d{4}){3,7}(?:\s?\d{1,4})?\b")
NIF = re.compile(r"\b[0-9XYZxyz]\d{7}[A-Za-z]\b")
# The quotes are excluded so the pattern cannot eat the closing quote of a snapshot line's accessible name.
EMAIL = re.compile(r"[^\s@\"]+@[^\s@\"]+\.[^\s@\"]+")

IDENTIFICADORES = (CUPS, IBAN, NIF, EMAIL)

__all__ = ["CUPS", "EMAIL", "IBAN", "IDENTIFICADORES", "MASCARA", "NIF", "SALUDO"]
