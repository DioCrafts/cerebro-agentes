"""Deterministic calculations over figures the user gives in the conversation.

Two families, both pure (no I/O, no model, no ``eval``), in ``Decimal`` with explicit rounding:

* amounts: add, subtract, multiply, monthly ↔ yearly, a percentage of a base and the percentage change between two
  amounts (:func:`calcular_importes`);
* a fixed-rate loan (French system): instalment, schedule, total interest and the interest saved by an early
  repayment that reduces the instalment or the term (:func:`calcular_prestamo`). The schedule itself is the mortgage
  module's (``ambitos/hipoteca/calculo.calcular_cuadro``) over a fixed-rate ``Prestamo`` built from the user's
  figures, so the arithmetic is the same one the mortgage tools use.

Every result carries its figures as :class:`Resultado` lines (concept, exact value, unit, how it is written), so the
tool layer can declare them to the figures gate, and the assumptions taken, so the answer can state them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
import re
from typing import Any, Literal

CENT = Decimal("0.01")
MAX_IMPORTES = 50
MAX_VALOR = Decimal("1000000000000")  # 10^12: anything above is a typo, not a household amount

Periodicidad = Literal["mes", "año", "unico"]
UnidadSalida = Literal["EUR", "EUR/mes", "EUR/año", "%", "meses", "unidades"]


class CalculoError(ValueError):
    """The arguments cannot be computed; the message is for the user (Spanish)."""


@dataclass(frozen=True)
class Linea:
    """One output figure: what it is, its exact (already rounded) value, its unit and how the text writes it."""

    concepto: str
    valor: Decimal
    unidad: UnidadSalida | None
    literal: str


@dataclass
class Resultado:
    titulo: str
    lineas: list[Linea] = field(default_factory=list)
    detalle: list[str] = field(default_factory=list)  # extra text lines (schedule rows…) whose figures are declared too
    supuestos: list[str] = field(default_factory=list)

    def anadir(self, concepto: str, valor: Decimal, unidad: UnidadSalida | None, decimales: int = 2) -> Linea:
        redondeado = redondear(valor, decimales)
        linea = Linea(concepto, redondeado, unidad, escribir(redondeado, unidad, decimales))
        self.lineas.append(linea)
        return linea


# --------------------------------------------------------------------------------------------------------------
# Numbers in and out
# --------------------------------------------------------------------------------------------------------------

_NUMERO_ES = re.compile(r"^[+-]?\d{1,3}(?:\.\d{3})+(?:,\d+)?$")


def leer_decimal(valor: Any, nombre: str) -> Decimal:
    """A number from the model's arguments: a JSON number or a Spanish/plain string («13,99», «180.000», «3.2»).

    Floats are read through their shortest repr (``13.99`` → ``Decimal('13.99')``), never their binary value.
    """
    if isinstance(valor, bool) or valor is None:
        raise CalculoError(f"Falta un número en «{nombre}».")
    if isinstance(valor, int | Decimal):
        numero = Decimal(valor)
    elif isinstance(valor, float):
        numero = Decimal(repr(valor))
    elif isinstance(valor, str):
        texto = valor.strip().replace("€", "").replace("%", "").replace(" ", "").replace(" ", "")
        if _NUMERO_ES.match(texto):
            texto = texto.replace(".", "").replace(",", ".")
        elif "," in texto and "." not in texto:
            texto = texto.replace(",", ".")
        try:
            numero = Decimal(texto)
        except InvalidOperation as exc:
            raise CalculoError(f"«{valor}» no es un número válido ({nombre}).") from exc
    else:
        raise CalculoError(f"«{nombre}» debe ser un número.")
    if not numero.is_finite() or abs(numero) > MAX_VALOR:
        raise CalculoError(f"«{nombre}» está fuera de rango.")
    return numero


def redondear(valor: Decimal, decimales: int = 2) -> Decimal:
    """Half-up rounding to ``decimales`` decimals (the usual Spanish commercial rounding)."""
    return valor.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP)


_SUFIJO = {"EUR": " €", "EUR/mes": " €/mes", "EUR/año": " €/año", "%": " %", "meses": " meses", "unidades": ""}


def escribir(valor: Decimal, unidad: str | None, decimales: int = 2) -> str:
    """Spanish writing: 1234.5 → «1.234,50 €»."""
    texto = f"{redondear(valor, decimales):,.{decimales}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if texto.startswith("-"):
        texto = "−" + texto[1:]
    return texto + _SUFIJO.get(unidad or "", "")


# --------------------------------------------------------------------------------------------------------------
# Amounts
# --------------------------------------------------------------------------------------------------------------

OPERACIONES = ("sumar", "restar", "multiplicar", "anualizar", "mensualizar", "porcentaje", "variacion")
_UNIDAD_DE = {"mes": "EUR/mes", "año": "EUR/año", "unico": "EUR"}


def _periodicidad(valor: Any) -> Periodicidad:
    texto = str(valor or "unico").strip().lower()
    if texto in ("mes", "mensual", "al mes", "mensuales"):
        return "mes"
    if texto in ("año", "ano", "anual", "al año", "anuales"):
        return "año"
    if texto in ("unico", "único", "puntual", ""):
        return "unico"
    raise CalculoError(f"Periodicidad «{valor}» no válida: usa mes, año o unico.")


@dataclass(frozen=True)
class Importe:
    concepto: str
    valor: Decimal
    periodicidad: Periodicidad = "unico"


def leer_importes(crudos: Any) -> list[Importe]:
    if not isinstance(crudos, Sequence) or isinstance(crudos, str) or not crudos:
        raise CalculoError("Indica los importes (lista «importes» con concepto y valor).")
    if len(crudos) > MAX_IMPORTES:
        raise CalculoError(f"Como mucho {MAX_IMPORTES} importes por cálculo.")
    importes: list[Importe] = []
    for i, crudo in enumerate(crudos, start=1):
        if isinstance(crudo, Mapping):
            concepto = str(crudo.get("concepto") or f"importe {i}").strip()[:80]
            valor = leer_decimal(crudo.get("valor"), concepto)
            periodicidad = _periodicidad(crudo.get("periodicidad"))
        else:
            concepto, valor, periodicidad = f"importe {i}", leer_decimal(crudo, f"importe {i}"), "unico"
        importes.append(Importe(concepto, valor, periodicidad))
    return importes


def calcular_importes(
    operacion: str,
    importes: Sequence[Importe],
    *,
    factor: Decimal | None = None,
    porcentaje: Decimal | None = None,
    decimales: int = 2,
) -> Resultado:
    """One arithmetic operation over the user's amounts. See :data:`OPERACIONES`."""
    if operacion not in OPERACIONES:
        raise CalculoError(f"Operación «{operacion}» desconocida. Válidas: {', '.join(OPERACIONES)}.")
    if not 0 <= decimales <= 6:
        raise CalculoError("«decimales» debe estar entre 0 y 6.")
    res = Resultado(titulo=f"Cálculo ({operacion})")
    res.supuestos.append(
        f"Importes tal como los ha dado el usuario; resultado redondeado a {decimales} decimales (mitad hacia arriba)."
    )

    if operacion == "sumar":
        periodos = {i.periodicidad for i in importes}
        if periodos <= {"unico"} or len(periodos) == 1:
            unidad = _UNIDAD_DE[importes[0].periodicidad]
            total = sum((i.valor for i in importes), Decimal(0))
            res.anadir("total", total, unidad, decimales)  # type: ignore[arg-type]
            if periodos == {"mes"}:
                res.anadir("total al año (×12)", total * 12, "EUR/año", decimales)
                res.supuestos.append("Al año = importe mensual × 12 (todos los meses se paga lo mismo).")
            elif periodos == {"año"}:
                res.anadir("total al mes (÷12)", total / 12, "EUR/mes", decimales)
                res.supuestos.append("Al mes = importe anual ÷ 12 (prorrateo lineal).")
        else:
            if "unico" in periodos:
                raise CalculoError(
                    "No se pueden sumar importes únicos con importes periódicos: indica su periodicidad."
                )
            mensual = sum((i.valor if i.periodicidad == "mes" else i.valor / 12 for i in importes), Decimal(0))
            anual = sum((i.valor * 12 if i.periodicidad == "mes" else i.valor for i in importes), Decimal(0))
            res.anadir("total al mes", mensual, "EUR/mes", decimales)
            res.anadir("total al año", anual, "EUR/año", decimales)
            res.supuestos.append("Mensual ↔ anual con el factor 12 (prorrateo lineal).")
            for i in importes:
                otro = (i.valor * 12, "EUR/año") if i.periodicidad == "mes" else (i.valor / 12, "EUR/mes")
                res.anadir(
                    f"{i.concepto} ({'al año' if otro[1] == 'EUR/año' else 'al mes'})", otro[0], otro[1], decimales
                )  # type: ignore[arg-type]
        if periodos == {"mes"} and len(importes) > 1:
            for i in importes:
                res.anadir(f"{i.concepto} al año", i.valor * 12, "EUR/año", decimales)
        return res

    if operacion == "restar":
        if len(importes) < 2:
            raise CalculoError("Para restar hacen falta al menos dos importes (el primero menos los demás).")
        if len({i.periodicidad for i in importes}) > 1:
            raise CalculoError("Para restar, todos los importes deben tener la misma periodicidad.")
        total = importes[0].valor - sum((i.valor for i in importes[1:]), Decimal(0))
        res.anadir("diferencia", total, _UNIDAD_DE[importes[0].periodicidad], decimales)  # type: ignore[arg-type]
        return res

    if operacion == "multiplicar":
        if factor is None:
            raise CalculoError("Para multiplicar indica «factor».")
        for i in importes:
            res.anadir(
                f"{i.concepto} × {escribir(factor, None, _decimales_de(factor))}",
                i.valor * factor,
                _UNIDAD_DE[i.periodicidad],
                decimales,
            )  # type: ignore[arg-type]
        return res

    if operacion in ("anualizar", "mensualizar"):
        destino: Periodicidad = "año" if operacion == "anualizar" else "mes"
        for i in importes:
            if i.periodicidad == destino:
                valor = i.valor
            elif destino == "año":
                valor = i.valor * 12
            else:
                valor = i.valor / 12
            res.anadir(
                f"{i.concepto} {'al año' if destino == 'año' else 'al mes'}", valor, _UNIDAD_DE[destino], decimales
            )  # type: ignore[arg-type]
        res.supuestos.append("Mensual ↔ anual con el factor 12 (prorrateo lineal, sin pagas extra ni meses distintos).")
        return res

    if operacion == "porcentaje":
        if porcentaje is None:
            raise CalculoError("Indica «porcentaje» (por ejemplo 21 para el 21 %).")
        for i in importes:
            parte = i.valor * porcentaje / 100
            pct = escribir(porcentaje, "%", _decimales_de(porcentaje))
            unidad = _UNIDAD_DE[i.periodicidad]
            res.anadir(f"{pct} de {i.concepto}", parte, unidad, decimales)  # type: ignore[arg-type]
            res.anadir(f"{i.concepto} + {pct}", i.valor + parte, unidad, decimales)  # type: ignore[arg-type]
            res.anadir(f"{i.concepto} − {pct}", i.valor - parte, unidad, decimales)  # type: ignore[arg-type]
        return res

    # variacion: from the first amount to the second
    if len(importes) != 2:
        raise CalculoError("Para la variación indica exactamente dos importes: el inicial y el final.")
    inicial, final = importes
    if inicial.valor == 0:
        raise CalculoError("El importe inicial es 0: no hay variación porcentual.")
    res.anadir("diferencia", final.valor - inicial.valor, _UNIDAD_DE[inicial.periodicidad], decimales)  # type: ignore[arg-type]
    res.anadir("variación porcentual", (final.valor - inicial.valor) / abs(inicial.valor) * 100, "%", decimales)
    return res


def _decimales_de(valor: Decimal) -> int:
    exponente = valor.normalize().as_tuple().exponent
    return max(0, -exponente) if isinstance(exponente, int) else 0


# --------------------------------------------------------------------------------------------------------------
# Fixed-rate loan
# --------------------------------------------------------------------------------------------------------------

MAX_PLAZO_MESES = 600
# The brain keeps at most 60 figures per report: the summary first, then a few rows and years of the schedule.
MAX_FILAS = 12
MAX_ANOS_RESUMEN = 5


@lru_cache(maxsize=1)
def _serie() -> Any:
    from asistente.ambitos.hipoteca.indices import cargar_serie

    return cargar_serie()


def calcular_prestamo(
    capital: Decimal,
    tipo_anual: Decimal,
    plazo_meses: int,
    *,
    amortizacion: Decimal | None = None,
    tras_cuota: int = 0,
    filas_cuadro: int = 0,
    hoy: date | None = None,
) -> Resultado:
    """Fixed-rate French loan with the user's figures; optionally an early repayment after instalment ``tras_cuota``
    (0 = now, before the first one), compared reducing the instalment and reducing the term.

    ``filas_cuadro``: how many schedule rows to show (the first ones, at most 12), plus the first years' summary.
    """
    from asistente.ambitos.hipoteca.calculo import Supuestos, calcular_cuadro, cuotas_necesarias, fecha_cuota
    from asistente.ambitos.hipoteca.modelo import AmortizacionRealizada, Prestamo

    if capital <= 0:
        raise CalculoError("El capital debe ser mayor que 0.")
    if not Decimal(-5) <= tipo_anual <= Decimal(30):
        raise CalculoError("El tipo de interés anual debe estar entre −5 % y 30 %.")
    if not 1 <= plazo_meses <= MAX_PLAZO_MESES:
        raise CalculoError(f"El plazo debe estar entre 1 y {MAX_PLAZO_MESES} meses.")
    if not 0 <= tras_cuota < plazo_meses:
        raise CalculoError("«tras_cuota» debe estar entre 0 (ahora) y el número de cuotas menos una.")
    inicio = hoy or date.today()
    prestamo = Prestamo(
        capital_inicial=capital,
        fecha_firma=inicio,
        plazo_meses=plazo_meses,
        tipo="fijo",
        tipo_inicial=tipo_anual,
        indice=None,
        comisiones_pactadas=[],
    )

    def cuadro(extra: tuple[AmortizacionRealizada, ...] = ()) -> Any:
        return calcular_cuadro(prestamo, _serie(), Supuestos(hoy=inicio, amortizaciones=extra))

    base = cuadro()
    tipo_txt = escribir(tipo_anual, "%", max(2, _decimales_de(tipo_anual)))
    res = Resultado(titulo=f"Préstamo de {escribir(capital, 'EUR')} al {tipo_txt} fijo a {plazo_meses} meses")
    res.supuestos += [
        f"Tipo fijo del {tipo_txt} nominal anual durante toda la vida del préstamo (si es variable o mixto, el "
        "resultado no vale tras la primera revisión).",
        "Sistema francés, cuotas mensuales, tipo mensual = tipo anual ÷ 12; cuota e intereses redondeados al céntimo "
        "y la última cuota ajusta el resto.",
        "Sin comisiones, seguros vinculados ni gastos: el banco puede cobrar comisión por amortizar anticipadamente "
        "(con límites legales) y su redondeo puede diferir en céntimos.",
    ]
    primera = base.filas[0]
    total_intereses = base.intereses_totales()
    res.anadir("cuota mensual", primera.cuota, "EUR/mes")
    res.anadir("intereses totales", total_intereses, "EUR")
    res.anadir("total pagado (capital + intereses)", capital + total_intereses, "EUR")
    res.anadir("número de cuotas", Decimal(len(base.filas)), "unidades", 0)

    if amortizacion is not None:
        if amortizacion <= 0:
            raise CalculoError("El importe a amortizar debe ser mayor que 0.")
        fecha = inicio if tras_cuota == 0 else fecha_cuota(prestamo, tras_cuota)
        pendiente = capital if tras_cuota == 0 else base.filas[tras_cuota - 1].capital_pendiente
        importe = min(amortizacion, pendiente)
        if tras_cuota == 0 and importe < capital:
            # Before the first instalment the schedule has no instalment to keep yet: the same rule as the mortgage
            # engine applies after one (fewer instalments of at most the original amount), on the remaining capital.
            restantes = min(plazo_meses, cuotas_necesarias(capital - importe, tipo_anual, primera.cuota))
            corto = prestamo.model_copy(update={"capital_inicial": capital - importe, "plazo_meses": restantes})
            plazo = calcular_cuadro(corto, _serie(), Supuestos(hoy=inicio))
        else:
            plazo = cuadro((AmortizacionRealizada(fecha=fecha, importe=importe, modalidad="plazo"),))
        cuota = cuadro((AmortizacionRealizada(fecha=fecha, importe=importe, modalidad="cuota"),))
        cuando = "ahora (antes de la primera cuota)" if tras_cuota == 0 else f"tras la cuota {tras_cuota}"
        res.detalle.append(f"Amortización anticipada de {escribir(importe, 'EUR')} {cuando}.")
        res.anadir("capital pendiente antes de amortizar", pendiente, "EUR")
        ahorro_plazo = total_intereses - plazo.intereses_totales()
        ahorro_cuota = total_intereses - cuota.intereses_totales()
        res.anadir("reducir plazo: intereses ahorrados", ahorro_plazo, "EUR")
        res.anadir("reducir plazo: cuotas menos", Decimal(len(base.filas) - len(plazo.filas)), "meses", 0)
        res.anadir("reducir plazo: nuevo número total de cuotas", Decimal(len(plazo.filas)), "unidades", 0)
        if len(cuota.filas) > tras_cuota:
            nueva = cuota.filas[tras_cuota].cuota
            res.anadir("reducir cuota: nueva cuota mensual", nueva, "EUR/mes")
            res.anadir("reducir cuota: rebaja de la cuota", primera.cuota - nueva, "EUR/mes")
        res.anadir("reducir cuota: intereses ahorrados", ahorro_cuota, "EUR")
        res.anadir("diferencia de intereses ahorrados (plazo − cuota)", ahorro_plazo - ahorro_cuota, "EUR")
        res.supuestos.append(
            "Ahorro de intereses = intereses del cuadro original menos los del cuadro tras amortizar, sin descontar "
            "la comisión de amortización anticipada ni el valor temporal del dinero."
        )

    if filas_cuadro > 0:
        for fila in base.filas[: min(filas_cuadro, MAX_FILAS)]:
            res.anadir(f"cuota {fila.numero}: intereses", fila.intereses, "EUR")
            res.anadir(f"cuota {fila.numero}: capital amortizado", fila.amortizacion, "EUR")
            res.anadir(f"cuota {fila.numero}: capital pendiente", fila.capital_pendiente, "EUR")
        for ano in range(1, min(MAX_ANOS_RESUMEN, (len(base.filas) + 11) // 12) + 1):
            filas = base.filas[(ano - 1) * 12 : ano * 12]
            res.anadir(f"año {ano}: intereses pagados", sum((f.intereses for f in filas), Decimal(0)), "EUR")
            res.anadir(f"año {ano}: capital pendiente al final", filas[-1].capital_pendiente, "EUR")
    return res


__all__ = [
    "OPERACIONES",
    "CalculoError",
    "Importe",
    "Linea",
    "Resultado",
    "calcular_importes",
    "calcular_prestamo",
    "escribir",
    "leer_decimal",
    "leer_importes",
    "redondear",
]
