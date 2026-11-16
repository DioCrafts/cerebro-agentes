"""Whether an allocation changes a public aid: each aid's rule, what it measures, and a re-evaluation with the
aid module's own evaluators (``modulos/ayudas/prestaciones.py``) on the shifted magnitude.

Only interactions the rules support (quotes from the aid module's data and :mod:`.textos`):

* Bono social eléctrico (and the térmico, which follows it): the household's «base imponible general y del
  ahorro» (art. 4.1 Orden ETU/943/2017). A pension contribution reduces the base LIQUIDABLE (arts. 50.1 and 51
  LIRPF), and the housing and donation deductions come off the tax due, so the planner computes the change of the
  base imponible from the calculator's returns: it is zero for these levers, and the plan says so.
* Ingreso mínimo vital and complemento de ayuda para la infancia: income computed at its gross value (art.
  20.1.a Ley 19/2021) MINUS the income tax accrued and the social contributions (art. 20.3). Every euro of IRPF
  the plan saves raises that figure by one euro (taken as the change of the household's cuota líquida, the tax
  accrued; an assumption the plan states). Their asset test counts bank accounts and pension plans alike, and
  excludes the debts on the main home (art. 20.4.b): a repayment of the home loan and a donation lower the
  assets counted; a contribution to a pension plan does not change them.
* Prestación por nacimiento: «ingresos anuales percibidos, de cualquier naturaleza» (art. 358.2 LGSS): none of
  these levers changes the income received. Asignación por hijo con discapacidad: no income test.

Timing: the aids look at the income of the year before the application (art. 20.2 Ley 19/2021) or the last tax
period the tax agency knows (bono social), so a plan made in year N shows in applications from N+1.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any

from hacienda_gpt.modulos.ayudas.hogar import Hogar, entero, hogar_del_perfil
from hacienda_gpt.modulos.ayudas.parametros import Grupo, PrestacionesParams, ejercicio_para, load_prestaciones
from hacienda_gpt.modulos.ayudas.prestaciones import (
    ESTADOS_PROPONIBLES,
    ORDEN_ESTADOS,
    Evaluacion,
    evaluar_bono_social_electrico,
    evaluar_bono_social_termico,
    evaluar_capi,
    evaluar_imv,
    evaluar_todas,
)
from hacienda_gpt.planificador.modelos import EfectoAyuda
from hacienda_gpt.planificador.textos import cita

ZERO = Decimal(0)
CENT = Decimal("0.01")


def _cita_datos(grupo: Grupo, articulo: str) -> str:
    for ev in grupo.evidencia:
        if ev.articulo == articulo:
            return f"{ev.articulo} ({ev.boe_id}): «{ev.cita}»"
    raise KeyError(articulo)


@dataclass(frozen=True)
class Desplazamiento:
    """How an allocation moves what the aids measure."""

    base_imponible: Decimal = ZERO
    ingresos_computables: Decimal = ZERO  # IMV/CAPI income (art. 20.3: minus the tax accrued)
    patrimonio: Decimal = ZERO  # IMV/CAPI assets (art. 20.4)


@dataclass
class ContextoAyudas:
    params: PrestacionesParams
    hogar: Hogar
    grado_progenitor: int | None
    antes: dict[str, Evaluacion]

    @classmethod
    def desde_perfil(cls, perfil: Mapping[str, Any], hoy: date) -> ContextoAyudas:
        params = load_prestaciones(ejercicio_para(hoy))
        hogar = hogar_del_perfil(perfil, hoy)
        grado = entero(perfil.get("grado_discapacidad"))
        conyuge = entero(perfil.get("grado_discapacidad_conyuge"))
        if conyuge is not None and (grado is None or conyuge > grado):
            grado = conyuge
        antes = {e.id: e for e in evaluar_todas(params, hogar, grado)}
        return cls(params=params, hogar=hogar, grado_progenitor=grado, antes=antes)

    # ------------------------------------------------------------------ #

    def _hogar(self, d: Desplazamiento, *, ingresos: bool, base: bool) -> Hogar:
        h = self.hogar
        cambios: dict[str, Any] = {}
        if ingresos and h.ingresos is not None and d.ingresos_computables:
            cambios["ingresos"] = max(ZERO, h.ingresos + d.ingresos_computables)
        if ingresos and h.patrimonio is not None and d.patrimonio:
            cambios["patrimonio"] = max(ZERO, h.patrimonio + d.patrimonio)
        if base and h.base_imponible is not None and d.base_imponible:
            cambios["base_imponible"] = max(ZERO, h.base_imponible + d.base_imponible)
        return replace(h, **cambios) if cambios else h

    def despues(self, d: Desplazamiento) -> dict[str, Evaluacion]:
        """Every aid re-evaluated with the magnitudes each rule uses shifted (the others as before)."""
        p = self.params
        h_ingresos = self._hogar(d, ingresos=True, base=False)
        h_base = self._hogar(d, ingresos=False, base=True)
        electrico = evaluar_bono_social_electrico(p, h_base)
        nuevos: dict[str, Callable[[], Evaluacion]] = {
            "imv": lambda: evaluar_imv(p, h_ingresos),
            "capi": lambda: evaluar_capi(p, h_ingresos),
            "bono_social_electrico": lambda: electrico,
            "bono_social_termico": lambda: evaluar_bono_social_termico(p, h_base, electrico),
        }
        return {clave: (nuevos[clave]() if clave in nuevos else e) for clave, e in self.antes.items()}

    @staticmethod
    def _peor(antes: Evaluacion, despues: Evaluacion) -> bool:
        if antes.estado not in ESTADOS_PROPONIBLES:
            return False
        if ORDEN_ESTADOS[despues.estado] > ORDEN_ESTADOS[antes.estado]:
            return True
        for campo in ("importe_anual", "importe_mensual"):
            a, b = getattr(antes, campo), getattr(despues, campo)
            if a is not None and (b is None or b < a):
                return True
        return False

    def empeora(self, d: Desplazamiento) -> list[str]:
        """The aids the household gets today that the shift would lose or lower."""
        despues = self.despues(d)
        return [clave for clave, e in self.antes.items() if self._peor(e, despues[clave])]

    def margen_ingresos(self, maximo: Decimal) -> Decimal | None:
        """Smallest rise of the IMV/CAPI computable income (to the cent) that loses or lowers an aid, or None when
        no rise up to ``maximo`` does. The aid rules are monotone in income (a threshold or a lower amount)."""
        if maximo <= 0 or not self.empeora(Desplazamiento(ingresos_computables=maximo)):
            return None
        bajo, alto = ZERO, maximo.quantize(CENT)
        while alto - bajo > CENT:
            medio = ((bajo + alto) / 2).quantize(CENT)
            if self.empeora(Desplazamiento(ingresos_computables=medio)):
                alto = medio
            else:
                bajo = medio
        return alto

    # ------------------------------------------------------------------ #

    def efectos(self, d: Desplazamiento, *, ejercicio: int) -> list[EfectoAyuda]:
        p = self.params
        despues = self.despues(d)
        siguiente = ejercicio + 1
        cuando = (
            f"Las ayudas miran los ingresos del año anterior a la solicitud ({cita('art. 20.2 Ley 19/2021')}): "
            f"lo que hagas en {ejercicio} se notaría en las solicitudes desde {siguiente}."
        )
        reglas: dict[str, tuple[str, str]] = {
            "imv": (
                "ingresos del año anterior por su valor íntegro, menos el IRPF devengado y las cotizaciones; y "
                "patrimonio sin la vivienda habitual ni sus deudas",
                f"{cita('art. 20.1.a) Ley 19/2021')} {cita('art. 20.3 Ley 19/2021')} "
                f"{cita('art. 20.4.b) Ley 19/2021')} {cita('art. 20.4.b), pasivos, Ley 19/2021')}",
            ),
            "capi": (
                "ingresos computables del art. 20 Ley 19/2021 (íntegros menos el IRPF devengado y las cotizaciones) "
                "y patrimonio neto",
                f"{_cita_datos(p.capi, 'art. 11.6 Ley 19/2021')} {cita('art. 20.3 Ley 19/2021')}",
            ),
            "bono_social_electrico": (
                "base imponible general y del ahorro del IRPF",
                f"{_cita_datos(p.bono_social_electrico, 'art. 4.1 Orden ETU/943/2017')} {cita('art. 50.1 LIRPF')}",
            ),
            "bono_social_termico": (
                "tener el bono social eléctrico a 31 de diciembre del año anterior",
                _cita_datos(p.bono_social_termico, "art. 8 RDL 15/2018"),
            ),
            "nacimiento": (
                "ingresos anuales percibidos, de cualquier naturaleza",
                _cita_datos(p.nacimiento, "art. 358.2 LGSS"),
            ),
            "hijo_discapacidad": (
                "edad y grado de discapacidad de cada hijo (sin límite de ingresos)",
                _cita_datos(p.hijo_discapacidad, "art. 351.a) LGSS"),
            ),
        }
        out: list[EfectoAyuda] = []
        for clave, antes in self.antes.items():
            if clave not in reglas:
                continue
            magnitud, texto = reglas[clave]
            nuevo = despues[clave]
            cambio: Decimal | None
            if clave in ("imv", "capi"):
                cambio = d.ingresos_computables
                partes = []
                if d.ingresos_computables:
                    partes.append(
                        f"El plan baja el IRPF devengado en {_eur(d.ingresos_computables)}; como esta ayuda resta ese "
                        f"impuesto de los ingresos, los ingresos computables suben lo mismo."
                    )
                else:
                    partes.append("El plan no cambia el IRPF devengado: los ingresos computables no cambian.")
                if d.patrimonio:
                    partes.append(
                        f"El patrimonio computable baja {_eur(-d.patrimonio)} (lo que sale de tus cuentas para "
                        "amortizar la hipoteca de la vivienda habitual, cuya deuda no cuenta, o para donar); lo que "
                        "pasa a un plan de pensiones sigue contando."
                    )
                if self.hogar.patrimonio is None and d.patrimonio:
                    partes.append("No consta tu patrimonio («ayudas_patrimonio_neto_hogar»): no se ha recalculado.")
                partes.append(cuando)
            elif clave == "bono_social_electrico":
                cambio = d.base_imponible
                partes = [
                    (
                        (
                            "Ninguna de estas palancas cambia la base imponible: la aportación a planes de pensiones "
                            "reduce la base liquidable, y las deducciones por vivienda y donativos se restan de la cuota. "
                            "El límite de renta del bono social no se mueve."
                        )
                        if not d.base_imponible
                        else f"El plan cambia la base imponible en {_eur(d.base_imponible)}."
                    )
                ]
                partes.append("Se mira el último período impositivo del que tenga constancia la Agencia Tributaria.")
            elif clave == "bono_social_termico":
                cambio = None
                partes = ["Sigue al bono social eléctrico."]
            else:
                cambio = ZERO
                partes = ["Ninguna de estas palancas cambia lo que mide esta ayuda."]
            if nuevo.estado != antes.estado or nuevo.importe_anual != antes.importe_anual:
                partes.append(f"Con el plan: {nuevo.motivo}")
            out.append(
                EfectoAyuda(
                    id=clave,
                    nombre=antes.nombre,
                    magnitud=magnitud,
                    cita=texto,
                    cambio=cambio,
                    estado_antes=antes.estado,
                    estado_despues=nuevo.estado,
                    importe_antes=antes.importe_anual,
                    importe_despues=nuevo.importe_anual,
                    explicacion=" ".join(partes),
                )
            )
        return out


def _eur(importe: Decimal) -> str:
    texto = f"{abs(importe):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if importe < 0 else ''}{texto} €"


__all__ = ["ContextoAyudas", "Desplazamiento"]
