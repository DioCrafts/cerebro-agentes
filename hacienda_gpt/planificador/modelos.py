"""Input and output of the household planner (see :mod:`hacienda_gpt.planificador.motor`).

Money is ``Decimal`` (euros); every model forbids unknown fields. The planner's own words for the user
are Spanish; field names follow the rest of the project (Spanish, snake case).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hacienda_gpt.tax.optimizador import PlanOptimo, TextoLegal

ZERO = Decimal(0)

# The levers the planner allocates money to. "liquidez" (keeping the money) is the remainder, not a lever.
PalancaId = Literal["amortizar_hipoteca", "plan_pensiones_contribuyente", "plan_pensiones_conyuge", "donativo"]
PALANCAS: tuple[PalancaId, ...] = (
    "amortizar_hipoteca",
    "plan_pensiones_contribuyente",
    "plan_pensiones_conyuge",
    "donativo",
)
NOMBRES_PALANCAS: dict[str, str] = {
    "amortizar_hipoteca": "Amortizar hipoteca",
    "plan_pensiones_contribuyente": "Tu plan de pensiones",
    "plan_pensiones_conyuge": "Plan de pensiones de tu cónyuge",
    "donativo": "Donativo",
    "liquidez": "Mantener líquido",
}
Modalidad = Literal["plazo", "cuota"]
# How the household files its IRPF (``Restricciones.modalidades_excluidas`` of the tax optimiser).
Tributacion = Literal["conjunta", "individual"]
VistaPensiones = Literal["diferimiento", "definitivo"]
# What a component of the net position is: a definitive saving, a tax only deferred (pension contributions are
# taxed when redeemed), a cost, a return foregone or earned, or the user's own liquidity penalty.
NaturalezaComponente = Literal["definitivo", "diferimiento", "coste", "rendimiento", "penalizacion"]


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RestriccionesPlan(_Modelo):
    """Decisions the user has taken (or the memory of past decisions brings in): levers excluded, modalities
    excluded and caps. The planner never proposes an excluded lever and lists it with its reason.

    This is the hook for ``hacienda_gpt.memoria`` (rejected levers): its adapter fills this object; the planner
    does not import that package.
    """

    excluidas: list[PalancaId] = Field(default_factory=list, max_length=len(PALANCAS))
    # Ways of repaying the mortgage the user rules out (reduce the term / the instalment).
    modalidades_excluidas: list[Modalidad] = Field(default_factory=list, max_length=2)
    # Filing modes the user rules out: the tax optimiser never uses them for the plan's IRPF.
    tributacion_excluida: list[Tributacion] = Field(default_factory=list, max_length=2)
    # Most each lever may take, in euros (on top of the budget, which always applies).
    maximos: dict[PalancaId, Decimal] = Field(default_factory=dict)
    # Why each lever is excluded or capped, in the user's words (shown as is).
    motivos: dict[PalancaId, str] = Field(default_factory=dict)
    # Who set them ("usuario", "memoria"…), shown with the plan.
    origen: str | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def _coherentes(self) -> RestriccionesPlan:
        for clave, valor in self.maximos.items():
            if valor < 0:
                raise ValueError(f"el máximo de «{clave}» no puede ser negativo")
        for clave, texto in self.motivos.items():
            if len(texto) > 300:
                raise ValueError(f"el motivo de «{clave}» es demasiado largo (máximo 300 caracteres)")
        return self


class EntradaPlan(_Modelo):
    """What the household can allocate and the frame of the comparison. Absent = not stated (the plan says
    what it assumed)."""

    ejercicio: int | None = None
    # The day the plan is made for (default: today in Spain) and the day the money is used (default: that day;
    # with a monthly surplus, 15 December, when it has been saved).
    fecha: date | None = None
    fecha_ejecucion: date | None = None
    # Money available now, and the monthly surplus saved until December (added to it).
    importe_disponible: Decimal = Field(default=ZERO, ge=0, le=Decimal("100000000"))
    excedente_mensual: Decimal = Field(default=ZERO, ge=0, le=Decimal("10000000"))
    horizonte_anos: int = Field(default=5, ge=1, le=30)
    # Net annual return of the money NOT used (the user's own figure; arithmetic only, no advice).
    rentabilidad_alternativa_pct: Decimal = Field(default=ZERO, ge=Decimal(-20), le=Decimal(30))
    # Emergency fund: an explicit target, or ``meses_colchon`` months of ``gastos_mensuales``.
    colchon_objetivo: Decimal | None = Field(default=None, ge=0, le=Decimal("100000000"))
    gastos_mensuales: Decimal | None = Field(default=None, ge=0, le=Decimal("10000000"))
    meses_colchon: int = Field(default=6, ge=0, le=36)
    # Liquid savings the household already holds apart from ``importe_disponible``.
    liquidez_actual: Decimal = Field(default=ZERO, ge=0, le=Decimal("100000000"))
    # Euros subtracted per euro the cushion falls short. None: the cushion is a hard limit.
    penalizacion_liquidez: Decimal | None = Field(default=None, ge=0, le=Decimal(10))
    # Marginal IRPF rate expected when the pension plan is redeemed (percent); used by the «definitive» view.
    tipo_rescate_pct: Decimal | None = Field(default=None, ge=0, le=Decimal(60))
    # Which view of the pension contribution the search maximises. None: «definitivo» when the redemption rate
    # is given, else «diferimiento» (both are always shown).
    valorar_pensiones: VistaPensiones | None = None
    # Cash each spouse can put into a pension plan at most (the budget applies anyway).
    aportacion_maxima_pensiones: Decimal | None = Field(default=None, ge=0)
    aportacion_maxima_pensiones_conyuge: Decimal | None = Field(default=None, ge=0)
    acepta_iliquidez: bool | None = None
    # Donation the household wants to make out of this money, and the most it would give.
    donativo_previsto: Decimal = Field(default=ZERO, ge=0, le=Decimal("100000000"))
    presupuesto_donativos: Decimal | None = Field(default=None, ge=0, le=Decimal("100000000"))
    donativo_recurrente_importe_anterior: Decimal | None = Field(default=None, gt=0)
    # Share of the mortgage payments that the spouse pays (for the pre-2013 housing deduction in separate returns).
    parte_hipoteca_conyuge: Decimal = Field(default=ZERO, ge=0, le=1)
    # The tax figures (calculator fields) on top of the case, and the spouse's figures.
    datos: dict[str, Any] = Field(default_factory=dict)
    conyuge: dict[str, Any] | None = None
    sin_conyuge: bool = False
    # The loan, with the shape of the ``hipoteca_prestamo`` fact; replaces the one in the profile.
    hipoteca: dict[str, Any] | None = None
    restricciones: RestriccionesPlan = Field(default_factory=RestriccionesPlan)
    # Do not propose a plan that loses (or lowers) a public aid the household gets today.
    proteger_ayudas: bool = True
    # Step of the uniform part of the grid, in euros (None: the finest that keeps the search within budget).
    resolucion: Decimal | None = Field(default=None, ge=Decimal(1), le=Decimal("100000"))

    @model_validator(mode="after")
    def _coherente(self) -> EntradaPlan:
        if self.presupuesto_donativos is not None and self.presupuesto_donativos < self.donativo_previsto:
            raise ValueError("el presupuesto de donativos no puede ser menor que el donativo previsto")
        if self.fecha is not None and self.fecha_ejecucion is not None and self.fecha_ejecucion < self.fecha:
            raise ValueError("la fecha de ejecución no puede ser anterior a la fecha del plan")
        return self


class Asignacion(_Modelo):
    """One allocation of the money: what goes to each lever (the rest stays liquid)."""

    amortizar_hipoteca: Decimal = ZERO
    modalidad: Modalidad | None = None
    plan_pensiones_contribuyente: Decimal = ZERO
    plan_pensiones_conyuge: Decimal = ZERO
    donativo: Decimal = ZERO

    def importe(self, palanca: str) -> Decimal:
        valor: Decimal = getattr(self, palanca)
        return valor

    def sin(self, palanca: str) -> Asignacion:
        cambios: dict[str, Any] = {palanca: ZERO}
        if palanca == "amortizar_hipoteca":
            cambios["modalidad"] = None
        return self.model_copy(update=cambios)


class Componente(_Modelo):
    """One line of the net position at the horizon (euros of that date), against keeping everything liquid."""

    concepto: str
    importe: Decimal
    naturaleza: NaturalezaComponente
    palanca: str | None = None
    # Module that computed it ("fiscal", "hipoteca", "planificador") and its legal reference, when there is one.
    modulo: str
    referencia: str | None = None


class Valoracion(_Modelo):
    asignacion: Asignacion
    # Cash each lever uses (the repayment includes its fee), what stays liquid and the cushion shortfall.
    usado: Decimal
    liquido: Decimal
    falta_colchon: Decimal
    componentes: list[Componente]
    # The objective (the view chosen) and both views.
    total: Decimal
    total_diferimiento: Decimal
    total_definitivo: Decimal
    # ``total`` in euros of the plan's date (discounted at the alternative return).
    valor_actual: Decimal
    # IRPF of the year (sum of the household's returns; positive: to pay) and how they file.
    impuesto_ejercicio: Decimal
    tributacion: str
    cuota_liquida: Decimal
    base_imponible: Decimal


class LineaAsignacion(_Modelo):
    id: str
    nombre: str
    importe: Decimal
    # Cash it takes out of the available money (a repayment adds its fee).
    efectivo: Decimal
    detalle: str


class PalancaPlan(_Modelo):
    id: str
    nombre: str
    importe: Decimal
    # What the plan loses without it (its money back to liquidity), and its best value used alone.
    valor_marginal: Decimal
    valor_aislado: Decimal
    importe_aislado: Decimal
    naturaleza: str
    explicacion: str
    referencias: list[str]


class PalancaNoUsada(_Modelo):
    id: str
    nombre: str
    causa: Literal["restricciones", "faltan_datos", "sin_margen", "fecha", "no_compensa", "modulo_inactivo"]
    detalle: str


class EfectoAyuda(_Modelo):
    """Whether the plan changes a public aid, by what the aid's rule measures (computed with the aid module)."""

    id: str
    nombre: str
    # What the rule measures, with its literal quote.
    magnitud: str
    cita: str
    cambio: Decimal | None
    estado_antes: str
    estado_despues: str
    importe_antes: Decimal | None = None
    importe_despues: Decimal | None = None
    explicacion: str


class ResumenHipoteca(_Modelo):
    capital_pendiente: Decimal
    tipo_actual: Decimal
    cuota_actual: Decimal
    fin_actual: date
    importe: Decimal
    modalidad: Modalidad | None
    comision: Decimal
    comision_porcentaje: Decimal
    comision_es_maximo: bool
    cuota_nueva: Decimal | None
    fin_nuevo: date | None
    intereses_ahorrados_horizonte: Decimal
    intereses_ahorrados_total: Decimal
    fundamento_comision: list[str]


class Escenario(_Modelo):
    nombre: str
    asignacion: Asignacion
    total: Decimal
    factible: bool
    nota: str = ""


class BusquedaPlan(_Modelo):
    metodo: Literal["exhaustiva"]
    resolucion: Decimal
    # The repayment grid's own step (coarser: each point is a whole schedule); None without a mortgage lever.
    resolucion_hipoteca: Decimal | None = None
    combinaciones: int
    # Allocations of the first pass valued again exactly, and allocations tried by the final local polish.
    recalculadas: int = 0
    afinado: int = 0
    evaluaciones_irpf: int
    candidatos: dict[str, int]
    nota: str


class PlanHogar(_Modelo):
    ejercicio: int
    fecha: date
    fecha_ejecucion: date
    fecha_horizonte: date
    horizonte_anos: int
    rentabilidad_alternativa_pct: Decimal
    vista: VistaPensiones
    tipo_rescate_pct: Decimal | None
    importe_total: Decimal
    colchon_objetivo: Decimal | None
    liquidez_antes: Decimal
    liquidez_despues: Decimal
    asignacion: list[LineaAsignacion]
    valoracion: Valoracion
    palancas: list[PalancaPlan]
    no_usadas: list[PalancaNoUsada]
    suma_aislados: Decimal
    interaccion: Decimal
    explicacion_interaccion: list[str]
    escenarios: list[Escenario]
    hipoteca: ResumenHipoteca | None
    ayudas: list[EfectoAyuda]
    plan_fiscal: PlanOptimo | None
    restricciones: RestriccionesPlan
    supuestos: list[str]
    avisos: list[str]
    no_modelado: list[str]
    citas: list[str]
    textos_legales: list[TextoLegal]
    busqueda: BusquedaPlan
    explicacion: str
