"""The calculation tools (``calculo_importes``, ``calculo_prestamo``) as module tools and as brain tools.

They compute with figures the USER gives in the conversation (not the household case), so a specialist can answer
«¿cuánto gasto al año en Netflix 13,99, Spotify 10,99…?» or «hipoteca de 180.000 € al 3,2 % a 25 años, ¿amortizo
20.000 € en cuota o plazo?» without a stored fact. Every output figure is declared as a ``CifraHerramienta``: the
collaboration runtime registers the result as a ``CALCULADORA`` ledger fragment and the figures gate attributes the
figures to this tool. A figure the model works out by itself still has no origin and is rejected.

The result text always ends with the assumptions taken (fixed rate, ×12…), which the specialist must state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from asistente.nucleo.agent.providers.base import ToolSpec
from asistente.nucleo.calculo.operaciones import (
    OPERACIONES,
    CalculoError,
    Resultado,
    calcular_importes,
    calcular_prestamo,
    leer_decimal,
    leer_importes,
)
from asistente.nucleo.modulos.contrato import (
    CifraHerramienta,
    ContextoModulo,
    HerramientaModulo,
    ResultadoHerramienta,
)

if TYPE_CHECKING:  # pragma: no cover - agent.tools imports the modules, which import this
    from asistente.nucleo.agent.tools import ToolOutcome

IMPORTES = "calculo_importes"
PRESTAMO = "calculo_prestamo"

_NUMERO = {"type": ["number", "string"]}

SPEC_IMPORTES = ToolSpec(
    name=IMPORTES,
    description=(
        "Calculadora exacta de importes con los datos que da el usuario en la conversación (no hace falta que "
        "estén en el expediente). Operaciones: sumar (con periodicidad mes/año da el total al mes y al año), "
        "restar (el primero menos los demás), multiplicar (por «factor»), anualizar (×12), mensualizar (÷12), "
        "porcentaje (un % de cada importe, y el importe con y sin ese %) y variacion (diferencia y % de cambio "
        "del primero al segundo). Úsala SIEMPRE que tengas que sumar, restar, multiplicar o prorratear cifras: no "
        "hagas cuentas de cabeza. Devuelve las cifras y los supuestos que debes decir en la respuesta."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operacion": {"type": "string", "enum": list(OPERACIONES)},
            "importes": {
                "type": "array",
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "concepto": {"type": "string", "description": "Qué es (Netflix, gimnasio…)."},
                        "valor": {**_NUMERO, "description": "Importe tal como lo dio el usuario (13,99 o 13.99)."},
                        "periodicidad": {"type": "string", "enum": ["mes", "año", "unico"]},
                    },
                    "required": ["valor"],
                },
            },
            "factor": {**_NUMERO, "description": "Para multiplicar."},
            "porcentaje": {**_NUMERO, "description": "Para porcentaje: 21 es el 21 %."},
            "decimales": {"type": "integer", "minimum": 0, "maximum": 6, "description": "Redondeo (por defecto 2)."},
        },
        "required": ["operacion", "importes"],
    },
)

SPEC_PRESTAMO = ToolSpec(
    name=PRESTAMO,
    description=(
        "Calculadora de un préstamo a TIPO FIJO (sistema francés, cuotas mensuales) con los datos que da el usuario "
        "en la conversación, sin necesidad de que el préstamo esté guardado: cuota, intereses totales, cuadro de "
        "amortización y, si se indica «amortizacion», el ahorro de intereses al amortizar anticipadamente reduciendo "
        "cuota o reduciendo plazo. Si el préstamo es variable o mixto, dilo: el cálculo supone el tipo fijo."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "capital": {**_NUMERO, "description": "Capital pendiente (o prestado) en euros."},
            "tipo_anual_pct": {**_NUMERO, "description": "Tipo nominal anual en %: 3,2 es el 3,2 %."},
            "plazo_meses": {"type": "integer", "minimum": 1, "maximum": 600},
            "plazo_anos": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Alternativa a plazo_meses."},
            "amortizacion": {**_NUMERO, "description": "Importe a amortizar anticipadamente (opcional)."},
            "tras_cuota": {
                "type": "integer",
                "minimum": 0,
                "description": "Tras qué cuota se amortiza (0 = ahora, antes de la primera; por defecto 0).",
            },
            "filas_cuadro": {
                "type": "integer",
                "minimum": 0,
                "maximum": 12,
                "description": "Cuántas primeras cuotas del cuadro mostrar (0 = ninguna; con >0 añade el resumen de los primeros años).",
            },
        },
        "required": ["capital", "tipo_anual_pct"],
    },
)


def _resultado(res: Resultado) -> ResultadoHerramienta:
    lineas = [res.titulo + ":", *res.detalle]
    lineas += [f"- {linea.concepto}: {linea.literal}" for linea in res.lineas]
    lineas += ["", "Supuestos (dilos en la respuesta):", *[f"- {s}" for s in res.supuestos]]
    cifras = tuple(
        CifraHerramienta(concepto=linea.concepto[:200], valor=linea.valor, unidad=linea.unidad, literal=linea.literal)
        for linea in res.lineas
    )
    principal = res.lineas[0] if res.lineas else None
    resumen = f"{res.titulo}: {principal.concepto} {principal.literal}" if principal else res.titulo
    return ResultadoHerramienta(contenido="\n".join(lineas), resumen=resumen[:160], cifras=cifras)


def _error(mensaje: str) -> ResultadoHerramienta:
    return ResultadoHerramienta(
        contenido=f"No he podido calcular: {mensaje}", resumen="Error de cálculo", es_error=True
    )


def _entero(valor: Any, nombre: str) -> int | None:
    if valor is None or valor == "":
        return None
    numero = leer_decimal(valor, nombre)
    if numero != numero.to_integral_value():
        raise CalculoError(f"«{nombre}» debe ser un número entero.")
    return int(numero)


def ejecutar_importes(argumentos: Mapping[str, Any], contexto: ContextoModulo | None = None) -> ResultadoHerramienta:
    try:
        argumentos = argumentos or {}
        factor = argumentos.get("factor")
        porcentaje = argumentos.get("porcentaje")
        res = calcular_importes(
            str(argumentos.get("operacion") or "").strip().lower(),
            leer_importes(argumentos.get("importes")),
            factor=leer_decimal(factor, "factor") if factor not in (None, "") else None,
            porcentaje=leer_decimal(porcentaje, "porcentaje") if porcentaje not in (None, "") else None,
            decimales=_entero(argumentos.get("decimales"), "decimales") or 2,
        )
    except CalculoError as exc:
        return _error(str(exc))
    except ArithmeticError as exc:
        return _error(f"operación no válida ({type(exc).__name__}).")
    return _resultado(res)


def ejecutar_prestamo(argumentos: Mapping[str, Any], contexto: ContextoModulo | None = None) -> ResultadoHerramienta:
    try:
        argumentos = argumentos or {}
        meses = _entero(argumentos.get("plazo_meses"), "plazo_meses")
        anos = _entero(argumentos.get("plazo_anos"), "plazo_anos")
        if meses is None and anos is None:
            raise CalculoError("Indica el plazo (plazo_meses o plazo_anos).")
        if meses is not None and anos is not None and meses != anos * 12:
            raise CalculoError("plazo_meses y plazo_anos no coinciden: indica solo uno.")
        amortizacion = argumentos.get("amortizacion")
        res = calcular_prestamo(
            leer_decimal(argumentos.get("capital"), "capital"),
            leer_decimal(argumentos.get("tipo_anual_pct"), "tipo_anual_pct"),
            meses if meses is not None else (anos or 0) * 12,
            amortizacion=leer_decimal(amortizacion, "amortizacion") if amortizacion not in (None, "") else None,
            tras_cuota=_entero(argumentos.get("tras_cuota"), "tras_cuota") or 0,
            filas_cuadro=_entero(argumentos.get("filas_cuadro"), "filas_cuadro") or 0,
            hoy=contexto.hoy if contexto is not None else None,
        )
    except CalculoError as exc:
        return _error(str(exc))
    except (ValueError, ArithmeticError) as exc:
        return _error(f"datos no válidos ({exc}).")
    return _resultado(res)


def herramientas_de_modulo(clave: str, *, prestamo: bool = False) -> tuple[HerramientaModulo, ...]:
    """The tools for module ``clave``, named ``<clave>_calculo_importes`` (and ``<clave>_calculo_prestamo``):
    the registry requires a module's tools to carry its prefix."""
    herramientas = [HerramientaModulo(replace(SPEC_IMPORTES, name=f"{clave}_{IMPORTES}"), ejecutar_importes, "lectura")]
    if prestamo:
        herramientas.append(
            HerramientaModulo(replace(SPEC_PRESTAMO, name=f"{clave}_{PRESTAMO}"), ejecutar_prestamo, "lectura")
        )
    return tuple(herramientas)


def instrucciones_calculo(clave: str, *, prestamo: bool = False) -> str:
    """The paragraph a module adds to its instructions to use the tools of :func:`herramientas_de_modulo`."""
    nombres = f"{clave}_{IMPORTES} (sumas, restas, ×12, ÷12, porcentajes)"
    if prestamo:
        nombres += f" y {clave}_{PRESTAMO} (préstamo a tipo fijo: cuota, cuadro, amortizar reduciendo cuota o plazo)"
    return (
        f" Para cuentas con datos que el usuario da en la conversación (y no están en el expediente) usa {nombres}. "
        "No hagas cuentas de cabeza: cada cifra de tu informe debe salir de una herramienta o del usuario. Di siempre "
        "los supuestos que devuelve la herramienta (por ejemplo, que se ha supuesto tipo fijo o que el año son 12 "
        "meses iguales)."
    )


ADENDA_CEREBRO = (
    "\n\nCÁLCULOS CON DATOS DEL USUARIO: si la respuesta necesita una cuenta con cifras que el usuario ha dado en la "
    "conversación (sumar sus suscripciones, pasar de mes a año, la cuota de un préstamo que describe), usa "
    "calculo_importes o calculo_prestamo; nunca hagas la cuenta tú. Di los supuestos que devuelvan."
)


def herramientas_para_cerebro(ledger: Any) -> list[tuple[ToolSpec, Callable[[dict[str, Any]], ToolOutcome]]]:
    """The two tools as brain tools: each result is registered in the turn's ledger as a CALCULADORA fragment."""
    from asistente.nucleo.agent.tools import ToolOutcome

    def envolver(nombre: str, funcion: Callable[..., ResultadoHerramienta]) -> Callable[[dict[str, Any]], ToolOutcome]:
        def ejecutar(argumentos: dict[str, Any]) -> ToolOutcome:
            resultado = funcion(argumentos)
            if resultado.es_error:
                return ToolOutcome(content=resultado.contenido, summary=resultado.resumen, is_error=True)
            fragmento = ledger.register(
                text=resultado.contenido,
                metadata={
                    "title": f"Calculadora · {nombre}",
                    "source": f"calculo://{nombre}",
                    "source_authority": "CALCULADORA",
                    "section": "Cálculo con datos del usuario",
                },
            )
            return ToolOutcome(
                content=f"[{fragmento.fragment_id}] {resultado.contenido}",
                summary=f"{resultado.resumen}: {fragmento.fragment_id}"[:160],
            )

        return ejecutar

    return [
        (SPEC_IMPORTES, envolver(IMPORTES, ejecutar_importes)),
        (SPEC_PRESTAMO, envolver(PRESTAMO, ejecutar_prestamo)),
    ]


__all__ = [
    "ADENDA_CEREBRO",
    "herramientas_para_cerebro",
    "IMPORTES",
    "herramientas_de_modulo",
    "instrucciones_calculo",
    "PRESTAMO",
    "SPEC_IMPORTES",
    "SPEC_PRESTAMO",
    "ejecutar_importes",
    "ejecutar_prestamo",
]
