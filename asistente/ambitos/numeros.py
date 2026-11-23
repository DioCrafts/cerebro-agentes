"""Parser único de importes y cifras escritas a la española para todos los ámbitos.

Antes cada ámbito tenía el suyo y casi todos fallaban en algo: unos leían «150.000» como 150 (quitaban
los puntos solo si había coma), otros convertían «1.234,56» en un número inválido. Las reglas son las
de ``asistente.nucleo.decision.fact_extractor._parse_number_from_string``, aplicadas a la cadena entera
(aquí no se busca la cifra dentro de una frase: una entrada que no es un número es un error):

* con punto y coma, el último separador es el decimal («150.000,50» → 150000.50, «1,234.50» → 1234.50);
* solo coma: decimal («2,5» → 2.5), salvo agrupación estricta de miles («45,000» → 45000);
* solo punto: miles si agrupa de tres en tres («150.000» → 150000), decimal si no («2.5» → 2.5).

``coma_decimal=True`` hace que una coma sola sea siempre decimal, para columnas que sabemos decimales
(los kWh horarios de la distribuidora, «0,123» → 0.123).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

_MILES_PUNTO = re.compile(r"^[1-9]\d{0,2}(?:\.\d{3})+$")
_MILES_COMA = re.compile(r"^[1-9]\d{0,2}(?:,\d{3})+$")
_NUMERO = re.compile(r"^\d[\d.,]*$|^[.,]\d+$")
_SOBRA = str.maketrans("", "", "€%   '")


def importe(valor: Any, *, coma_decimal: bool = False) -> Decimal | None:
    """Decimal de ``valor`` (texto en castellano, int, float o Decimal); ``None`` si no es un número finito."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, Decimal):
        return valor if valor.is_finite() else None
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, float):
        numero = Decimal(str(valor))
        return numero if numero.is_finite() else None
    texto = str(valor).strip().translate(_SOBRA).replace("EUR", "").replace("eur", "")
    signo = ""
    if texto[:1] in "+-" and texto:
        signo, texto = ("-" if texto[0] == "-" else ""), texto[1:]
    if not texto or not _NUMERO.match(texto):
        return None
    punto, coma = "." in texto, "," in texto
    if punto and coma:
        if texto.rfind(",") > texto.rfind("."):
            normal = texto.replace(".", "").replace(",", ".")
        else:
            normal = texto.replace(",", "")
    elif coma:
        if texto.count(",") > 1 or (not coma_decimal and _MILES_COMA.match(texto)):
            normal = texto.replace(",", "") if _MILES_COMA.match(texto) else ""
        else:
            normal = texto.replace(",", ".")
    elif punto:
        if _MILES_PUNTO.match(texto):
            normal = texto.replace(".", "")
        elif texto.count(".") > 1:
            normal = ""
        else:
            normal = texto
    else:
        normal = texto
    try:
        numero = Decimal(signo + normal)
    except InvalidOperation:
        return None
    return numero if numero.is_finite() else None
