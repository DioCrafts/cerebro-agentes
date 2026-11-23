"""Deterministic household planner: the best use of a sum of money among the household's levers.

The agent only states the problem (how much money, over what horizon, which constraints); this module computes.
Levers: early repayment of the mortgage (reduce term or instalment), a pension contribution per spouse, a
donation within the user's budget, and keeping the money liquid (the remainder), with an emergency cushion.

Objective — the household's NET POSITION at the horizon (euros of that date), against keeping all the money
liquid at the user's own alternative return (arithmetic, never advice):

    IRPF saved this year (computed for the combination by the tax optimiser's evaluator, filing the cheaper way)
  + change of the housing deduction in the following years (pre-2013 homes)
  + interest saved on the mortgage within the horizon − its fee − the return that money would have earned
    + the return of the instalment cut kept liquid                      (mortgage module: schedule and fee cap)
  − what is donated (and its return)
  − [«definitive» view only] the tax due when the pension plan is redeemed
  − the user's penalty per euro below the cushion (or the cushion is a hard limit)

Every euro figure of a module comes from that module's calculator on the scenario of the allocation; the
planner adds them only as parts of one net position whose arithmetic is written above (never two modules'
results computed on different scenarios). Pension contributions are a DEFERRAL (taxed on redemption, art.
17.2.a.3.ª LIRPF): both views are reported; the search maximises the one chosen.

Search: exhaustive enumeration of a grid per lever — zero, the maximum, every breakpoint the modules expose (the
optimiser's pension and donation thresholds, the housing-deduction cap, paying the loan off), a uniform step whose
size is reported, and for every combination the repayment that uses up the rest of the budget. Decimal throughout,
stable order and explicit tie-breaks (higher value, then less money committed, fewer levers, round amounts):
the same input gives the same plan.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from pydantic import ValidationError

from asistente.ambitos.fiscal.calculo.irpf import IRPFInput
from asistente.ambitos.fiscal.calculo.optimizador import (
    PlanOptimo,
    Restricciones,
    TextoLegal,
    conocidos_de,
    optimizar,
)
from asistente.ambitos.fiscal.calculo.params import load_params
from asistente.ambitos.fiscal.profile import (
    DATOS_CONYUGE,
    DatosInvalidosError,
    build_input_con_actividad,
    normalizar_datos_conyuge,
)
from asistente.ambitos.hipoteca.calculo import fmt_pct
from asistente.ambitos.hipoteca.modelo import Prestamo
from asistente.nucleo.modulos.perfil import valor_de_perfil
from asistente.nucleo.modulos.puentes import HECHO_VIVIENDA_PRE2013
from asistente.nucleo.planificador.ayudas import ContextoAyudas, Desplazamiento
from asistente.nucleo.planificador.fiscal import Fiscal, OraculoFiscal
from asistente.nucleo.planificador.hipoteca import Crecimiento, EfectoAmortizacion, EfectoAproximado, PalancaHipoteca
from asistente.nucleo.planificador.modelos import (
    NOMBRES_PALANCAS,
    PALANCAS,
    Asignacion,
    BusquedaPlan,
    Componente,
    EfectoAyuda,
    EntradaPlan,
    Escenario,
    LineaAsignacion,
    Modalidad,
    PalancaNoUsada,
    PalancaPlan,
    PlanHogar,
    ResumenHipoteca,
    Valoracion,
    VistaPensiones,
)
from asistente.nucleo.planificador.textos import TEXTOS

ZERO = Decimal(0)
ONE = Decimal(1)
CENT = Decimal("0.01")
# Grid points enumerated at most, and calcular_irpf runs the grid may need (each 0.1-0.5 ms): the automatic step
# is the finest one that keeps both within these caps.
MAX_COMBINACIONES = 40000
MAX_EVALUACIONES_IRPF = 3000
# Points of the uniform part of the repayment grid at most (each is a whole amortisation schedule, ~10 ms), and
# allocations of the first pass recomputed exactly (the repayment that uses up the money is ranked by
# interpolating between grid points; every allocation the plan may choose is then valued exactly).
MAX_PUNTOS_HIPOTECA = 25
RECALCULADAS = 40
PASOS = tuple(Decimal(x) for x in (10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000))
# Day the IRPF difference of a year is assumed to reach the household: the filing campaign of the next year.
_DIA_DECLARACION = (6, 30)


def _eur(importe: Decimal) -> str:
    texto = f"{abs(importe):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if importe < 0 else ''}{texto} €"


def _fecha(dia: date) -> str:
    return dia.strftime("%d/%m/%Y")


def _mas_anos(dia: date, anos: int) -> date:
    try:
        return dia.replace(year=dia.year + anos)
    except ValueError:  # 29 February
        return dia.replace(year=dia.year + anos, day=28)


def _rejilla(maximo: Decimal, paso: Decimal) -> set[Decimal]:
    """0, paso, 2·paso… below ``maximo``, and ``maximo``."""
    if maximo <= 0:
        return {ZERO}
    n = int((maximo / paso).to_integral_value(rounding=ROUND_FLOOR))
    return {paso * k for k in range(n + 1)} | {maximo}


def _alrededor(valor: Decimal) -> set[Decimal]:
    abajo, arriba = valor.quantize(CENT, rounding=ROUND_FLOOR), valor.quantize(CENT, rounding=ROUND_CEILING)
    return {abajo - CENT, abajo, arriba, arriba + CENT}


def _es_casado(estado_civil: Any) -> bool:
    return isinstance(estado_civil, str) and estado_civil.strip().lower().startswith("casad")


@dataclass(frozen=True)
class _Rapida:
    """What the search needs of an allocation."""

    total: Decimal
    factible: bool
    compromiso: Decimal


class Planificador:
    """One planning problem: inputs resolved, modules prepared, caches shared by the search and the report."""

    def __init__(
        self,
        entrada: EntradaPlan,
        *,
        facts: Mapping[str, Any],
        modulos_activos: Collection[str],
        hoy: date,
    ) -> None:
        self.entrada = e = entrada
        self.activos = frozenset(modulos_activos)
        self.perfil = {nombre: valor_de_perfil(nombre, valor) for nombre, valor in facts.items()}
        self.supuestos: list[str] = []
        self.avisos: list[str] = []
        self.no_usadas: list[PalancaNoUsada] = []
        self.no_modelado: list[str] = []

        # --- Dates, money, horizon -------------------------------------------------------------------
        self.fecha = e.fecha or hoy
        self.ejercicio = e.ejercicio or self.fecha.year
        meses_excedente = max(0, 12 - self.fecha.month) if self.fecha.year == self.ejercicio else 0
        if e.excedente_mensual and meses_excedente:
            self.supuestos.append(
                f"El excedente mensual ({_eur(e.excedente_mensual)}) se ahorra los {meses_excedente} meses que quedan "
                f"hasta diciembre y se usa todo junto en la fecha de ejecución."
            )
        elif e.excedente_mensual:
            self.avisos.append("No quedan meses de este ejercicio para ahorrar el excedente mensual: no se ha sumado.")
        self.total = e.importe_disponible + e.excedente_mensual * meses_excedente
        if e.fecha_ejecucion is not None:
            self.fecha_ejecucion = e.fecha_ejecucion
        elif e.excedente_mensual and meses_excedente:
            self.fecha_ejecucion = max(self.fecha, date(self.ejercicio, 12, 15))
        else:
            self.fecha_ejecucion = self.fecha
        self.horizonte = _mas_anos(self.fecha_ejecucion, e.horizonte_anos)
        self.crecimiento = Crecimiento.anual(e.rentabilidad_alternativa_pct, self.horizonte)
        self.g_ejecucion = self.crecimiento.factor(self.fecha_ejecucion)
        self.fecha_declaracion = date(self.ejercicio + 1, *_DIA_DECLARACION)
        self.g_declaracion = self.crecimiento.factor(self.fecha_declaracion)
        if not e.rentabilidad_alternativa_pct:
            self.supuestos.append(
                "No has indicado qué rentabilidad neta obtendrías con el dinero que no uses: se ha tomado el 0 % "
                "(dinero en una cuenta sin remunerar). Indícala para comparar con tu alternativa."
            )

        # --- Cushion -----------------------------------------------------------------------------------
        self.colchon: Decimal | None = e.colchon_objetivo
        if self.colchon is None and e.gastos_mensuales is not None:
            self.colchon = e.gastos_mensuales * e.meses_colchon
            self.supuestos.append(
                f"Colchón de emergencia: {e.meses_colchon} meses de tus gastos ({_eur(e.gastos_mensuales)} al mes) = "
                f"{_eur(self.colchon)}."
            )
        elif self.colchon is None:
            self.supuestos.append(
                "No has indicado un colchón de emergencia ni tus gastos mensuales: el plan puede usar todo el dinero. "
                "Indica uno (por ejemplo, seis meses de gastos) para que se respete."
            )
        self.liquidez_inicial = e.liquidez_actual + self.total
        if self.colchon is not None and e.penalizacion_liquidez is None:
            self.utilizable = max(ZERO, min(self.total, self.liquidez_inicial - self.colchon))
            if self.utilizable < self.total:
                self.supuestos.append(
                    f"Para mantener el colchón de {_eur(self.colchon)} (tienes {_eur(e.liquidez_actual)} aparte de "
                    f"este dinero), el plan puede usar como mucho {_eur(self.utilizable)}."
                )
        else:
            self.utilizable = self.total
            if self.colchon is not None:
                self.supuestos.append(
                    f"Cada euro por debajo del colchón ({_eur(self.colchon)}) resta {e.penalizacion_liquidez} € de la "
                    "posición neta (la penalización que has elegido)."
                )

        # --- Pension view --------------------------------------------------------------------------------
        if e.valorar_pensiones == "definitivo" and e.tipo_rescate_pct is None:
            raise DatosInvalidosError(
                "Para valorar el plan de pensiones como ahorro definitivo indica el tipo marginal que esperas pagar al "
                "rescatarlo (tipo_rescate_pct)."
            )
        self.vista: VistaPensiones = e.valorar_pensiones or (
            "definitivo" if e.tipo_rescate_pct is not None else "diferimiento"
        )

        self._cache_futura: dict[tuple[Decimal, str | None], Decimal] = {}
        self._preparar_fiscal()
        self._preparar_hipoteca()
        self._preparar_ayudas()

    # ------------------------------------------------------------------ #
    # Preparation
    # ------------------------------------------------------------------ #

    def _maximo(self, palanca: str) -> Decimal | None:
        for clave, valor in self.entrada.restricciones.maximos.items():
            if clave == palanca:
                return valor
        return None

    def _excluida(self, palanca: str) -> bool:
        return palanca in self.entrada.restricciones.excluidas

    def _motivo(self, palanca: str) -> str:
        r = self.entrada.restricciones
        motivo = next((texto for clave, texto in r.motivos.items() if clave == palanca), None)
        origen = f" ({r.origen})" if r.origen else ""
        return f"La has descartado{origen}" + (f": {motivo}" if motivo else ".")

    def _preparar_fiscal(self) -> None:
        e = self.entrada
        facts = dict(self.perfil)
        datos = dict(e.datos)
        estado_civil = datos.pop("estado_civil", None) or facts.get("estado_civil")
        self.params = load_params(self.ejercicio)
        contribuyente, actividad = build_input_con_actividad(ejercicio=self.ejercicio, facts=facts, datos=datos)
        if actividad is not None:
            self.avisos.append(actividad.aviso)
        conyuge_datos: dict[str, Any] | None = None
        if not e.sin_conyuge:
            guardado: dict[str, Any] | None = None
            if _es_casado(estado_civil) and facts.get(DATOS_CONYUGE) is not None:
                try:
                    guardado = normalizar_datos_conyuge(facts[DATOS_CONYUGE])
                except DatosInvalidosError as exc:
                    self.avisos.append(f"Las cifras de tu cónyuge guardadas no se han podido usar ({exc})")
            if e.conyuge is not None:
                conyuge_datos = dict(guardado or {})
                for campo, valor in e.conyuge.items():
                    if valor is None:
                        conyuge_datos.pop(campo, None)
                    else:
                        conyuge_datos[campo] = valor
            elif guardado is not None:
                conyuge_datos = guardado
                self.supuestos.append("Se han usado las cifras de tu cónyuge guardadas en tu expediente.")
        conyuge: IRPFInput | None = None
        conocidos_conyuge: set[str] = set()
        if conyuge_datos is not None:
            conyuge_datos = {k: v for k, v in conyuge_datos.items() if k != "estado_civil"}
            conyuge, actividad_b = build_input_con_actividad(
                ejercicio=self.ejercicio,
                datos={"comunidad_autonoma": contribuyente.comunidad_autonoma, **conyuge_datos},
            )
            if actividad_b is not None:
                self.avisos.append(f"Cónyuge: {actividad_b.aviso}")
            conocidos_conyuge = conocidos_de({}, conyuge_datos, conyuge)
        self.contribuyente = contribuyente
        self.conyuge = conyuge
        self.estado_civil = str(estado_civil) if estado_civil is not None else None
        self.conocidos = frozenset(conocidos_de(facts, datos, contribuyente))
        self.conocidos_conyuge = frozenset(conocidos_conyuge)
        self.pagos_hecho = contribuyente.pagos_vivienda_habitual_pre2013 + (
            conyuge.pagos_vivienda_habitual_pre2013 if conyuge is not None else ZERO
        )

    def _restricciones_fiscales(self, pagos_base: Decimal) -> Restricciones:
        e = self.entrada

        def tope(palanca: str, propio: Decimal | None) -> Decimal:
            if self._excluida(palanca):
                return ZERO
            valores = [self.utilizable] + [v for v in (propio, self._maximo(palanca)) if v is not None]
            return min(valores)

        donativo = not self._excluida("donativo")
        previsto = e.donativo_previsto if donativo else ZERO
        presupuesto = (
            (e.presupuesto_donativos if e.presupuesto_donativos is not None else previsto) if donativo else ZERO
        )
        maximo_donativo = self._maximo("donativo")
        if maximo_donativo is not None:
            presupuesto = min(presupuesto, maximo_donativo)
        presupuesto = min(presupuesto, self.utilizable)
        if previsto > presupuesto:
            self.avisos.append(
                f"El donativo previsto ({_eur(previsto)}) no cabe en lo que el plan puede usar ({_eur(presupuesto)}): "
                "se ha limitado a esa cifra."
            )
            previsto = presupuesto
        self.donativo_minimo, self.donativo_maximo = previsto, presupuesto
        return Restricciones(
            aportacion_maxima_pensiones=tope("plan_pensiones_contribuyente", e.aportacion_maxima_pensiones),
            aportacion_maxima_pensiones_conyuge=tope("plan_pensiones_conyuge", e.aportacion_maxima_pensiones_conyuge),
            acepta_iliquidez=e.acepta_iliquidez,
            donativo_previsto=previsto,
            presupuesto_donativos=presupuesto,
            donativo_recurrente_importe_anterior=e.donativo_recurrente_importe_anterior,
            modalidades_excluidas=list(e.restricciones.tributacion_excluida),
        )

    def _preparar_hipoteca(self) -> None:
        e = self.entrada
        crudo: Any = e.hipoteca
        origen = "consulta"
        if crudo is None and self.perfil.get("hipoteca_prestamo") is not None:
            if "hipoteca" in self.activos:
                crudo, origen = self.perfil["hipoteca_prestamo"], "expediente"
            else:
                self.no_usadas.append(
                    PalancaNoUsada(
                        id="amortizar_hipoteca",
                        nombre=NOMBRES_PALANCAS["amortizar_hipoteca"],
                        causa="modulo_inactivo",
                        detalle="El módulo de hipoteca no está activado: sus datos no se usan. Actívalo para incluir "
                        "la amortización anticipada en el plan.",
                    )
                )
        self.hipoteca: PalancaHipoteca | None = None
        self.prestamo: Prestamo | None = None
        if crudo is not None:
            try:
                self.prestamo = Prestamo.model_validate(crudo)
            except ValidationError as exc:
                errores = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors())
                raise DatosInvalidosError(f"Los datos de la hipoteca no son válidos: {errores}") from exc
            self.hipoteca = PalancaHipoteca(
                self.prestamo, hoy=self.fecha, fecha_ejecucion=self.fecha_ejecucion, crecimiento=self.crecimiento
            )
            self.avisos.extend(self.hipoteca.base.avisos)
            if origen == "consulta":
                self.supuestos.append("Se han usado los datos de la hipoteca de esta consulta.")
        elif not any(n.id == "amortizar_hipoteca" for n in self.no_usadas):
            self.no_usadas.append(
                PalancaNoUsada(
                    id="amortizar_hipoteca",
                    nombre=NOMBRES_PALANCAS["amortizar_hipoteca"],
                    causa="faltan_datos",
                    detalle="No constan los datos de una hipoteca (dato hipoteca_prestamo del módulo de hipoteca).",
                )
            )

        # The transitional housing deduction: from the user's figure, or what the bridge would propose.
        p = self.prestamo
        self.deduce_vivienda = self.pagos_hecho > 0
        base_hipoteca = self.hipoteca.evaluar(ZERO, None) if self.hipoteca is not None else None
        pagos_base = self.pagos_hecho
        if not self.deduce_vivienda and p is not None and p.adquisicion_antes_2013 is True and p.vivienda_habitual:
            assert base_hipoteca is not None
            pagos_base = base_hipoteca.pagos_por_ano.get(self.ejercicio, ZERO)
            self.deduce_vivienda = pagos_base > 0
            if self.deduce_vivienda:
                self.supuestos.append(
                    f"Deducción por vivienda habitual (compra anterior a 2013): no consta «{HECHO_VIVIENDA_PRE2013}»; "
                    f"se ha usado lo que pagas de hipoteca en {self.ejercicio} según el cuadro ({_eur(pagos_base)}), la "
                    "cifra que el módulo de hipoteca propone al módulo fiscal. Compruébala con el certificado del banco."
                )
        elif p is not None and p.adquisicion_antes_2013 is None and p.vivienda_habitual and not self.deduce_vivienda:
            self.avisos.append(
                "Si compraste tu vivienda habitual antes de 2013 y ya aplicabas la deducción, indícalo "
                "(adquisicion_antes_2013) o indica lo que pagas al año (pagos_vivienda_habitual_pre2013): amortizar "
                "podría deducir."
            )
        self.pagos_base = pagos_base
        self.base_hipoteca = base_hipoteca
        if self.deduce_vivienda and self.conyuge is not None and self.entrada.parte_hipoteca_conyuge > 0:
            self.supuestos.append(
                f"Pagos de la hipoteca: tu cónyuge paga el {self.entrada.parte_hipoteca_conyuge * 100:.0f} % y cada uno "
                "aplica la deducción por su parte en las declaraciones individuales (la cifra del expediente se toma "
                "como la de todo el hogar)."
            )
        elif self.deduce_vivienda and self.conyuge is not None:
            self.no_modelado.append(
                "Si la vivienda es de los dos y presentáis declaraciones separadas, cada uno deduce por lo que paga "
                "con su propio límite: aquí la deducción va entera a tu declaración. Indica parte_hipoteca_conyuge."
            )
        if self.deduce_vivienda:
            v = self.params.vivienda_habitual_transitoria
            if not v.fuente.verificado:
                self.avisos.append(
                    f"Deducción por vivienda: {v.fuente.nota or 'el porcentaje y la base máxima no están verificados.'}"
                )
            self.avisos.append(
                "La deducción por vivienda solo procede si compraste antes de 2013 y ya te la aplicabas entonces "
                "(disposición transitoria 18.ª LIRPF); lo amortizado también entra en su base, con el mismo límite."
            )

        self.restricciones_fiscales = self._restricciones_fiscales(pagos_base)
        self.oraculo = OraculoFiscal(
            self.contribuyente,
            self.conyuge,
            conocidos=self.conocidos,
            conocidos_conyuge=self.conocidos_conyuge,
            restricciones=self.restricciones_fiscales,
            fecha=self.fecha_ejecucion,
            estado_civil=self.estado_civil,
            params=self.params,
            pagos_vivienda=pagos_base,
            parte_conyuge=self.entrada.parte_hipoteca_conyuge,
        )
        self.supuestos.extend(s for s in self.oraculo.supuestos if "no has indicado cuánto puedes aportar" not in s)
        self.avisos.extend(self.oraculo.avisos)
        self.fiscal_base = self.oraculo.impuesto(pagos_base, (ZERO, ZERO), ZERO)

    def _preparar_ayudas(self) -> None:
        self.ayudas: ContextoAyudas | None = None
        self.margen_ayudas: Decimal | None = None
        if "ayudas" not in self.activos:
            self.no_modelado.append(
                "El módulo de ayudas no está activado: no se ha comprobado si el plan cambia alguna ayuda pública."
            )
            return
        self.ayudas = ContextoAyudas.desde_perfil(self.perfil, self.fecha)
        if self.entrada.proteger_ayudas:
            self.margen_ayudas = self.ayudas.margen_ingresos(max(ZERO, self.fiscal_base.cuota_liquida))

    # ------------------------------------------------------------------ #
    # Valuation of one allocation
    # ------------------------------------------------------------------ #

    def _efecto(self, a: Asignacion) -> EfectoAmortizacion | None:
        if self.hipoteca is None:
            return None
        return self.hipoteca.evaluar(a.amortizar_hipoteca, a.modalidad)

    def _efecto_busqueda(
        self, a: Asignacion, nodos: list[Decimal] | None
    ) -> EfectoAmortizacion | EfectoAproximado | None:
        if self.hipoteca is None:
            return None
        if nodos is None:
            return self.hipoteca.evaluar(a.amortizar_hipoteca, a.modalidad)
        return self.hipoteca.aproximar(a.amortizar_hipoteca, a.modalidad, nodos)

    def _pagos(self, efecto: EfectoAmortizacion | EfectoAproximado | None, ano: int) -> Decimal:
        if not self.deduce_vivienda or efecto is None or self.base_hipoteca is None:
            return self.pagos_base
        return max(
            ZERO,
            self.pagos_base + efecto.pagos_por_ano.get(ano, ZERO) - self.base_hipoteca.pagos_por_ano[self.ejercicio],
        )

    def _fiscal(self, a: Asignacion, efecto: EfectoAmortizacion | EfectoAproximado | None) -> Fiscal:
        return self.oraculo.impuesto(
            self._pagos(efecto, self.ejercicio),
            (a.plan_pensiones_contribuyente, a.plan_pensiones_conyuge),
            a.donativo,
        )

    def _anos_futuros(self) -> list[int]:
        return [
            ano for ano in range(self.ejercicio + 1, self.horizonte.year + 1) if date(ano, 12, 31) <= self.horizonte
        ]

    def _valor_vivienda_futura(self, efecto: EfectoAmortizacion | EfectoAproximado | None) -> Decimal:
        """The future housing-deduction effect at the horizon (cached; interpolated for an interpolated repayment)."""
        if not self.deduce_vivienda or efecto is None or efecto.importe <= 0:
            return ZERO
        if isinstance(efecto, EfectoAproximado):
            bajo, alto = self._valor_vivienda_futura(efecto.bajo), self._valor_vivienda_futura(efecto.alto)
            return bajo + (alto - bajo) * efecto.peso
        clave = (efecto.importe, efecto.modalidad)
        hecho = self._cache_futura.get(clave)
        if hecho is None:
            hecho = sum((ahorro * self._g_ano(ano) for ano, ahorro in self._vivienda_futura(efecto)), ZERO)
            self._cache_futura[clave] = hecho
        return hecho

    def _vivienda_futura(self, efecto: EfectoAmortizacion | EfectoAproximado | None) -> list[tuple[int, Decimal]]:
        """(year, IRPF saved that year) from the change of the housing payments, for the complete years in the
        horizon (the rest of the household's figures as this year, with this year's rules)."""
        if not self.deduce_vivienda or efecto is None or efecto.importe <= 0 or self.base_hipoteca is None:
            return []
        out: list[tuple[int, Decimal]] = []
        for ano in self._anos_futuros():
            antes = self.oraculo.impuesto(self._pagos(self.base_hipoteca, ano), (ZERO, ZERO), ZERO).total
            despues = self.oraculo.impuesto(self._pagos(efecto, ano), (ZERO, ZERO), ZERO).total
            if antes != despues:
                out.append((ano, antes - despues))
        return out

    def _efectivo(self, a: Asignacion, efecto: EfectoAmortizacion | EfectoAproximado | None) -> Decimal:
        hipoteca = efecto.efectivo if efecto is not None else ZERO
        return hipoteca + a.plan_pensiones_contribuyente + a.plan_pensiones_conyuge + a.donativo

    def _penalizacion(self, usado: Decimal) -> tuple[Decimal, Decimal, bool]:
        """(shortfall below the cushion, penalty, feasible)."""
        if self.colchon is None:
            return ZERO, ZERO, usado <= self.total
        falta = max(ZERO, self.colchon - (self.liquidez_inicial - usado))
        if self.entrada.penalizacion_liquidez is None:
            return falta, ZERO, usado <= self.utilizable
        return falta, falta * self.entrada.penalizacion_liquidez, usado <= self.total

    def rapida(self, a: Asignacion, nodos: list[Decimal] | None = None) -> _Rapida:
        """The objective of an allocation (and whether it is allowed), using the caches. With ``nodos`` (the
        repayment grid) a repayment off the grid is interpolated (search ranking only)."""
        efecto = self._efecto_busqueda(a, nodos)
        fiscal = self._fiscal(a, efecto)
        usado = self._efectivo(a, efecto)
        _falta, penalizacion, factible = self._penalizacion(usado)
        valor = (self.fiscal_base.total - fiscal.total) * self.g_declaracion
        valor += self._valor_vivienda_futura(efecto)
        if efecto is not None:
            valor += efecto.valor
        valor -= a.donativo * self.g_ejecucion
        pension = a.plan_pensiones_contribuyente + a.plan_pensiones_conyuge
        if self.vista == "definitivo" and pension:
            assert self.entrada.tipo_rescate_pct is not None
            valor -= self.entrada.tipo_rescate_pct / 100 * pension * self.g_ejecucion
        valor -= penalizacion
        if (
            self.margen_ayudas is not None
            and self.fiscal_base.cuota_liquida - fiscal.cuota_liquida >= self.margen_ayudas
        ):
            factible = False
        return _Rapida(total=valor, factible=factible, compromiso=usado)

    def _g_ano(self, ano: int) -> Decimal:
        return self.crecimiento.factor(date(ano + 1, *_DIA_DECLARACION))

    def valorar(self, a: Asignacion) -> Valoracion:
        """The allocation's net position at the horizon, line by line, in both views of the pension."""
        efecto = self._efecto(a)
        fiscal = self._fiscal(a, efecto)
        usado = self._efectivo(a, efecto)
        falta, penalizacion, _factible = self._penalizacion(usado)
        ej, siguiente = self.ejercicio, self.ejercicio + 1
        pension = a.plan_pensiones_contribuyente + a.plan_pensiones_conyuge
        comps: list[Componente] = []
        ahorro_irpf = self.fiscal_base.total - fiscal.total
        parte_pension = ZERO
        if pension:
            sin_pension = self.oraculo.impuesto(self._pagos(efecto, ej), (ZERO, ZERO), a.donativo)
            parte_pension = sin_pension.total - fiscal.total
            comps.append(
                Componente(
                    concepto=f"IRPF {ej}: menos impuesto por la aportación a planes de pensiones (se nota al declarar, "
                    f"junio de {siguiente}; tributa al rescatar)",
                    importe=parte_pension * self.g_declaracion,
                    naturaleza="diferimiento",
                    palanca="plan_pensiones",
                    modulo="fiscal",
                    referencia="art. 51 LIRPF",
                )
            )
        resto = ahorro_irpf - parte_pension
        if resto:
            motivos = [
                texto
                for texto, hay in (
                    ("la deducción por vivienda", self.deduce_vivienda and a.amortizar_hipoteca > 0),
                    ("el donativo", a.donativo > 0),
                )
                if hay
            ]
            comps.append(
                Componente(
                    concepto=f"IRPF {ej}: {'menos impuesto por ' + ' y '.join(motivos) if motivos else 'diferencia de impuesto'}"
                    f" (se nota al declarar, junio de {siguiente})",
                    importe=resto * self.g_declaracion,
                    naturaleza="definitivo",
                    modulo="fiscal",
                    referencia="LIRPF",
                )
            )
        futuras = self._vivienda_futura(efecto)
        if futuras:
            comps.append(
                Componente(
                    concepto=f"Deducción por vivienda de {futuras[0][0]} a {futuras[-1][0]}: cambio por lo que pagas de "
                    "hipoteca esos años",
                    importe=sum((ahorro * self._g_ano(ano) for ano, ahorro in futuras), ZERO),
                    naturaleza="definitivo",
                    palanca="amortizar_hipoteca",
                    modulo="fiscal",
                    referencia="disposición transitoria 18.ª LIRPF",
                )
            )
        if efecto is not None and efecto.importe > 0:
            comps.append(
                Componente(
                    concepto=f"Intereses de la hipoteca que te ahorras hasta el {_fecha(self.horizonte)}",
                    importe=efecto.intereses_ahorrados_horizonte,
                    naturaleza="definitivo",
                    palanca="amortizar_hipoteca",
                    modulo="hipoteca",
                )
            )
            if efecto.comision.importe:
                comps.append(
                    Componente(
                        concepto="Comisión por amortización anticipada (máximo legal)",
                        importe=-efecto.comision.importe,
                        naturaleza="coste",
                        palanca="amortizar_hipoteca",
                        modulo="hipoteca",
                        referencia=", ".join(efecto.comision.citas) or None,
                    )
                )
            if efecto.rendimiento_renunciado:
                comps.append(
                    Componente(
                        concepto=f"Rendimiento que daría ese dinero ({fmt_pct(self.entrada.rentabilidad_alternativa_pct)} "
                        "anual, tu supuesto)",
                        importe=-efecto.rendimiento_renunciado,
                        naturaleza="rendimiento",
                        palanca="amortizar_hipoteca",
                        modulo="planificador",
                    )
                )
            if efecto.rendimiento_liberado:
                comps.append(
                    Componente(
                        concepto="Rendimiento de lo que dejas de pagar cada mes, si lo guardas",
                        importe=efecto.rendimiento_liberado,
                        naturaleza="rendimiento",
                        palanca="amortizar_hipoteca",
                        modulo="planificador",
                    )
                )
        if a.donativo:
            comps.append(
                Componente(
                    concepto="Donativo (dinero que sale del hogar)",
                    importe=-a.donativo,
                    naturaleza="coste",
                    palanca="donativo",
                    modulo="planificador",
                )
            )
            if self.g_ejecucion != ONE:
                comps.append(
                    Componente(
                        concepto="Rendimiento que daría lo donado",
                        importe=-a.donativo * (self.g_ejecucion - ONE),
                        naturaleza="rendimiento",
                        palanca="donativo",
                        modulo="planificador",
                    )
                )
        if penalizacion:
            comps.append(
                Componente(
                    concepto=f"Penalización por quedar {_eur(falta)} por debajo del colchón",
                    importe=-penalizacion,
                    naturaleza="penalizacion",
                    modulo="planificador",
                )
            )
        diferimiento = sum((c.importe for c in comps), ZERO)
        rescate = ZERO
        if pension:
            tipo = (
                self.entrada.tipo_rescate_pct / 100
                if self.entrada.tipo_rescate_pct is not None
                else (parte_pension / pension if pension else ZERO)
            )
            rescate = tipo * pension * self.g_ejecucion
        definitivo = diferimiento - rescate
        total = definitivo if self.vista == "definitivo" else diferimiento
        if rescate:
            comps.append(
                Componente(
                    concepto="Impuesto al rescatar el plan de pensiones (solo en la vista de ahorro definitivo)",
                    importe=-rescate,
                    naturaleza="diferimiento",
                    palanca="plan_pensiones",
                    modulo="planificador",
                    referencia="art. 17.2.a).3.ª LIRPF",
                )
            )
        q = [c.model_copy(update={"importe": c.importe.quantize(CENT)}) for c in comps]
        return Valoracion(
            asignacion=a,
            usado=usado,
            liquido=self.liquidez_inicial - usado,
            falta_colchon=falta,
            componentes=q,
            total=total.quantize(CENT),
            total_diferimiento=diferimiento.quantize(CENT),
            total_definitivo=definitivo.quantize(CENT),
            valor_actual=(total / self.crecimiento.factor(self.fecha)).quantize(CENT),
            impuesto_ejercicio=fiscal.total,
            tributacion=fiscal.tributacion,
            cuota_liquida=fiscal.cuota_liquida,
            base_imponible=fiscal.base_imponible,
        )

    # ------------------------------------------------------------------ #
    # Candidates and search
    # ------------------------------------------------------------------ #

    def modalidades(self) -> list[Modalidad]:
        todas: tuple[Modalidad, ...] = ("plazo", "cuota")
        return [m for m in todas if m not in self.entrada.restricciones.modalidades_excluidas]

    def maximo_hipoteca(self) -> Decimal:
        if self.hipoteca is None or self._excluida("amortizar_hipoteca") or not self.modalidades():
            return ZERO
        tope = self.utilizable
        maximo = self._maximo("amortizar_hipoteca")
        if maximo is not None:
            tope = min(tope, maximo)
        return self.hipoteca.maximo_con(tope)

    def maximos_pension(self) -> tuple[Decimal, Decimal]:
        m = self.oraculo.max_pension
        return (
            ZERO if self._excluida("plan_pensiones_contribuyente") else m[0],
            ZERO if self._excluida("plan_pensiones_conyuge") or self.conyuge is None else m[1],
        )

    def _puntos_hipoteca(self, paso: Decimal) -> set[Decimal]:
        maximo = self.maximo_hipoteca()
        if maximo <= 0:
            return {ZERO}
        assert self.hipoteca is not None
        puntos = _rejilla(maximo, paso)
        if self.hipoteca.capital_pendiente <= maximo:
            puntos.add(self.hipoteca.capital_pendiente)
        if self.deduce_vivienda and self.base_hipoteca is not None:
            # Where this year's payments reach the housing deduction's base cap (in each return).
            tope = self.oraculo.tope_vivienda
            s = self.oraculo.parte_conyuge
            actuales = self.pagos_base
            if s < 1:
                puntos |= _alrededor((tope - (1 - s) * actuales) / (1 - s))
            if s > 0:
                puntos |= _alrededor((tope - s * actuales) / s)
        return {p for p in puntos if ZERO <= p <= maximo}

    def _paso_automatico(self, n_pension: int, n_donativo: int) -> Decimal:
        m_p = self.maximos_pension()
        n_h = MAX_PUNTOS_HIPOTECA + 6 if self.maximo_hipoteca() > 0 else 1
        for paso in PASOS:
            n_p0 = len(_rejilla(m_p[0], paso)) + n_pension
            n_p1 = len(_rejilla(m_p[1], paso)) + n_pension if m_p[1] > 0 else 1
            combinaciones = n_donativo * n_p0 * n_p1 * (n_h + 1) * len(self.modalidades())
            claves = (min(n_h, 12) * 2 + 1) if self.deduce_vivienda else 1
            # Separate returns: one run per spouse and amount; a joint return: one per total (see fiscal.py).
            sumas = n_p0 + n_p1 + (n_p0 * n_p1) // 4 if m_p[1] > 0 else n_p0
            irpf = n_donativo * claves * (n_p0 + n_p1 + sumas)
            if combinaciones <= MAX_COMBINACIONES and irpf <= MAX_EVALUACIONES_IRPF:
                return paso
        return PASOS[-1]

    def paso_hipoteca(self, paso: Decimal) -> Decimal:
        """The repayment grid's step: the common one, coarser when it would exceed ``MAX_PUNTOS_HIPOTECA``."""
        maximo = self.maximo_hipoteca()
        for candidato in (paso, *(p for p in PASOS if p > paso)):
            if len(_rejilla(maximo, candidato)) <= MAX_PUNTOS_HIPOTECA:
                return candidato
        return PASOS[-1]

    def candidatos(self, paso: Decimal | None = None) -> tuple[Decimal, dict[str, list[Decimal]]]:
        m_p = self.maximos_pension()
        puntos_p = [
            {p for p in self.oraculo.puntos_pension(i) if p <= m_p[i]} if m_p[i] > 0 else {ZERO} for i in (0, 1)
        ]
        donativos = (
            sorted(self.oraculo.puntos_donativo(self.donativo_minimo, self.donativo_maximo))
            if self.donativo_maximo > 0 and self.oraculo.base._lever_donativo
            else [ZERO]
        )
        if paso is None:
            paso = self.entrada.resolucion or self._paso_automatico(
                max(len(puntos_p[0]), len(puntos_p[1])), len(donativos)
            )
        p0 = sorted((_rejilla(m_p[0], paso) | puntos_p[0]) if m_p[0] > 0 else {ZERO})
        p1 = sorted((_rejilla(m_p[1], paso) | puntos_p[1]) if m_p[1] > 0 else {ZERO})
        rejilla = {
            "amortizar_hipoteca": sorted(self._puntos_hipoteca(self.paso_hipoteca(paso))),
            "amortizar_hipoteca_plazo": [],
            "plan_pensiones_contribuyente": p0,
            "plan_pensiones_conyuge": p1,
            "donativo": donativos,
        }
        tope = self.maximo_hipoteca()
        if tope > 0 and "plazo" in self.modalidades():
            assert self.hipoteca is not None
            # Where «reduce term» drops one more instalment, around every amount a fill can take (the money left
            # after the pensions and the donation), plus one segment below.
            desde = max(ZERO, self.utilizable - m_p[0] - m_p[1] - donativos[-1])
            saltos = self.hipoteca.saltos_plazo(max(ZERO, desde - paso * 10), tope)
            if saltos:
                previo = self.hipoteca.saltos_plazo(ZERO, saltos[0], maximo=1) if saltos[0] > 0 else []
                saltos = [*previo[-1:], *saltos]
            puntos = {x for s in saltos for x in (s, s - CENT) if ZERO < x <= tope}
            rejilla["amortizar_hipoteca_plazo"] = sorted(puntos - set(rejilla["amortizar_hipoteca"]))
        return paso, rejilla

    def _orden(self, a: Asignacion, r: _Rapida) -> tuple[Any, ...]:
        importes = [a.amortizar_hipoteca, a.plan_pensiones_contribuyente, a.plan_pensiones_conyuge, a.donativo]
        return (
            -r.total.quantize(CENT),
            r.compromiso.quantize(ONE),
            sum(1 for v in importes if v),
            sum(1 for v in importes if v != v.to_integral_value()),
            importes,
            ["plazo", "cuota", None].index(a.modalidad),
        )

    def _nodos(self, rejilla: dict[str, list[Decimal]]) -> dict[str | None, list[Decimal]]:
        """Repayment amounts computed exactly per modality (interpolation nodes of the first pass)."""
        base = rejilla["amortizar_hipoteca"]
        return {
            "plazo": sorted({*base, *rejilla.get("amortizar_hipoteca_plazo", [])}),
            "cuota": list(base),
            None: [ZERO],
        }

    def _relleno(self, resto: Decimal, cache: dict[Decimal, Decimal]) -> Decimal:
        """The repayment (rounded down to the euro, or the whole debt) that uses ``resto`` with its fee."""
        tope = self.maximo_hipoteca()
        if self.hipoteca is None or tope <= 0 or resto <= 0:
            return ZERO
        clave = resto.quantize(ONE, rounding=ROUND_FLOOR)
        if clave not in cache:
            relleno = self.hipoteca.maximo_con(min(clave, tope))
            cache[clave] = relleno if relleno == tope else relleno.quantize(ONE, rounding=ROUND_FLOOR)
        return cache[clave]

    def _efectivo_hipoteca(self, importe: Decimal) -> Decimal:
        if self.hipoteca is None or importe <= 0:
            return ZERO
        return importe + self.hipoteca.comision(importe).importe

    def asignaciones(self, rejilla: dict[str, list[Decimal]]) -> Iterable[Asignacion]:
        """Every allocation of the grid within the budget; for each, the repayment that uses the rest of the
        money (to the euro: what is left stays liquid); and, for each repayment of the grid, the contribution of
        one spouse that uses the rest."""
        nodos = self._nodos(rejilla)
        tope_h = self.maximo_hipoteca()
        m_p = self.maximos_pension()
        rellenos: dict[Decimal, Decimal] = {}
        vistas: set[tuple[Any, ...]] = set()

        def nueva(h: Decimal, modo: Modalidad | None, p0: Decimal, p1: Decimal, d: Decimal) -> Asignacion | None:
            clave = (h, modo, p0, p1, d)
            if clave in vistas:
                return None
            vistas.add(clave)
            return Asignacion(
                amortizar_hipoteca=h,
                modalidad=modo,
                plan_pensiones_contribuyente=p0,
                plan_pensiones_conyuge=p1,
                donativo=d,
            )

        for d in rejilla["donativo"]:
            for p0 in rejilla["plan_pensiones_contribuyente"]:
                if d + p0 > self.utilizable:
                    continue
                for p1 in rejilla["plan_pensiones_conyuge"]:
                    resto = self.utilizable - d - p0 - p1
                    if resto < 0:
                        continue
                    relleno = self._relleno(resto, rellenos) if tope_h > 0 else ZERO
                    a = nueva(ZERO, None, p0, p1, d)
                    if a is not None:
                        yield a
                    for modo in self.modalidades() if relleno > 0 else []:
                        for h in sorted({h for h in nodos[modo] if ZERO < h <= relleno} | {relleno}):
                            a = nueva(h, modo, p0, p1, d)
                            if a is not None:
                                yield a
            # The contribution that uses the money left by each repayment of the grid.
            if tope_h > 0 and (m_p[0] > 0 or m_p[1] > 0):
                for modo in self.modalidades():
                    for h in nodos[modo]:
                        resto = self.utilizable - d - self._efectivo_hipoteca(h)
                        if h <= 0 or resto <= 0:
                            continue
                        for p1 in rejilla["plan_pensiones_conyuge"]:
                            p0 = min(m_p[0], resto - p1).quantize(ONE, rounding=ROUND_FLOOR)
                            if p0 > 0:
                                a = nueva(h, modo, p0, p1, d)
                                if a is not None:
                                    yield a
                        for p0 in rejilla["plan_pensiones_contribuyente"]:
                            p1 = min(m_p[1], resto - p0).quantize(ONE, rounding=ROUND_FLOOR)
                            if p1 > 0:
                                a = nueva(h, modo, p0, p1, d)
                                if a is not None:
                                    yield a

    def _mejor_exacta(self, opciones: Iterable[Asignacion]) -> tuple[tuple[Any, ...], Asignacion] | None:
        mejor: tuple[tuple[Any, ...], Asignacion] | None = None
        for a in opciones:
            if a.plan_pensiones_contribuyente < 0 or a.plan_pensiones_conyuge < 0 or a.amortizar_hipoteca < 0:
                continue
            r = self.rapida(a)
            if not r.factible:
                continue
            orden = self._orden(a, r)
            if mejor is None or orden < mejor[0]:
                mejor = (orden, a)
        return mejor

    def buscar(self, rejilla: dict[str, list[Decimal]], paso: Decimal) -> tuple[Asignacion, int, int, int]:
        """Exhaustive pass over the grid (repayments off the grid ranked by interpolation), the best
        ``RECALCULADAS`` allocations valued exactly, and a local polish around the best one.
        Returns (best, allocations enumerated, recomputed, polish evaluations)."""
        nodos = self._nodos(rejilla)
        primeras: list[tuple[tuple[Any, ...], Asignacion]] = []
        n = 0
        for a in self.asignaciones(rejilla):
            n += 1
            r = self.rapida(a, nodos[a.modalidad])
            if not r.factible:
                continue
            primeras.append((self._orden(a, r), a))
            if len(primeras) > 4 * RECALCULADAS:
                primeras.sort(key=lambda x: x[0])
                del primeras[RECALCULADAS:]
        primeras.sort(key=lambda x: x[0])
        mejor = self._mejor_exacta(a for _o, a in primeras[:RECALCULADAS])
        if mejor is None:
            return Asignacion(), n, 0, 0
        pulido, pasos = self.pulir(mejor, rejilla, paso)
        return pulido[1], n, min(len(primeras), RECALCULADAS), pasos

    def pulir(
        self, mejor: tuple[tuple[Any, ...], Asignacion], rejilla: dict[str, list[Decimal]], paso: Decimal
    ) -> tuple[tuple[Any, ...], int]:
        """Coordinate polish of the best allocation, valued exactly: each contribution within one grid step
        around its value (at a tenth of the step) and at the optimiser's breakpoints recomputed there, with the
        repayment kept or refilled; the repayment within one step of its grid. Never worse than ``mejor``."""
        fino = max(ONE, (paso / 10).quantize(ONE, rounding=ROUND_FLOOR))
        m_p = self.maximos_pension()
        paso_h = self.paso_hipoteca(paso)
        fino_h = max(ONE, (paso_h / 10).quantize(ONE, rounding=ROUND_FLOOR))
        saltos = rejilla.get("amortizar_hipoteca_plazo", [])
        rellenos: dict[Decimal, Decimal] = {}
        evaluaciones = 0
        for _ in range(4):
            a = mejor[1]
            opciones: list[Asignacion] = []
            pension = (a.plan_pensiones_contribuyente, a.plan_pensiones_conyuge)
            for i, campo in enumerate(("plan_pensiones_contribuyente", "plan_pensiones_conyuge")):
                if m_p[i] <= 0:
                    continue
                actual = pension[i]
                valores = {actual + fino * k for k in range(-10, 11)}
                valores |= self.oraculo.puntos_pension(i, otros=[pension])
                for v in sorted(x for x in valores if ZERO <= x <= m_p[i]):
                    b = a.model_copy(update={campo: v})
                    opciones.append(b)
                    if self.maximo_hipoteca() > 0:
                        resto = self.utilizable - b.donativo - b.plan_pensiones_contribuyente - b.plan_pensiones_conyuge
                        relleno = self._relleno(resto, rellenos)
                        for modo in self.modalidades():
                            candidatos = {relleno} | {x for x in saltos if x <= relleno and modo == "plazo"}
                            for h in sorted(candidatos)[-2:]:
                                if h > 0:
                                    opciones.append(b.model_copy(update={"amortizar_hipoteca": h, "modalidad": modo}))
            if a.amortizar_hipoteca > 0:
                for k in range(-10, 11):
                    h = a.amortizar_hipoteca + fino_h * k
                    if ZERO < h <= self.maximo_hipoteca():
                        opciones.append(a.model_copy(update={"amortizar_hipoteca": h}))
            evaluaciones += len(opciones)
            nuevo = self._mejor_exacta([a, *opciones])
            if nuevo is None or nuevo[1] == a:
                break
            mejor = nuevo
        return mejor, evaluaciones

    def aislada(self, palanca: str, rejilla: dict[str, list[Decimal]]) -> tuple[Asignacion, Decimal]:
        """The best value of one lever used alone (the rest of the money kept liquid)."""
        opciones: list[Asignacion] = []
        if palanca == "amortizar_hipoteca":
            tope = self.maximo_hipoteca()
            assert self.hipoteca is not None
            importes = set(rejilla[palanca]) | {tope}
            for h in sorted(importes):
                modos: list[Modalidad | None] = list(self.modalidades()) if h > 0 else [None]
                for modo in modos:
                    opciones.append(Asignacion(amortizar_hipoteca=h, modalidad=modo))
        elif palanca == "donativo":
            opciones = [Asignacion(donativo=d) for d in rejilla[palanca] if d > 0] or [Asignacion()]
        else:
            opciones = [Asignacion.model_validate({palanca: v}) for v in rejilla[palanca]]
        mejor: tuple[tuple[Any, ...], Asignacion, Decimal] | None = None
        for a in opciones:
            r = self.rapida(a)
            if not r.factible:
                continue
            orden = self._orden(a, r)
            if mejor is None or orden < mejor[0]:
                mejor = (orden, a, r.total)
        if mejor is None:
            return Asignacion(), ZERO
        return mejor[1], mejor[2]


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


def _usadas(a: Asignacion) -> list[str]:
    return [p for p in PALANCAS if a.importe(p) > 0]


def _detalle(planif: Planificador, palanca: str, a: Asignacion, efecto: EfectoAmortizacion | None) -> str:
    if palanca == "amortizar_hipoteca" and efecto is not None:
        modo = "reduciendo plazo" if a.modalidad == "plazo" else "reduciendo cuota"
        comision = f" + {_eur(efecto.comision.importe)} de comisión (máximo legal)" if efecto.comision.importe else ""
        return f"Amortizar {_eur(a.amortizar_hipoteca)} el {_fecha(planif.fecha_ejecucion)}, {modo}{comision}."
    if palanca.startswith("plan_pensiones"):
        quien = "a tu plan" if palanca.endswith("contribuyente") else "al plan de tu cónyuge"
        return f"Aportar {_eur(a.importe(palanca))} {quien} antes del 31/12/{planif.ejercicio}."
    if palanca == "donativo":
        return f"Donar {_eur(a.donativo)} antes del 31/12/{planif.ejercicio} (destino en el plan fiscal)."
    return ""


def _naturaleza(palanca: str) -> str:
    return {
        "amortizar_hipoteca": "ahorro definitivo (intereses) e irreversible",
        "plan_pensiones_contribuyente": "diferimiento (tributa al rescatar) e ilíquido hasta la jubilación",
        "plan_pensiones_conyuge": "diferimiento (tributa al rescatar) e ilíquido hasta la jubilación",
        "donativo": "requiere gastar: coste neto",
    }[palanca]


def _referencias(planif: Planificador, palanca: str, efecto: EfectoAmortizacion | None) -> list[str]:
    if palanca == "amortizar_hipoteca":
        refs = list(efecto.comision.citas) if efecto is not None else []
        if planif.hipoteca is not None:
            refs.append(planif.hipoteca.normativa.reembolso_anticipado.cita_corta())
        if planif.deduce_vivienda:
            refs.append(planif.params.vivienda_habitual_transitoria.fuente.referencia)
        return refs
    if palanca.startswith("plan_pensiones"):
        return [planif.params.prevision_social.fuente.referencia, "art. 17.2.a).3.ª LIRPF"]
    return [planif.params.donativos.fuente.referencia]


def _plan_fiscal(planif: Planificador, a: Asignacion) -> PlanOptimo | None:
    """The tax optimiser's own plan for the chosen contributions, donation and housing payments: its trace,
    citations and requirements are the fiscal part of the household plan."""
    efecto = planif._efecto(a)
    pagos = planif._pagos(efecto, planif.ejercicio)
    propios, del_conyuge = planif.oraculo._reparto(pagos)
    contribuyente = planif.contribuyente.model_copy(update={"pagos_vivienda_habitual_pre2013": propios})
    conyuge = (
        planif.conyuge.model_copy(update={"pagos_vivienda_habitual_pre2013": del_conyuge})
        if planif.conyuge is not None
        else None
    )
    pension = a.plan_pensiones_contribuyente + a.plan_pensiones_conyuge
    r = planif.restricciones_fiscales
    restricciones = Restricciones(
        aportacion_maxima_pensiones=a.plan_pensiones_contribuyente,
        aportacion_maxima_pensiones_conyuge=a.plan_pensiones_conyuge,
        acepta_iliquidez=True if pension else planif.entrada.acepta_iliquidez,
        donativo_previsto=a.donativo,
        presupuesto_donativos=a.donativo,
        donativo_recurrente_importe_anterior=r.donativo_recurrente_importe_anterior,
        modalidades_excluidas=r.modalidades_excluidas,
    )
    try:
        return optimizar(
            contribuyente,
            fecha=planif.fecha_ejecucion,
            conyuge=conyuge,
            conocidos=planif.conocidos,
            conocidos_conyuge=planif.conocidos_conyuge,
            restricciones=restricciones,
            estado_civil=planif.estado_civil,
            params=planif.params,
        )
    except DatosInvalidosError:
        return None


def _explicar_interaccion(
    planif: Planificador,
    a: Asignacion,
    palancas: list[PalancaPlan],
    aislados: dict[str, Asignacion],
) -> list[str]:
    lineas: list[str] = []
    usadas = _usadas(a)
    for p in palancas:
        if p.valor_marginal != p.valor_aislado:
            lineas.append(
                f"«{p.nombre}» vale {_eur(p.valor_marginal)} dentro del plan y {_eur(p.valor_aislado)} sola "
                f"(con {_eur(p.importe_aislado)})."
            )
    # Shared money.
    efectivo_aislado = ZERO
    for palanca in usadas:
        sola = aislados.get(palanca)
        if sola is not None:
            efectivo_aislado += planif._efectivo(sola, planif._efecto(sola))
    if efectivo_aislado > planif.utilizable and len(usadas) > 1:
        lineas.append(
            f"Por separado, cada palanca usaría {_eur(efectivo_aislado)} en total y el plan solo puede usar "
            f"{_eur(planif.utilizable)}: el dinero va donde cada euro rinde más."
        )
    # Tax coupling between the housing deduction and the pension contribution (computed, not assumed).
    pension = (a.plan_pensiones_contribuyente, a.plan_pensiones_conyuge)
    if planif.deduce_vivienda and a.amortizar_hipoteca > 0 and sum(pension, ZERO) > 0:
        efecto = planif._efecto(a)
        pagos_plan = planif._pagos(efecto, planif.ejercicio)
        o = planif.oraculo
        t_nada = o.impuesto(planif.pagos_base, (ZERO, ZERO), a.donativo).total
        t_h = o.impuesto(pagos_plan, (ZERO, ZERO), a.donativo).total
        t_p = o.impuesto(planif.pagos_base, pension, a.donativo).total
        t_ambas = o.impuesto(pagos_plan, pension, a.donativo).total
        acoplamiento = (t_nada - t_ambas) - (t_nada - t_h) - (t_nada - t_p)
        if acoplamiento:
            lineas.append(
                f"En el IRPF de {planif.ejercicio}, amortizar y aportar juntas ahorran {_eur(t_nada - t_ambas)}; por "
                f"separado, {_eur(t_nada - t_h)} y {_eur(t_nada - t_p)}. La diferencia ({_eur(acoplamiento)}) se debe a "
                "que la aportación baja la cuota y la deducción por vivienda no puede superarla."
            )
    fiscal_plan = planif._fiscal(a, planif._efecto(a))
    if fiscal_plan.tributacion != planif.fiscal_base.tributacion:
        lineas.append(
            f"Con el plan os conviene otra forma de declarar ({fiscal_plan.tributacion} en vez de "
            f"{planif.fiscal_base.tributacion}): cambia los umbrales y el valor de cada palanca."
        )
    if planif.colchon is not None and planif.utilizable < planif.total and planif.entrada.penalizacion_liquidez is None:
        lineas.append(
            f"El colchón de {_eur(planif.colchon)} limita lo que se puede usar a {_eur(planif.utilizable)} de "
            f"{_eur(planif.total)}."
        )
    if planif.margen_ayudas is not None:
        lineas.append(
            f"Para no perder ni rebajar una ayuda que hoy te corresponde, el plan no baja tu IRPF devengado en "
            f"{_eur(planif.margen_ayudas)} o más (art. 20.3 Ley 19/2021)."
        )
    return lineas


def _escenarios(planif: Planificador, rejilla: dict[str, list[Decimal]], plan: Asignacion) -> list[Escenario]:
    def escenario(nombre: str, a: Asignacion, nota: str = "") -> Escenario:
        r = planif.rapida(a)
        return Escenario(nombre=nombre, asignacion=a, total=r.total.quantize(CENT), factible=r.factible, nota=nota)

    out = [escenario("No hacer nada (todo líquido)", Asignacion())]
    tope = planif.maximo_hipoteca()
    if tope > 0:
        for modo, texto in (("plazo", "reduciendo plazo"), ("cuota", "reduciendo cuota")):
            if modo in planif.modalidades():
                out.append(
                    escenario(
                        f"Todo lo posible a la hipoteca, {texto}",
                        Asignacion(amortizar_hipoteca=tope, modalidad=modo),
                    )
                )
    m = planif.maximos_pension()
    if m[0] > 0 or m[1] > 0:
        p0 = min(m[0], planif.utilizable)
        p1 = min(m[1], planif.utilizable - p0)
        out.append(
            escenario(
                "Planes de pensiones al máximo que reduce la base, el resto líquido",
                Asignacion(plan_pensiones_contribuyente=p0, plan_pensiones_conyuge=p1),
            )
        )
    out.append(escenario("Plan óptimo", plan))
    return out


def _texto(plan: PlanHogar) -> str:
    v = plan.valoracion
    partes = [
        f"Dinero para repartir: {_eur(plan.importe_total)}. Horizonte: {plan.horizonte_anos} años (hasta el "
        f"{_fecha(plan.fecha_horizonte)}), frente a dejarlo líquido al {fmt_pct(plan.rentabilidad_alternativa_pct)} "
        "anual (tu supuesto)."
    ]
    usadas = [linea for linea in plan.asignacion if linea.id != "liquidez" and linea.importe > 0]
    if usadas:
        partes.append(
            "Reparto: " + "; ".join(f"{linea.nombre}: {_eur(linea.importe)}" for linea in plan.asignacion) + "."
        )
        partes.extend(f"- {linea.detalle}" for linea in usadas)
    else:
        partes.append("Con estos datos, lo mejor es no usar el dinero en ninguna palanca: mantenerlo líquido.")
    otra = "ahorro definitivo" if plan.vista == "diferimiento" else "diferimiento"
    otra_total = v.total_definitivo if plan.vista == "diferimiento" else v.total_diferimiento
    partes.append(
        f"Posición neta al final del horizonte: {_eur(v.total)} ({'contando el plan de pensiones como diferimiento' if plan.vista == 'diferimiento' else 'descontando el impuesto al rescatar el plan'}); "
        f"en la vista de {otra}, {_eur(otra_total)}. En euros de hoy: {_eur(v.valor_actual)}."
    )
    for e in plan.ayudas:
        if e.estado_antes != e.estado_despues or e.importe_antes != e.importe_despues:
            partes.append(f"Ayudas: {e.nombre}: {e.explicacion}")
    partes.append("No es una recomendación de inversión: son cuentas con tus supuestos.")
    return "\n".join(partes)


def planificar(
    entrada: EntradaPlan | Mapping[str, Any],
    *,
    facts: Mapping[str, Any] | None = None,
    modulos_activos: Collection[str] = (),
    hoy: date,
) -> PlanHogar:
    """The best allocation of the household's money (see the module docstring).

    ``facts`` are the case's facts as stored (name -> value); module facts are read only for the modules in
    ``modulos_activos`` (the user's consent). Raises :class:`DatosInvalidosError` (Spanish message) on bad data and
    ``EjercicioNoDisponibleError`` for a year without tax parameters.
    """
    if not isinstance(entrada, EntradaPlan):
        try:
            entrada = EntradaPlan.model_validate(dict(entrada))
        except ValidationError as exc:
            problemas = "; ".join(
                f"{'.'.join(str(x) for x in err['loc'])}: {str(err['msg']).removeprefix('Value error, ')}"
                for err in exc.errors()
            )
            raise DatosInvalidosError(f"Datos del plan no válidos: {problemas}.") from exc
    planif = Planificador(entrada, facts=dict(facts or {}), modulos_activos=modulos_activos, hoy=hoy)
    paso, rejilla = planif.candidatos()
    mejor, combinaciones, recalculadas, pulidas = planif.buscar(rejilla, paso)
    valoracion = planif.valorar(mejor)
    efecto = planif._efecto(mejor)

    # Each lever: inside the plan and alone.
    palancas: list[PalancaPlan] = []
    aisladas: dict[str, Asignacion] = {}
    suma_aislados = ZERO
    total_plan = planif.rapida(mejor).total
    for palanca in _usadas(mejor):
        sola, valor_solo = planif.aislada(palanca, rejilla)
        aisladas[palanca] = sola
        marginal = total_plan - planif.rapida(mejor.sin(palanca)).total
        suma_aislados += valor_solo
        palancas.append(
            PalancaPlan(
                id=palanca,
                nombre=NOMBRES_PALANCAS[palanca],
                importe=mejor.importe(palanca),
                valor_marginal=marginal.quantize(CENT),
                valor_aislado=valor_solo.quantize(CENT),
                importe_aislado=sola.importe(palanca),
                naturaleza=_naturaleza(palanca),
                explicacion=_detalle(planif, palanca, mejor, efecto),
                referencias=_referencias(planif, palanca, efecto),
            )
        )

    # Levers not used, and why.
    no_usadas = list(planif.no_usadas)
    m_p = planif.maximos_pension()
    for palanca in PALANCAS:
        if palanca in _usadas(mejor) or any(n.id == palanca for n in no_usadas):
            continue
        nombre = NOMBRES_PALANCAS[palanca]
        if planif._excluida(palanca):
            no_usadas.append(
                PalancaNoUsada(id=palanca, nombre=nombre, causa="restricciones", detalle=planif._motivo(palanca))
            )
            continue
        if palanca == "plan_pensiones_conyuge" and planif.conyuge is None:
            continue
        if palanca.startswith("plan_pensiones"):
            i = 0 if palanca.endswith("contribuyente") else 1
            if m_p[i] <= 0:
                causas = [n for n in planif.oraculo.no_disponibles if n.id == palanca]
                if causas:
                    causa = causas[0].causa
                    detalle = causas[0].detalle
                    propio = (
                        planif.entrada.aportacion_maxima_pensiones
                        if i == 0
                        else planif.entrada.aportacion_maxima_pensiones_conyuge
                    )
                    if causa == "restricciones" and planif.utilizable <= 0 and propio is None:
                        causa, detalle = "sin_margen", "No queda dinero que usar sin bajar del colchón de emergencia."
                    no_usadas.append(
                        PalancaNoUsada(
                            id=palanca,
                            nombre=nombre,
                            causa=causa
                            if causa in ("restricciones", "faltan_datos", "sin_margen", "fecha")
                            else "sin_margen",
                            detalle=detalle,
                        )
                    )
                    continue
        if palanca == "donativo" and planif.donativo_maximo <= 0:
            no_usadas.append(
                PalancaNoUsada(
                    id=palanca,
                    nombre=nombre,
                    causa="restricciones",
                    detalle="No has indicado un donativo ni un presupuesto: el plan no propone gastar dinero.",
                )
            )
            continue
        if palanca == "amortizar_hipoteca" and planif.hipoteca is None:
            continue
        sola, valor_solo = planif.aislada(palanca, rejilla) if palanca in rejilla else (Asignacion(), ZERO)
        no_usadas.append(
            PalancaNoUsada(
                id=palanca,
                nombre=nombre,
                causa="no_compensa",
                detalle=(
                    f"Con tus supuestos no mejora el plan: sola, su mejor opción vale {_eur(valor_solo.quantize(CENT))}"
                    f" (con {_eur(sola.importe(palanca))})."
                    if sola.importe(palanca) > 0
                    else "Con tus supuestos no mejora la posición: por sí sola no aporta nada positivo."
                ),
            )
        )

    # Allocation table.
    lineas: list[LineaAsignacion] = []
    for palanca in PALANCAS:
        importe = mejor.importe(palanca)
        if importe <= 0:
            continue
        efectivo = efecto.efectivo if palanca == "amortizar_hipoteca" and efecto is not None else importe
        lineas.append(
            LineaAsignacion(
                id=palanca,
                nombre=NOMBRES_PALANCAS[palanca],
                importe=importe,
                efectivo=efectivo,
                detalle=_detalle(planif, palanca, mejor, efecto),
            )
        )
    liquido = planif.total - valoracion.usado
    lineas.append(
        LineaAsignacion(
            id="liquidez",
            nombre=NOMBRES_PALANCAS["liquidez"],
            importe=liquido,
            efectivo=liquido,
            detalle=f"Queda líquido (con lo que ya tenías: {_eur(valoracion.liquido)}"
            + (f"; colchón objetivo {_eur(planif.colchon)})." if planif.colchon is not None else ")."),
        )
    )

    # Mortgage summary.
    resumen_h: ResumenHipoteca | None = None
    if planif.hipoteca is not None and not planif.hipoteca.pagada:
        h = planif.hipoteca
        actual = h.base.proxima(planif.fecha_ejecucion)
        ef = efecto if efecto is not None and efecto.importe > 0 else None
        nueva = ef.cuadro.proxima(planif.fecha_ejecucion) if ef is not None else None
        comision = ef.comision if ef is not None else h.comision(ONE)
        resumen_h = ResumenHipoteca(
            capital_pendiente=h.capital_pendiente,
            tipo_actual=h.base.cambio_vigente(planif.fecha_ejecucion).tipo,
            cuota_actual=actual.cuota if actual is not None else ZERO,
            fin_actual=h.base.fecha_fin,
            importe=mejor.amortizar_hipoteca,
            modalidad=mejor.modalidad,
            comision=ef.comision.importe if ef is not None else ZERO,
            comision_porcentaje=comision.porcentaje,
            comision_es_maximo=comision.es_maximo,
            cuota_nueva=nueva.cuota if nueva is not None else (ZERO if ef is not None else None),
            fin_nuevo=ef.cuadro.fecha_fin if ef is not None else None,
            intereses_ahorrados_horizonte=ef.intereses_ahorrados_horizonte if ef is not None else ZERO,
            intereses_ahorrados_total=ef.intereses_ahorrados_total if ef is not None else ZERO,
            fundamento_comision=list(comision.fundamento),
        )

    # Aids.
    ayudas: list[EfectoAyuda] = []
    if planif.ayudas is not None:
        fiscal_plan = planif._fiscal(mejor, efecto)
        desplazamiento = Desplazamiento(
            base_imponible=fiscal_plan.base_imponible - planif.fiscal_base.base_imponible,
            ingresos_computables=max(ZERO, planif.fiscal_base.cuota_liquida - fiscal_plan.cuota_liquida),
            patrimonio=-((efecto.efectivo if efecto is not None else ZERO) + mejor.donativo),
        )
        ayudas = planif.ayudas.efectos(desplazamiento, ejercicio=planif.ejercicio)
        if any(e.id in ("imv", "capi") for e in ayudas):
            planif.supuestos.append(
                "Para el IMV y el complemento de infancia se toma como «impuesto devengado» la cuota líquida del IRPF."
            )

    plan_fiscal = _plan_fiscal(planif, mejor)
    citas: list[str] = []
    for elegida in palancas:
        citas.extend(elegida.referencias)
    if plan_fiscal is not None:
        citas.extend(plan_fiscal.citas)
    if planif.hipoteca is not None:
        citas.extend(planif.hipoteca.normativa.url(b) for b in (efecto.comision.citas if efecto is not None else []))
    textos: list[TextoLegal] = [TEXTOS["art. 12 LIRPF"], TEXTOS["art. 17.2.a).3.ª LIRPF"]]
    if planif.ayudas is not None:
        textos += [
            TEXTOS[r]
            for r in (
                "art. 50.1 LIRPF",
                "art. 51 LIRPF",
                "art. 20.1.a) Ley 19/2021",
                "art. 20.2 Ley 19/2021",
                "art. 20.3 Ley 19/2021",
                "art. 20.4.b) Ley 19/2021",
                "art. 20.4.b).4.º Ley 19/2021",
                "art. 20.4.b), pasivos, Ley 19/2021",
            )
        ]
    if plan_fiscal is not None:
        textos += [t for t in plan_fiscal.textos_legales if t.referencia not in {x.referencia for x in textos}]
    avisos = list(dict.fromkeys(planif.avisos))

    no_modelado = [
        *planif.no_modelado,
        "La rentabilidad del plan de pensiones: se supone la misma que tu alternativa, así que la diferencia entre "
        "ambas es solo fiscal; las comisiones del plan no se han tenido en cuenta.",
        "El IRPF de los años siguientes se calcula con tus cifras y las reglas de "
        f"{planif.ejercicio}: no se conocen las futuras.",
        "Los gastos de amortizar distintos de la comisión (notaría, registro) y el interés del periodo roto; el "
        "seguro vinculado y su extorno.",
        "Que los rendimientos de tu alternativa tributen: indica una rentabilidad neta de impuestos.",
        "El excedente mensual se aplica todo junto en la fecha de ejecución (no se amortiza mes a mes).",
        "Luz, suscripciones y caducidades: estas palancas no cambian nada que esos módulos calculen (el bono social "
        "eléctrico depende de la renta y se analiza con las ayudas).",
        "Ayudas autonómicas y municipales (convocatorias): sus límites de renta no se han cruzado con el plan.",
    ]

    candidatos = {k: len(v) for k, v in rejilla.items()}
    plan = PlanHogar(
        ejercicio=planif.ejercicio,
        fecha=planif.fecha,
        fecha_ejecucion=planif.fecha_ejecucion,
        fecha_horizonte=planif.horizonte,
        horizonte_anos=entrada.horizonte_anos,
        rentabilidad_alternativa_pct=entrada.rentabilidad_alternativa_pct,
        vista=planif.vista,
        tipo_rescate_pct=entrada.tipo_rescate_pct,
        importe_total=planif.total,
        colchon_objetivo=planif.colchon,
        liquidez_antes=planif.liquidez_inicial,
        liquidez_despues=valoracion.liquido,
        asignacion=lineas,
        valoracion=valoracion,
        palancas=palancas,
        no_usadas=no_usadas,
        suma_aislados=suma_aislados.quantize(CENT),
        interaccion=(total_plan - suma_aislados).quantize(CENT),
        explicacion_interaccion=_explicar_interaccion(planif, mejor, palancas, aisladas),
        escenarios=_escenarios(planif, rejilla, mejor),
        hipoteca=resumen_h,
        ayudas=ayudas,
        plan_fiscal=plan_fiscal,
        restricciones=entrada.restricciones,
        supuestos=list(dict.fromkeys(planif.supuestos)),
        avisos=avisos,
        no_modelado=no_modelado,
        citas=list(dict.fromkeys(c for c in citas if c)),
        textos_legales=textos,
        busqueda=BusquedaPlan(
            metodo="exhaustiva",
            resolucion=paso,
            resolucion_hipoteca=planif.paso_hipoteca(paso) if planif.maximo_hipoteca() > 0 else None,
            combinaciones=combinaciones,
            recalculadas=recalculadas,
            afinado=pulidas,
            evaluaciones_irpf=planif.oraculo.evaluaciones,
            candidatos=candidatos,
            nota=(
                f"Se han recorrido todas las combinaciones de la rejilla: cada palanca en múltiplos de {_eur(paso)}, "
                "más cero, su máximo, los umbrales que exponen los módulos (tramos y límites fiscales del optimizador, "
                "tope de la deducción por vivienda, cancelar el préstamo) y, en cada combinación, la amortización que "
                "agota el resto del dinero (al euro). Esas amortizaciones fuera de la rejilla se ordenan interpolando "
                f"entre sus dos puntos vecinos y las {RECALCULADAS} mejores combinaciones se recalculan exactamente: "
                "el plan elegido y todas sus cifras son exactos. Al reducir plazo, el módulo de hipoteca redondea "
                "al alza el número de cuotas: cada importe en que desaparece una cuota es también un punto de la "
                "rejilla. Por último se afina alrededor del mejor (a una décima del paso). Entre dos puntos de la "
                "rejilla el resultado puede variar en menos de un paso de cada palanca."
            ),
        ),
        explicacion="",
    )
    return plan.model_copy(update={"explicacion": _texto(plan)})


__all__ = ["MAX_COMBINACIONES", "MAX_EVALUACIONES_IRPF", "Planificador", "planificar"]
