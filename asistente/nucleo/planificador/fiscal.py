"""The planner's tax oracle: the household's IRPF for any allocation, computed by the tax optimiser.

The planner has no tax formula of its own. For each combination of pension contributions, donation and housing
payments it asks the optimiser's evaluator (:class:`hacienda_gpt.tax.optimizador._Optimizador`, the same code
behind ``optimizar_impuestos`` and ``POST /optimize``): it builds each spouse's return or the joint one with
:func:`conjunta._conjunta`, applies every limit read from the year's data, and runs :func:`calcular_irpf`. The
household files whichever way pays less (the filing mode is a free choice of the return, not a use of money),
and a donation goes to the recipient and donor that deduce most (``optimizador._destinos``).

The pension breakpoints are the optimiser's own (``_Optimizador.candidatos``: amounts that take a monitored base
exactly onto a threshold of the data, ±1 cent), so the planner's grid contains every point where the tax
saving per euro changes.

The transitional housing deduction (pre-2013 homes) reads ``pagos_vivienda_habitual_pre2013``: an early
repayment adds to that year's payments; a lower instalment lowers the following years'. The calculator caps
the base at ``vivienda_habitual_transitoria.base_maxima`` (``irpf.py``: ``min(pagos, base_maxima) * tipo``), so
returns are cached by the capped figure (``test_fiscal.py`` checks the equivalence on the calculator).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from hacienda_gpt.modulos.fiscal.calculo.optimizador import (
    DESTINO_RECURRENTE,
    PalancaNoDisponible,
    Restricciones,
    _Coordenada,
    _Optimizador,
    _Punto,
)
from hacienda_gpt.tax.irpf import IRPFInput, IRPFResult
from hacienda_gpt.tax.params import IRPFParams

ZERO = Decimal(0)
CENT = Decimal("0.01")
Donativo = tuple[str, int, Decimal]


@dataclass(frozen=True)
class Fiscal:
    """The household's IRPF for one allocation, filing the cheaper way."""

    total: Decimal  # sum of the returns' results (positive: to pay)
    cuota_liquida: Decimal
    base_imponible: Decimal  # general + savings, all returns
    conjunta: bool
    donativo: Donativo | None
    declaraciones: tuple[tuple[str, IRPFInput, IRPFResult], ...]

    @property
    def tributacion(self) -> str:
        return self.declaraciones[0][1].tributacion if self.conjunta else "individual"


class OraculoFiscal:
    def __init__(
        self,
        contribuyente: IRPFInput,
        conyuge: IRPFInput | None,
        *,
        conocidos: frozenset[str],
        conocidos_conyuge: frozenset[str],
        restricciones: Restricciones,
        fecha: date,
        estado_civil: str | None,
        params: IRPFParams,
        pagos_vivienda: Decimal,
        parte_conyuge: Decimal,
    ) -> None:
        self.contribuyente = contribuyente
        self.conyuge = conyuge
        self.conocidos = conocidos
        self.conocidos_conyuge = conocidos_conyuge
        self.restricciones = restricciones
        self.fecha = fecha
        self.estado_civil = estado_civil
        self.params = params
        self.parte_conyuge = parte_conyuge if conyuge is not None else ZERO
        self.tope_vivienda = params.vivienda_habitual_transitoria.base_maxima
        self._optimizadores: dict[tuple[Decimal, Decimal], _Optimizador] = {}
        self._resultados: dict[tuple[Any, ...], Fiscal] = {}
        self._rapidos: dict[tuple[Any, ...], Fiscal] = {}
        # The optimiser at the household's current housing payments: lever limits, notes and breakpoints.
        self.pagos_base = pagos_vivienda
        self.base = self._opt(pagos_vivienda)

    # ------------------------------------------------------------------ #

    def _reparto(self, total: Decimal) -> tuple[Decimal, Decimal]:
        if self.conyuge is None:
            return total, ZERO
        conyuge = (total * self.parte_conyuge).quantize(CENT)
        return total - conyuge, conyuge

    def _opt(self, pagos_total: Decimal) -> _Optimizador:
        propios, del_conyuge = self._reparto(pagos_total)
        clave = (min(propios, self.tope_vivienda), min(del_conyuge, self.tope_vivienda))
        opt = self._optimizadores.get(clave)
        if opt is None:
            contribuyente = self.contribuyente.model_copy(update={"pagos_vivienda_habitual_pre2013": clave[0]})
            conyuge = (
                self.conyuge.model_copy(update={"pagos_vivienda_habitual_pre2013": clave[1]})
                if self.conyuge is not None
                else None
            )
            opt = _Optimizador(
                contribuyente,
                conyuge,
                conocidos=self.conocidos,
                conocidos_conyuge=self.conocidos_conyuge,
                restricciones=self.restricciones,
                fecha=self.fecha,
                estado_civil=self.estado_civil,
                params=self.params,
                max_evaluaciones=10**9,
                avisos_previos=[],
            )
            self._optimizadores[clave] = opt
        return opt

    @property
    def evaluaciones(self) -> int:
        return sum(opt.evaluaciones for opt in self._optimizadores.values())

    @property
    def con_conyuge(self) -> bool:
        return self.conyuge is not None

    @property
    def max_pension(self) -> list[Decimal]:
        maximos = list(self.base.max_pension)
        return maximos + [ZERO] * (2 - len(maximos))

    @property
    def no_disponibles(self) -> list[PalancaNoDisponible]:
        return list(self.base.no_disponibles)

    @property
    def supuestos(self) -> list[str]:
        return list(self.base.supuestos)

    @property
    def avisos(self) -> list[str]:
        return list(self.base.avisos)

    # ------------------------------------------------------------------ #

    def impuesto(self, pagos_vivienda: Decimal, pension: tuple[Decimal, Decimal], donativo: Decimal = ZERO) -> Fiscal:
        """The household's IRPF with these housing payments, extra contributions and donation (cheaper filing)."""
        rapida = (pagos_vivienda, pension, donativo)
        hecho = self._rapidos.get(rapida)
        if hecho is not None:
            return hecho
        opt = self._opt(pagos_vivienda)
        propios, del_conyuge = self._reparto(pagos_vivienda)
        clave = (min(propios, self.tope_vivienda), min(del_conyuge, self.tope_vivienda), pension, donativo)
        hecho = self._resultados.get(clave)
        if hecho is not None:
            self._rapidos[rapida] = hecho
            return hecho
        mejor: tuple[tuple[Any, ...], Fiscal] | None = None
        for conjunta in opt.modos:
            aportaciones = self._en_conjunta(opt, pension) if conjunta else pension
            for don in self._donativos(opt, conjunta, donativo):
                ev = opt.evaluar(_Punto(conjunta=conjunta, pension=aportaciones, donativo=don))
                fiscal = Fiscal(
                    total=ev.total,
                    cuota_liquida=sum((r.cuota_liquida for _n, _i, r in ev.declaraciones), ZERO),
                    base_imponible=sum(
                        (r.base_imponible_general + r.base_imponible_ahorro for _n, _i, r in ev.declaraciones), ZERO
                    ),
                    conjunta=conjunta,
                    donativo=don,
                    declaraciones=ev.declaraciones,
                )
                # Least tax; then the current filing mode; then a fixed order of recipients.
                orden = (ev.total, 0 if conjunta == opt.base_conjunta else 1, don or ("", -1, ZERO))
                if mejor is None or orden < mejor[0]:
                    mejor = (orden, fiscal)
        assert mejor is not None
        self._resultados[clave] = self._rapidos[rapida] = mejor[1]
        return mejor[1]

    @staticmethod
    def _en_conjunta(opt: _Optimizador, pension: tuple[Decimal, Decimal]) -> tuple[Decimal, Decimal]:
        """In a joint return both spouses' extra contributions reduce the same base, each within its own limit
        (art. 84.2.1.º LIRPF); while each stays within its margin the tax depends only on their total. The
        optimiser's own search moves that total (user first, then the spouse); the planner does the same so the
        calculator runs once per total. The plan still reports each spouse's amount."""
        maximos = list(opt.max_pension) + [ZERO] * (2 - len(opt.max_pension))
        if not opt.con_conyuge or pension[0] > maximos[0] or pension[1] > maximos[1]:
            return pension
        total = pension[0] + pension[1]
        primero = min(total, maximos[0])
        return primero, total - primero

    def _donativos(self, opt: _Optimizador, conjunta: bool, importe: Decimal) -> Iterable[Donativo | None]:
        if importe <= 0:
            return [None]
        out: list[Donativo] = []
        anterior = self.restricciones.donativo_recurrente_importe_anterior
        for destino in opt.destinos:
            if destino.id == DESTINO_RECURRENTE:
                base = opt._j0 if conjunta else opt.individuales[0]
                if base is not None and base.donativos and not base.donativos_recurrentes:
                    continue
                if anterior is not None and importe < anterior:
                    continue
            for donante in opt._donantes(conjunta):
                out.append((destino.id, donante, importe))
        return out

    # ------------------------------------------------------------------ #
    # Breakpoints
    # ------------------------------------------------------------------ #

    def puntos_pension(self, i: int, otros: Iterable[tuple[Decimal, Decimal]] = ()) -> set[Decimal]:
        """Amounts of spouse ``i``'s extra contribution where the tax saving per euro may change: the optimiser's
        breakpoints at no contribution, at the maximum and at ``otros`` (pairs of contributions), in each filing
        mode."""
        opt = self.base
        maximo = self.max_pension[i]
        if maximo <= 0:
            return {ZERO}
        puntos: set[Decimal] = {ZERO, maximo}
        pares = [(ZERO, ZERO), (self.max_pension[0], self.max_pension[1]), *otros]
        for conjunta in opt.modos:
            coordenada = _Coordenada(f"pension_{i}", "pension", i, ZERO, maximo)
            for par in pares:
                punto = _Punto(conjunta=conjunta, pension=(par[0], par[1]))
                puntos |= set(opt.candidatos(punto, coordenada))
        return {p for p in puntos if ZERO <= p <= maximo}

    def puntos_donativo(self, minimo: Decimal, maximo: Decimal) -> set[Decimal]:
        """Donation amounts where the deduction rate or a cap changes (the optimiser's), within the range."""
        if maximo <= 0:
            return {ZERO}
        opt = self.base
        puntos: set[Decimal] = {minimo, maximo}
        if opt._lever_donativo and maximo > minimo:
            for conjunta in opt.modos:
                for _destino, _donante, importe in opt._candidatos_donativo(_Punto(conjunta=conjunta)):
                    puntos.add(importe)
        return {p for p in puntos if minimo <= p <= maximo}


__all__ = ["Fiscal", "OraculoFiscal"]
