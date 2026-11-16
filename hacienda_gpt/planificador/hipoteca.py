"""The mortgage lever of the planner: what an early repayment does to the household's position at the horizon.

Everything comes from the mortgage module: the schedule (:func:`calculo.calcular_cuadro`, the same conventions
and published indices), the fee cap (:func:`comisiones.comision_maxima`, Ley 5/2019 and earlier laws) and the
module's rule for what the household pays in a year (``pagos_del_ejercicio``: ordinary instalments plus early
repayments), which is what the pre-2013 housing deduction bridge (``modulos/puentes.py``) proposes to the tax
module.

Net position at the horizon ``T`` of repaying ``A`` on ``t0`` with fee ``c``, against keeping the money liquid at
the household's alternative return ``r`` (monthly ``(1+r)^(1/12)-1``, whole months as the module's own
comparison does)::

    Δ = Σ interest saved on the instalments in (t0, T]
        − c
        − (A + c)·(g(t0) − 1)                       # return the money would have earned
        + Σ F_t·(g(t) − 1)                          # return of the instalment cut F_t kept liquid

with ``g(t) = (1+r_m)^months(t, T)``. It is exact: the debt at ``T`` falls by ``A − ΣF_t + Σ interest saved``
and the liquid money grows by ``ΣF_t·g(t) − (A + c)·g(t0)``. Interest saved after ``T`` is outside the horizon
(the lower debt at ``T`` is counted).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from hacienda_gpt.modulos.hipoteca.calculo import Cuadro, Supuestos, calcular_cuadro, cuotas_necesarias, euros
from hacienda_gpt.modulos.hipoteca.comisiones import Comision, comision_maxima
from hacienda_gpt.modulos.hipoteca.indices import SerieIndices, cargar_serie
from hacienda_gpt.modulos.hipoteca.modelo import AmortizacionRealizada, Prestamo
from hacienda_gpt.modulos.hipoteca.normativa import Normativa, cargar_normativa

ZERO = Decimal(0)
ONE = Decimal(1)
CENT = Decimal("0.01")


def meses_entre(desde: date, hasta: date) -> int:
    """Whole calendar months from ``desde`` to ``hasta`` (negative when ``hasta`` is earlier)."""
    return (hasta.year - desde.year) * 12 + hasta.month - desde.month


@dataclass(frozen=True)
class Crecimiento:
    """Growth of money kept liquid at the household's alternative return, up to the horizon."""

    tasa_mensual: Decimal
    horizonte: date

    @classmethod
    def anual(cls, porcentaje: Decimal, horizonte: date) -> Crecimiento:
        r = porcentaje / 100
        mensual = (ONE + r) ** (ONE / 12) - ONE if r != 0 else ZERO
        return cls(tasa_mensual=mensual, horizonte=horizonte)

    def factor(self, desde: date) -> Decimal:
        """Value at the horizon of one euro held from ``desde`` (below 1 when ``desde`` is after the horizon)."""
        if self.tasa_mensual == 0:
            return ONE
        return (ONE + self.tasa_mensual) ** meses_entre(desde, self.horizonte)


@dataclass(frozen=True)
class EfectoAmortizacion:
    importe: Decimal
    modalidad: str | None
    comision: Comision
    intereses_ahorrados_horizonte: Decimal
    intereses_ahorrados_total: Decimal
    rendimiento_renunciado: Decimal
    rendimiento_liberado: Decimal
    # Net position at the horizon (without tax effects).
    valor: Decimal
    # What the household pays on the loan each calendar year (instalments + early repayments).
    pagos_por_ano: dict[int, Decimal]
    cuadro: Cuadro

    @property
    def efectivo(self) -> Decimal:
        """Cash the repayment takes: the amount plus its fee."""
        return self.importe + self.comision.importe


@dataclass(frozen=True)
class EfectoAproximado:
    """An early repayment valued by linear interpolation between two computed ones (search ranking only: the
    planner recomputes exactly every allocation it may choose)."""

    importe: Decimal
    valor: Decimal
    pagos_por_ano: dict[int, Decimal]
    efectivo: Decimal
    # The two computed repayments it lies between, and the weight of the upper one.
    bajo: EfectoAmortizacion
    alto: EfectoAmortizacion
    peso: Decimal


class PalancaHipoteca:
    """Evaluates early repayments of one loan on one date (cached by amount and modality)."""

    def __init__(
        self,
        prestamo: Prestamo,
        *,
        hoy: date,
        fecha_ejecucion: date,
        crecimiento: Crecimiento,
        serie: SerieIndices | None = None,
        normativa: Normativa | None = None,
    ) -> None:
        self.prestamo = prestamo
        self.hoy = hoy
        self.fecha = fecha_ejecucion
        self.crecimiento = crecimiento
        self.serie = serie or cargar_serie()
        self.normativa = normativa or cargar_normativa()
        self.base = calcular_cuadro(prestamo, self.serie, Supuestos(hoy=hoy))
        self.capital_pendiente = self.base.capital_pendiente(fecha_ejecucion)
        self.pagada = self.base.proxima(fecha_ejecucion) is None or self.capital_pendiente <= 0
        self._cache: dict[tuple[Decimal, str | None], EfectoAmortizacion] = {}
        self.cuadros_calculados = 1
        # Fee rate on this date (the caps are proportional to the amount repaid).
        self.porcentaje_comision = comision_maxima(prestamo, self.normativa, fecha_ejecucion, ONE).porcentaje

    def comision(self, importe: Decimal) -> Comision:
        return comision_maxima(self.prestamo, self.normativa, self.fecha, importe)

    def maximo_con(self, efectivo: Decimal) -> Decimal:
        """Largest repayment (to the cent) whose amount plus fee fits in ``efectivo``, capped at what is owed."""
        if efectivo <= 0 or self.pagada:
            return ZERO
        importe = min(self.capital_pendiente, (efectivo / (ONE + self.porcentaje_comision)).quantize(Decimal("0.01")))
        while importe > 0 and importe + self.comision(importe).importe > efectivo:
            importe -= Decimal("0.01")
        return max(importe, ZERO)

    def saltos_plazo(self, desde: Decimal, hasta: Decimal, *, maximo: int = 80) -> list[Decimal]:
        """Smallest repayments in [desde, hasta] at which «reduce term» removes one more instalment.

        The module rounds the new number of instalments up and recomputes the instalment on it
        (``calculo.cuotas_necesarias``), so the position jumps at each of these amounts: they are breakpoints of
        the mortgage lever. Found by bisection to the cent on the module's own function, with the rate,
        instalment and capital the schedule has right before the repayment."""
        fila = self.base.ultima_pagada(self.fecha)
        if fila is None or self.pagada or hasta <= desde:
            return []
        capital, tipo, cuota = fila.capital_pendiente, fila.tipo, fila.cuota
        periodo = self.prestamo.periodicidad_meses

        def cuotas(importe: Decimal) -> int:
            resto = capital - importe
            return cuotas_necesarias(resto, tipo, cuota, periodo) if resto > 0 else 0

        try:
            alto_n, bajo_n = cuotas(hasta), cuotas(desde)
        except ValueError:
            return []
        out: list[Decimal] = []
        for k in range(alto_n, bajo_n):
            if len(out) >= maximo:
                break
            bajo, alto = desde, hasta  # cuotas(bajo) > k >= cuotas(alto)
            while alto - bajo > CENT:
                medio = ((bajo + alto) / 2).quantize(CENT)
                if cuotas(medio) <= k:
                    alto = medio
                else:
                    bajo = medio
            out.append(alto)
        return sorted(out)

    def pagos_por_ano(self, cuadro: Cuadro, extra: AmortizacionRealizada | None) -> dict[int, Decimal]:
        extras = [*self.prestamo.amortizaciones_realizadas, *([extra] if extra is not None else [])]
        anos = range(self.fecha.year, self.crecimiento.horizonte.year + 1)
        out: dict[int, Decimal] = {}
        for ano in anos:
            capital, intereses = cuadro.pagos_del_ano(ano)
            out[ano] = capital + intereses + sum((a.importe for a in extras if a.fecha.year == ano), ZERO)
        return out

    def calculado(self, importe: Decimal, modalidad: str | None) -> bool:
        return (importe, modalidad if importe > 0 else None) in self._cache

    def aproximar(
        self, importe: Decimal, modalidad: str | None, nodos: list[Decimal]
    ) -> EfectoAmortizacion | EfectoAproximado:
        """The exact effect when it is cached or ``importe`` is a node; otherwise the interpolation between the
        nearest nodes (computed exactly). ``nodos`` is sorted and contains 0."""
        if importe <= 0 or self.calculado(importe, modalidad) or importe in nodos:
            return self.evaluar(importe, modalidad)
        bajo = max((n for n in nodos if n < importe), default=ZERO)
        alto = min((n for n in nodos if n > importe), default=None)
        if alto is None:
            return self.evaluar(importe, modalidad)
        a, b = self.evaluar(bajo, modalidad), self.evaluar(alto, modalidad)
        w = (importe - bajo) / (alto - bajo)
        anos = sorted(set(a.pagos_por_ano) | set(b.pagos_por_ano))
        return EfectoAproximado(
            importe=importe,
            valor=a.valor + (b.valor - a.valor) * w,
            pagos_por_ano={
                ano: a.pagos_por_ano.get(ano, ZERO)
                + (b.pagos_por_ano.get(ano, ZERO) - a.pagos_por_ano.get(ano, ZERO)) * w
                for ano in anos
            },
            efectivo=importe + self.comision(importe).importe,
            bajo=a,
            alto=b,
            peso=w,
        )

    def evaluar(self, importe: Decimal, modalidad: str | None) -> EfectoAmortizacion:
        clave = (importe, modalidad if importe > 0 else None)
        hecho = self._cache.get(clave)
        if hecho is not None:
            return hecho
        if importe <= 0:
            efecto = EfectoAmortizacion(
                importe=ZERO,
                modalidad=None,
                comision=self.comision(ZERO),
                intereses_ahorrados_horizonte=ZERO,
                intereses_ahorrados_total=ZERO,
                rendimiento_renunciado=ZERO,
                rendimiento_liberado=ZERO,
                valor=ZERO,
                pagos_por_ano=self.pagos_por_ano(self.base, None),
                cuadro=self.base,
            )
            self._cache[clave] = efecto
            return efecto
        if modalidad == "plazo":
            modo: Literal["plazo", "cuota"] = "plazo"
        elif modalidad == "cuota":
            modo = "cuota"
        else:
            raise ValueError("modalidad de amortización desconocida")
        importe = min(importe, self.capital_pendiente)
        extra = AmortizacionRealizada(fecha=self.fecha, importe=importe, modalidad=modo)
        cuadro = calcular_cuadro(self.prestamo, self.serie, Supuestos(hoy=self.hoy, amortizaciones=(extra,)))
        self.cuadros_calculados += 1
        comision = self.comision(importe)
        horizonte = self.crecimiento.horizonte
        dentro = [f for f in self.base.filas if self.fecha < f.fecha <= horizonte]
        nuevas = {f.fecha: f for f in cuadro.filas}
        ahorro_horizonte = ZERO
        liberado = ZERO
        for f in dentro:
            otra = nuevas.get(f.fecha)
            ahorro_horizonte += f.intereses - (otra.intereses if otra is not None else ZERO)
            recorte = f.cuota - (otra.cuota if otra is not None else ZERO)
            if recorte:
                liberado += recorte * (self.crecimiento.factor(f.fecha) - ONE)
        ahorro_total = self.base.intereses_desde(self.fecha) - cuadro.intereses_desde(self.fecha)
        renunciado = (importe + comision.importe) * (self.crecimiento.factor(self.fecha) - ONE)
        valor = ahorro_horizonte - comision.importe - renunciado + liberado
        efecto = EfectoAmortizacion(
            importe=importe,
            modalidad=modalidad,
            comision=comision,
            intereses_ahorrados_horizonte=ahorro_horizonte,
            intereses_ahorrados_total=ahorro_total,
            rendimiento_renunciado=renunciado,
            rendimiento_liberado=liberado,
            valor=valor,
            pagos_por_ano=self.pagos_por_ano(cuadro, extra),
            cuadro=cuadro,
        )
        self._cache[clave] = efecto
        return efecto


__all__ = ["Crecimiento", "EfectoAmortizacion", "EfectoAproximado", "PalancaHipoteca", "euros", "meses_entre"]
