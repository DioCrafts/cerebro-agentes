"""How the tax tools' results read: the citable texts the model receives (Spanish, figures with their sources).

Moved verbatim from the advisor's toolbox (``agent/tools.py``).
"""

from __future__ import annotations

from decimal import Decimal

from asistente.ambitos.fiscal.calculo.irpf import IRPFResult
from asistente.ambitos.fiscal.calculo.obligacion import ResultadoObligacion
from asistente.ambitos.fiscal.calculo.optimizador import PlanOptimo


def money(value: Decimal) -> str:
    """1234.5 → '1.234,50 €' (Spanish formatting, sign preserved)."""
    text = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if value < 0 else ''}{text} €"


def render_result(nombre: str, result: IRPFResult) -> str:
    lines = [f"Cálculo del IRPF {result.ejercicio} — {nombre} (tributación {result.tributacion.replace('_', ' ')})"]
    for line in result.lineas:
        ref = f" [{line.referencia}]" if line.referencia else ""
        lines.append(f"- {line.concepto}: {money(line.importe)}{ref}")
    lines.append(f"- Tipo medio efectivo: {result.tipo_medio_efectivo} %")
    if result.obligacion_declarar is not None:
        lines.append(
            f"- ¿Obligado a declarar? {_obligado_texto(result.obligacion_declarar)} (detalle: obligacion_declarar)"
        )
    if result.avisos:
        lines.append("Avisos (lo que este cálculo no cubre o da por supuesto):")
        lines.extend(f"  * {aviso}" for aviso in result.avisos)
    lines.append(f"Parámetros: {result.fuente}.")
    return "\n".join(lines)


OBLIGADO = {"si": "sí", "no": "no", "no_se_sabe": "no se puede saber con estos datos"}


def _obligado_texto(obligacion: ResultadoObligacion) -> str:
    regla = f" [{obligacion.regla.referencia}]" if obligacion.regla is not None else ""
    return OBLIGADO[obligacion.obligado] + regla


def render_obligacion(r: ResultadoObligacion) -> str:
    """The answer to «¿estoy obligado a declarar?» as a citable text."""
    lines = [f"Obligación de declarar el IRPF {r.ejercicio}: {OBLIGADO[r.obligado].upper()}", r.respuesta]
    for titulo, reglas in (("Regla que lo decide", [r.regla] if r.regla else []), ("También aplica", r.otras_reglas)):
        for regla in reglas:
            lines.append(f"{titulo} [{regla.referencia}, {regla.boe_id}]: «{regla.cita}»")
    if r.datos_que_faltan:
        lines.append("Datos que faltan (pregúntaselos al usuario):")
        lines.extend(f"  * {d.pregunta}" + (f" [dato: {d.campo}]" if d.campo else "") for d in r.datos_que_faltan)
    if r.recomendacion:
        lines.append(f"Recomendación: {r.recomendacion}")
    if r.lineas:
        lines.append("Cifras usadas:")
        lines.extend(
            f"- {linea.concepto}: {money(linea.importe)}" + (f" [{linea.referencia}]" if linea.referencia else "")
            for linea in r.lineas
        )
    if r.avisos:
        lines.append("Avisos (lo que esta respuesta no cubre o da por supuesto):")
        lines.extend(f"  * {aviso}" for aviso in r.avisos)
    if r.parametros_no_verificados:
        lines.append("Parámetros no contrastados: " + ", ".join(r.parametros_no_verificados) + ".")
    lines.append(f"Fuente: {r.fuente}.")
    return "\n".join(lines)


_NATURALEZA = {
    "ahorro_definitivo": "ahorro definitivo",
    "diferimiento": "diferimiento (se recupera más adelante)",
    "requiere_gastar": "requiere gastar",
}


def render_plan(plan: PlanOptimo) -> str:
    """The optimiser's plan as a citable text: every figure the answer may quote is in it."""
    modo = {
        "individual": "individual",
        "conjunta_biparental": "conjunta",
        "conjunta_monoparental": "conjunta monoparental",
    }
    lines = [
        f"Plan óptimo del IRPF {plan.ejercicio} a {plan.fecha.strftime('%d/%m/%Y')} "
        + (
            "(antes del cierre del ejercicio)"
            if plan.fase == "antes_del_cierre"
            else "(solo opciones de la declaración)"
        ),
        f"- Situación actual: resultado {money(plan.base.resultado_total)} (tributación "
        f"{modo[plan.base.tributacion]}; positivo = a pagar, negativo = a devolver).",
        f"- Con el plan: resultado {money(plan.optimo.resultado_total)} (tributación {modo[plan.optimo.tributacion]}).",
        f"- Ahorro total del plan combinado: {money(plan.ahorro_total)}.",
    ]
    if plan.palancas:
        lines.append(
            "Medidas (el ahorro de cada una es lo que aporta DENTRO del plan; no se suman los ahorros por separado):"
        )
        for n, p in enumerate(plan.palancas, start=1):
            detalle = [
                f"ahorro dentro del plan {money(p.ahorro_marginal)}",
                f"por sí sola {money(p.ahorro_aislado)}"
                + ("" if p.accion_aislada == p.accion else f" («{p.accion_aislada}»)"),
                _NATURALEZA[p.naturaleza],
            ]
            if p.compromiso:
                detalle.append(f"compromiso {money(p.compromiso)}")
            if p.ahorro_por_euro is not None:
                detalle.append(f"{str(p.ahorro_por_euro).replace('.', ',')} € de ahorro por euro comprometido")
            if p.coste_neto is not None:
                detalle.append(f"coste neto {money(p.coste_neto)}")
            lines.append(f"{n}. {p.accion}: " + "; ".join(detalle) + f" [{p.referencia}]")
            lines.extend(f"   - Requisito: {r}" for r in p.requisitos)
        lines.append(
            f"Suma de los ahorros por separado: {money(plan.suma_ahorros_aislados)}; diferencia con el plan "
            f"combinado: {money(plan.interaccion)}."
        )
        lines.extend(f"  * {m}" for m in plan.explicacion_interaccion)
    else:
        lines.append("No hay ninguna combinación de las opciones disponibles que reduzca el impuesto.")
    if plan.cambios_traza:
        lines.append(
            "Líneas del cálculo que cambian (suma del hogar): "
            + "; ".join(f"{c.concepto}: {money(c.antes)} → {money(c.despues)}" for c in plan.cambios_traza)
        )
    for titulo, items in (
        ("Opciones no disponibles", [f"{nd.nombre}: {nd.detalle}" for nd in plan.no_disponibles]),
        ("Opciones descartadas", [f"{d.nombre}: {d.motivo} [{d.referencia}]" for d in plan.descartadas]),
        ("Otras oportunidades", plan.oportunidades),
        ("Supuestos (trasládalos al usuario)", plan.supuestos),
        ("Datos que faltan", plan.datos_que_faltan),
    ):
        if items:
            lines.append(f"{titulo}:")
            lines.extend(f"  * {item}" for item in items)
    lines.append(f"Búsqueda {plan.busqueda.metodo}: {plan.busqueda.nota}")
    lines.append("Citas: " + "; ".join(plan.citas))
    return "\n".join(lines)


__all__ = ["OBLIGADO", "money", "render_obligacion", "render_plan", "render_result"]
