"""The tax module's tools: what runs when the model calls ``calcular_irpf``, ``optimizar_impuestos``…

Moved from the advisor's toolbox (``agent/tools.py``) with their behaviour unchanged. Each tool is a
:class:`~asistente.nucleo.modulos.contrato.HerramientaModulo` of :data:`HERRAMIENTAS`, and runs in one of two ways:

* as a DIRECT tool of the central agent (the module sets ``herramientas_directas``): the core passes the run's
  evidence ledger (``ContextoModulo.evidencia``) and the stored case (``ContextoModulo.caso``). Each result is
  registered as a citable fragment ``[F#]`` (authority ``CALCULADORA``) and goes to the model as it is, exactly
  as when these were the advisor's own tools;
* through the plain contract (an evaluation, the API, another caller with only a profile): a transient case is
  built from the profile and a throw-away ledger is used, whose ``[F#]`` ids are removed from the text.

Exceptions are not caught in the direct path: the core reports them to the model as before.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from asistente.ambitos.fiscal.calculo.actividad import ActividadInput, calcular_rendimiento_actividad
from asistente.ambitos.fiscal.calculo.autonomos_params import ejercicios_autonomos_disponibles
from asistente.ambitos.fiscal.calculo.beneficios import evaluar_beneficios
from asistente.ambitos.fiscal.calculo.calendario import aviso_festivos, proximos_vencimientos
from asistente.ambitos.fiscal.calculo.conjunta import comparar_tributacion
from asistente.ambitos.fiscal.calculo.irpf import IRPFInput
from asistente.ambitos.fiscal.calculo.iva import Modelo303Input, calcular_modelo_303
from asistente.ambitos.fiscal.calculo.obligacion import obligado_a_declarar
from asistente.ambitos.fiscal.calculo.optimizador import detalle_a_texto, resumen_conyuge
from asistente.ambitos.fiscal.calculo.pagos_fraccionados import Modelo130Input, calcular_modelo_130
from asistente.ambitos.fiscal.calculo.params import EjercicioNoDisponibleError, ejercicios_disponibles, load_params
from asistente.ambitos.fiscal.calculo.patrimonio import PatrimonioInput, calcular_patrimonio
from asistente.ambitos.fiscal.calculo.patrimonio_params import ejercicios_patrimonio_disponibles
from asistente.ambitos.fiscal.calculo.simulador import simular
from asistente.ambitos.fiscal.calculo.sociedades import (
    ComparacionInput,
    PagoFraccionadoSociedadesInput,
    SociedadesInput,
    calcular_pago_fraccionado_sociedades,
    calcular_sociedades,
    comparar_autonomo_vs_sociedad,
)
from asistente.ambitos.fiscal.calculo.sociedades_params import ejercicios_sociedades_disponibles
from asistente.ambitos.fiscal.calculo.sucesiones import BeneficiarioISD, calcular_isd
from asistente.ambitos.fiscal.calculo.sucesiones_params import ejercicios_isd_disponibles
from asistente.ambitos.fiscal.especificaciones import (
    _RESTRICCIONES,
    ACTIVITY_TOOL,
    AUTONOMO_SOCIEDAD_TOOL,
    BENEFITS_TOOL,
    CALCULATE_TOOL,
    CALENDAR_TOOL,
    ESPECIFICACIONES,
    ISD_TOOL,
    JOINT_TOOL,
    MODELO_130_TOOL,
    MODELO_303_TOOL,
    OBLIGACION_TOOL,
    OPTIMIZE_TOOL,
    PATRIMONIO_TOOL,
    SOCIEDADES_PAGO_TOOL,
    SOCIEDADES_TOOL,
)
from asistente.ambitos.fiscal.presentacion import (
    OBLIGADO,
    money,
    render_obligacion,
    render_plan,
    render_result,
)
from asistente.ambitos.fiscal.profile import (
    DATOS_CONYUGE,
    DETALLE_FACT_TYPES,
    RECUENTOS_DEL_DETALLE,
    DatosInvalidosError,
    build_input_con_actividad,
    normalizar_datos_conyuge,
)
from asistente.nucleo.agent.evidence import EvidenceLedger
from asistente.nucleo.decision.fact_extractor import EXPECTED_FACT_TYPE
from asistente.nucleo.decision.schemas import CaseState, Fact, parse_fiscal_year
from asistente.nucleo.modulos.contrato import ContextoModulo, HerramientaModulo, ResultadoHerramienta
from asistente.nucleo.proactive.jobs import hoy_en_espana

logger = logging.getLogger(__name__)

_FRAGMENT_ID = re.compile(r"\[F\d+\]\s?")


def _error(message: str) -> ResultadoHerramienta:
    return ResultadoHerramienta(contenido=message, resumen=message[:120], es_error=True)


def _cifras_del_conyuge(guardado: Any, pedido: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
    """The other spouse's figures for the joint comparison: the ones the case keeps (``datos_conyuge``) under
    the ones the agent passes, field by field (a null removes one). Returns them (None when there are none)
    and, when stored figures were used, the sentence that says which."""
    if guardado is None:
        return (None if pedido is None else {k: v for k, v in pedido.items() if v is not None}), []
    try:
        base = normalizar_datos_conyuge(guardado)
    except DatosInvalidosError as exc:
        raise DatosInvalidosError(f"{exc} (son las guardadas en el expediente)") from exc
    cifras = dict(base)
    for campo, valor in (pedido or {}).items():
        if valor is None:
            cifras.pop(campo, None)
        else:
            cifras[campo] = valor
    usados = [campo for campo in base if pedido is None or campo not in pedido]
    if not usados:
        return cifras, []
    return cifras, [f"Cónyuge B: cifras guardadas en el expediente — {resumen_conyuge(base, usados)}."]


class EjecucionFiscal:
    """One tool call's state: the evidence ledger it registers its fragments in and the case it reads."""

    def __init__(self, *, ledger: EvidenceLedger, case: CaseState | None) -> None:
        self.ledger = ledger
        self.case = case

    def manejadores(self) -> dict[str, Callable[[dict[str, Any]], ResultadoHerramienta]]:
        """Tool name -> handler (one per spec of :data:`ESPECIFICACIONES`)."""
        return {
            CALCULATE_TOOL.name: self._calculate,
            JOINT_TOOL.name: self._joint,
            ACTIVITY_TOOL.name: lambda a: self._autonomo(
                a, ActividadInput, calcular_rendimiento_actividad, "Rendimiento de la actividad"
            ),
            MODELO_130_TOOL.name: lambda a: self._autonomo(a, Modelo130Input, calcular_modelo_130, "Modelo 130"),
            MODELO_303_TOOL.name: lambda a: self._autonomo(a, Modelo303Input, calcular_modelo_303, "Modelo 303"),
            SOCIEDADES_TOOL.name: lambda a: self._calculadora(
                a,
                SociedadesInput,
                calcular_sociedades,
                "Impuesto sobre Sociedades",
                ejercicios_sociedades_disponibles(),
                "sociedades",
            ),
            SOCIEDADES_PAGO_TOOL.name: lambda a: self._calculadora(
                a,
                PagoFraccionadoSociedadesInput,
                calcular_pago_fraccionado_sociedades,
                "Pago fraccionado de Sociedades",
                ejercicios_sociedades_disponibles(),
                "sociedades",
            ),
            AUTONOMO_SOCIEDAD_TOOL.name: lambda a: self._calculadora(
                a,
                ComparacionInput,
                comparar_autonomo_vs_sociedad,
                "Autónomo frente a sociedad",
                [2025, 2026],
                "sociedades",
            ),
            PATRIMONIO_TOOL.name: lambda a: self._calculadora(
                a,
                PatrimonioInput,
                calcular_patrimonio,
                "Impuesto sobre el Patrimonio",
                ejercicios_patrimonio_disponibles(),
                "patrimonio",
            ),
            ISD_TOOL.name: lambda a: self._calculadora(
                a,
                BeneficiarioISD,
                calcular_isd,
                "Impuesto sobre Sucesiones y Donaciones",
                ejercicios_isd_disponibles(),
                "sucesiones",
            ),
            BENEFITS_TOOL.name: self._benefits,
            OPTIMIZE_TOOL.name: self._optimize,
            CALENDAR_TOOL.name: self._calendar,
            OBLIGACION_TOOL.name: self._obligacion,
        }

    # ------------------------------------------------------------------ #
    # Tax computation
    # ------------------------------------------------------------------ #

    def _scenario_base(self, arguments: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any]]:
        """Resolve (fiscal year, case facts, explicit data) for a computation."""
        datos = arguments.get("datos") or {}
        if not isinstance(datos, dict):
            raise DatosInvalidosError("'datos' debe ser un objeto {campo: valor}.")
        facts = {fact.name: fact.value for fact in self.case.facts} if self.case is not None else {}
        raw_year = arguments.get("ejercicio")
        if isinstance(raw_year, int) and not isinstance(raw_year, bool):
            year = raw_year
        else:
            case_year = parse_fiscal_year(self.case.tax_period) if self.case is not None else None
            year = case_year if case_year is not None else max(ejercicios_disponibles())
        return year, facts, datos

    def _calculate(self, arguments: dict[str, Any]) -> ResultadoHerramienta:
        try:
            year, facts, datos = self._scenario_base(arguments)
            simulacion = simular(
                ejercicio=year, facts=facts, datos=datos, escenarios=arguments.get("escenarios"), con_beneficios=False
            )
        except (DatosInvalidosError, EjercicioNoDisponibleError) as exc:
            return _error(str(exc))

        blocks: list[str] = []
        fragment_ids: list[str] = []
        base_name = simulacion.escenarios[0].nombre
        for escenario in simulacion.escenarios:
            nombre, result = escenario.nombre, escenario.resultado
            # Each scenario is registered as evidence: the answer cites it like
            # any other source and the verifier checks figures against it.
            fragment = self.ledger.register(
                text=render_result(nombre, result),
                metadata={
                    "title": f"Cálculo IRPF {result.ejercicio} — {nombre}",
                    "source": f"calculadora://irpf/{result.ejercicio}/{len(self.ledger) + 1}",
                    "source_authority": "CALCULADORA",
                    "section": "Simulación determinista",
                },
            )
            fragment_ids.append(fragment.fragment_id)
            block = f"[{fragment.fragment_id}] " + render_result(nombre, result)
            if nombre != base_name:
                diff = escenario.ahorro_frente_a_base
                verb = "ahorra" if diff > 0 else "cuesta"
                block += (
                    f"\nFrente a «{base_name}»: {verb} {money(abs(diff))}."
                    if diff
                    else f"\nFrente a «{base_name}»: sin diferencia."
                )
            blocks.append(block)
        if simulacion.datos_no_aportados:
            blocks.append(
                "DATOS NO APORTADOS (el cálculo los ha tomado como cero o sin informar): "
                + ", ".join(simulacion.datos_no_aportados)
                + ". Pídeselos al usuario si pueden cambiar el resultado, y dile que el cálculo es provisional sin ellos."
            )
        return ResultadoHerramienta(
            contenido="\n\n".join(blocks),
            resumen=f"{len(simulacion.escenarios)} cálculo(s): {', '.join(fragment_ids)}",
        )

    def _obligacion(self, arguments: dict[str, Any]) -> ResultadoHerramienta:
        try:
            year, facts, datos = self._scenario_base(arguments)
            inp, _actividad = build_input_con_actividad(ejercicio=year, facts=facts, datos=datos)
            resultado = obligado_a_declarar(inp, load_params(year))
        except (DatosInvalidosError, EjercicioNoDisponibleError) as exc:
            return _error(str(exc))
        text = render_obligacion(resultado)
        if facts.get("es_autonomo") is True and inp.alta_autonomo_en_el_ejercicio is None:
            text += (
                "\nEl expediente dice que trabaja por cuenta propia: si estuvo de alta como autónomo en algún momento "
                "del año, está obligado a declarar (confírmalo y registra alta_autonomo_en_el_ejercicio)."
            )
        fragment = self.ledger.register(
            text=text,
            metadata={
                "title": f"Obligación de declarar el IRPF {year}",
                "source": f"calculadora://obligacion/{year}/{len(self.ledger) + 1}",
                "source_authority": "CALCULADORA",
                "section": "Obligación de declarar (art. 96 LIRPF)",
            },
        )
        return ResultadoHerramienta(
            contenido=f"[{fragment.fragment_id}] {text}",
            resumen=f"obligado a declarar: {OBLIGADO[resultado.obligado]} ({fragment.fragment_id})",
        )

    def _autonomo(self, arguments: dict[str, Any], modelo: Any, calcular: Any, titulo: str) -> ResultadoHerramienta:
        return self._calculadora(arguments, modelo, calcular, titulo, ejercicios_autonomos_disponibles(), "autonomos")

    def _calculadora(
        self, arguments: dict[str, Any], modelo: Any, calcular: Any, titulo: str, ejercicios: list[int], ambito: str
    ) -> ResultadoHerramienta:
        """Shared handler of the deterministic calculators: validate, compute, register as evidence."""
        datos = dict(arguments)
        if not isinstance(datos.get("ejercicio"), int) or isinstance(datos.get("ejercicio"), bool):
            case_year = parse_fiscal_year(self.case.tax_period) if self.case is not None else None
            datos["ejercicio"] = case_year if case_year in ejercicios else max(ejercicios)
        try:
            result = calcular(modelo(**datos))
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
            return _error(f"Datos no válidos: {problems}")
        except ValueError as exc:  # unavailable year or community, inconsistent figures
            return _error(str(exc))
        lines = [f"{titulo} — ejercicio {datos['ejercicio']}"]
        for linea in getattr(result, "lineas", None) or []:
            ref = f" ({linea.referencia})" if linea.referencia else ""
            lines.append(f"  {linea.concepto}: {money(linea.importe)}{ref}")
        resumen = {
            k: v
            for k, v in result.model_dump(mode="json").items()
            if k not in ("lineas", "avisos", "parametros_no_verificados", "fuente", "no_modelado")
            and v is not None
            and not isinstance(v, dict | list)
        }
        lines.append("Resultado: " + "; ".join(f"{k} = {v}" for k, v in resumen.items()))
        if getattr(result, "avisos", None):
            lines.append("Avisos (lo que este cálculo no cubre o da por supuesto):")
            lines.extend(f"  * {aviso}" for aviso in result.avisos)
        if getattr(result, "no_modelado", None):
            lines.append("No modelado: " + "; ".join(str(x) for x in result.no_modelado))
        if getattr(result, "fuente", None):
            lines.append(f"Parámetros: {result.fuente}.")
        fragment = self.ledger.register(
            text="\n".join(lines),
            metadata={
                "title": f"{titulo} {datos['ejercicio']}",
                "source": f"calculadora://{ambito}/{datos['ejercicio']}/{len(self.ledger) + 1}",
                "source_authority": "CALCULADORA",
                "section": "Cálculo determinista",
            },
        )
        return ResultadoHerramienta(
            contenido=f"[{fragment.fragment_id}] " + "\n".join(lines), resumen=f"{titulo}: {fragment.fragment_id}"
        )

    def _joint(self, arguments: dict[str, Any]) -> ResultadoHerramienta:
        pedido = arguments.get("conyuge_b")
        if pedido is not None and not isinstance(pedido, dict):
            return _error("'conyuge_b' debe ser un objeto {campo: valor} con las cifras del otro cónyuge.")
        guardado_usado: list[str] = []
        try:
            year, facts, datos = self._scenario_base(
                {"ejercicio": arguments.get("ejercicio"), "datos": arguments.get("conyuge_a")}
            )
            otro, guardado_usado = _cifras_del_conyuge(facts.get(DATOS_CONYUGE), pedido)
            if otro is None:
                return _error(
                    "Falta 'conyuge_b' (objeto con los datos del otro cónyuge; si no tiene ingresos, su salario a 0): "
                    "el expediente no guarda sus cifras."
                )
            a, actividad_a = build_input_con_actividad(ejercicio=year, facts=facts, datos=datos)
            b, actividad_b = build_input_con_actividad(
                ejercicio=year, datos={"comunidad_autonoma": a.comunidad_autonoma, **otro}
            )
            try:
                comparacion = comparar_tributacion(a, b)
            except ValidationError as exc:
                # The joint return adds both spouses' figures: the sum can contradict the detail of
                # the children (a per-child expense given again by the other spouse) or a breakdown.
                problemas = "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())
                raise DatosInvalidosError(
                    f"No se puede formar la declaración conjunta sumando los datos de los dos cónyuges: {problemas}. "
                    "Un gasto repartido por hijo en `descendientes` debe darse por hijo en los dos cónyuges (los mismos hijos) o solo en uno."
                ) from exc
        except (DatosInvalidosError, EjercicioNoDisponibleError) as exc:
            return _error(str(exc))
        # Where each spouse's activity income came from (computed from the breakdown or given).
        supuestos = [
            *guardado_usado,
            *comparacion.avisos,
            *(
                f"Cónyuge {letra}: {actividad.aviso}"
                for letra, actividad in (("A", actividad_a), ("B", actividad_b))
                if actividad is not None
            ),
        ]

        blocks: list[str] = []
        ids: list[str] = []
        for nombre, result in (
            ("Individual del cónyuge A", comparacion.individual_a),
            ("Individual del cónyuge B", comparacion.individual_b),
            ("Conjunta", comparacion.conjunta),
        ):
            fragment = self.ledger.register(
                text=render_result(nombre, result),
                metadata={
                    "title": f"Cálculo IRPF {result.ejercicio} — {nombre}",
                    "source": f"calculadora://irpf/{result.ejercicio}/{len(self.ledger) + 1}",
                    "source_authority": "CALCULADORA",
                    "section": "Comparación de tributación individual y conjunta",
                },
            )
            ids.append(fragment.fragment_id)
            blocks.append(f"[{fragment.fragment_id}] " + render_result(nombre, result))
        veredicto = (
            "Las dos opciones dan el mismo resultado."
            if comparacion.mejor == "indiferente"
            else f"Sale mejor la declaración {comparacion.mejor}: ahorra {money(comparacion.ahorro)} "
            f"(individuales: {money(comparacion.total_individual)}; conjunta: {money(comparacion.total_conjunta)}; "
            "positivo = a pagar, negativo = a devolver)."
        )
        resumen = self.ledger.register(
            text=veredicto + "\nSupuestos: " + " ".join(supuestos),
            metadata={
                "title": f"Comparación individual / conjunta {year}",
                "source": f"calculadora://irpf/{year}/{len(self.ledger) + 1}",
                "source_authority": "CALCULADORA",
                "section": "Comparación de tributación individual y conjunta",
            },
        )
        blocks.insert(
            0, f"[{resumen.fragment_id}] {veredicto}\nSupuestos:\n" + "\n".join(f"  * {a}" for a in supuestos)
        )
        return ResultadoHerramienta(
            contenido="\n\n".join(blocks), resumen=f"mejor {comparacion.mejor}: {resumen.fragment_id}, {', '.join(ids)}"
        )

    def _optimize(self, arguments: dict[str, Any]) -> ResultadoHerramienta:
        conyuge = arguments.get("conyuge")
        if conyuge is not None and not isinstance(conyuge, dict):
            return _error("'conyuge' debe ser un objeto {campo: valor} con las cifras del otro cónyuge.")
        restricciones = {k: arguments[k] for k in _RESTRICCIONES if arguments.get(k) is not None}
        try:
            year, facts, datos = self._scenario_base(arguments)
            # With a case, the user's rejections and restrictions in force are constraints (asistente/nucleo/memoria).
            from asistente.nucleo.memoria.optimizador import optimizar_con_memoria

            plan = optimizar_con_memoria(
                case_id=self.case.case_id if self.case is not None else None,
                ejercicio=year,
                fecha=hoy_en_espana(),
                facts=facts,
                datos=datos,
                conyuge=conyuge,
                restricciones=restricciones,
            )
        except (DatosInvalidosError, EjercicioNoDisponibleError) as exc:
            return _error(str(exc))

        texto = render_plan(plan)
        resumen = self.ledger.register(
            text=texto,
            metadata={
                "title": f"Plan óptimo IRPF {plan.ejercicio}",
                "source": f"calculadora://optimizador/{plan.ejercicio}/{len(self.ledger) + 1}",
                "source_authority": "CALCULADORA",
                "section": "Optimización determinista",
            },
        )
        blocks = [f"[{resumen.fragment_id}] {texto}"]
        ids: list[str] = []
        # The returns of both scenarios, citable like any other calculation.
        for escenario, hogar in (("Situación actual", plan.base), ("Plan óptimo", plan.optimo)):
            for declaracion in hogar.declaraciones:
                quien = {"contribuyente": "usuario", "conyuge": "cónyuge", "conjunta": "conjunta"}[declaracion.titular]
                nombre = f"{escenario} ({quien})"
                result = declaracion.resultado
                fragment = self.ledger.register(
                    text=render_result(nombre, result),
                    metadata={
                        "title": f"Cálculo IRPF {result.ejercicio} — {nombre}",
                        "source": f"calculadora://irpf/{result.ejercicio}/{len(self.ledger) + 1}",
                        "source_authority": "CALCULADORA",
                        "section": "Optimización determinista",
                    },
                )
                ids.append(fragment.fragment_id)
                blocks.append(f"[{fragment.fragment_id}] " + render_result(nombre, result))
        return ResultadoHerramienta(
            contenido="\n\n".join(blocks),
            resumen=f"ahorro {money(plan.ahorro_total)} con {len(plan.palancas)} medida(s): "
            f"{resumen.fragment_id}, {', '.join(ids)}",
        )

    def _calendar(self, arguments: dict[str, Any]) -> ResultadoHerramienta:
        raw_days = arguments.get("dias", 365)
        days = raw_days if isinstance(raw_days, int) and not isinstance(raw_days, bool) else 365
        days = max(1, min(730, days))
        facts = {fact.name: fact.value for fact in self.case.facts} if self.case is not None else {}
        today = datetime.now(UTC).date()
        vencimientos = proximos_vencimientos(facts, hoy=today, dias=days)
        if not vencimientos:
            return ResultadoHerramienta(contenido="No hay vencimientos en ese plazo.", resumen="0 vencimientos")
        lines = [
            f"- {v.fecha.strftime('%d/%m/%Y')} (en {v.dias_restantes} días): {v.titulo} — {v.periodo}"
            + (f", modelo {v.modelo}" if v.modelo else "")
            + f". {v.detalle} [{v.referencia}{'' if v.verificado else '; fecha no contrastada'}]"
            + (f" Nota: {v.nota}" if v.nota else "")
            for v in vencimientos
        ]
        text = (
            f"Calendario fiscal del usuario a {today.strftime('%d/%m/%Y')} (próximos {days} días)\n"
            + "\n".join(lines)
            + f"\n{aviso_festivos()}\nSolo se listan las obligaciones que constan en el expediente: si el usuario "
            "tiene actividad económica o bienes en el extranjero y no lo ha dicho, no aparecen."
        )
        # Registered as evidence so dates in the answer can be cited and verified.
        fragment = self.ledger.register(
            text=text,
            metadata={
                "title": f"Calendario fiscal a {today.strftime('%d/%m/%Y')}",
                "source": f"calendario://{today.isoformat()}/{days}",
                "source_authority": "CALCULADORA",
                "section": "Plazos de presentación",
            },
        )
        return ResultadoHerramienta(
            contenido=f"[{fragment.fragment_id}] {text}",
            resumen=f"{len(vencimientos)} vencimientos: {fragment.fragment_id}",
        )

    def _benefits(self, arguments: dict[str, Any]) -> ResultadoHerramienta:
        try:
            year, facts, datos = self._scenario_base(arguments)
            params = load_params(year)
            inp, actividad = build_input_con_actividad(ejercicio=year, facts=facts, datos=datos)
        except (DatosInvalidosError, EjercicioNoDisponibleError) as exc:
            return _error(str(exc))
        conocidos = {name for name in (*facts, *datos) if name in IRPFInput.model_fields or name == "estado_civil"}
        if inp.descendientes:
            # The counts of children are known through the detail of each child.
            conocidos.update(RECUENTOS_DEL_DETALLE)
        beneficios = evaluar_beneficios(inp, conocidos=conocidos, params=params)
        payload = {
            "ejercicio": year,
            "beneficios": [
                {
                    "nombre": b.nombre,
                    "estado": b.estado,
                    "detalle": b.detalle,
                    "ahorro_estimado_eur": float(b.ahorro_estimado) if b.ahorro_estimado is not None else None,
                    "requisitos": b.requisitos,
                    "referencia": b.referencia,
                    "parametros_verificados": b.verificado,
                    "datos_que_faltan": b.datos_que_faltan,
                }
                for b in beneficios
            ],
            "nota": (
                "Los ahorros estimados salen de la calculadora con los datos disponibles; para citarlos ante el "
                "usuario, calcula el escenario con calcular_irpf y cita ese fragmento. Las condiciones de cada "
                "beneficio deben contrastarse con buscar_fuentes antes de afirmarlas."
            ),
        }
        if actividad is not None:
            # The activity's net income was derived (or deliberately not): the agent must say so.
            payload["actividad_economica"] = actividad.aviso
        disponibles = sum(1 for b in beneficios if b.estado == "disponible")
        return ResultadoHerramienta(
            contenido=json.dumps(payload, ensure_ascii=False),
            resumen=f"{len(beneficios)} beneficios revisados, {disponibles} disponibles",
        )


def caso_desde_perfil(perfil: Mapping[str, Any], *, hoy: date, case_id: str | None) -> CaseState:
    """A transient case with the tax facts of the profile (JSON facts back to their canonical text)."""
    facts: list[Fact] = []
    for nombre, valor in perfil.items():
        tipo = EXPECTED_FACT_TYPE.get(nombre)
        if tipo is None:
            continue  # another module's fact
        if nombre in DETALLE_FACT_TYPES and not isinstance(valor, str):
            try:
                valor = detalle_a_texto(nombre, valor)
            except DatosInvalidosError:
                continue
        facts.append(
            Fact(fact_id=f"fact_{nombre}", name=nombre, value=valor, value_type=tipo, source="perfil", confidence=1.0)
        )
    ahora = datetime.now(UTC)
    periodo = str(perfil.get("periodo_fiscal") or hoy.year)
    return CaseState(
        case_id=case_id or "perfil",
        user_id="perfil",
        jurisdiction="ES",
        tax_period=periodo,
        facts=facts,
        created_at=ahora,
        updated_at=ahora,
    )


def _ejecutor(nombre: str) -> Callable[[Mapping[str, Any], ContextoModulo], ResultadoHerramienta]:
    def ejecutar(argumentos: Mapping[str, Any], contexto: ContextoModulo) -> ResultadoHerramienta:
        if contexto.evidencia is not None:
            # A direct tool of the central agent: the run's ledger and the stored case.
            directa = EjecucionFiscal(ledger=contexto.evidencia, case=contexto.caso)
            return directa.manejadores()[nombre](dict(argumentos))
        caso = caso_desde_perfil(contexto.perfil, hoy=contexto.hoy, case_id=contexto.case_id)
        aislada = EjecucionFiscal(ledger=EvidenceLedger(), case=caso)
        try:
            resultado = aislada.manejadores()[nombre](dict(argumentos))
        except Exception as exc:  # a tool failure is reported, never raised
            logger.exception("tool %s failed", nombre)
            resultado = _error(f"La herramienta {nombre} falló: {exc}")
        # The throw-away ledger's ids mean nothing outside this call.
        return ResultadoHerramienta(
            contenido=_FRAGMENT_ID.sub("", resultado.contenido),
            resumen=_FRAGMENT_ID.sub("", resultado.resumen),
            es_error=resultado.es_error,
        )

    return ejecutar


# Every tax tool, in the order the central agent offers them. They only read: none acts outside the app.
HERRAMIENTAS: tuple[HerramientaModulo, ...] = tuple(
    HerramientaModulo(spec=spec, ejecutar=_ejecutor(spec.name), efecto="lectura") for spec in ESPECIFICACIONES
)


__all__ = ["HERRAMIENTAS", "EjecucionFiscal", "caso_desde_perfil"]
