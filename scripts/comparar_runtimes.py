"""Mide el cerebro sobre un conjunto fijo de turnos reales (la medición del ADR 0006).

Se escribió para comparar los runtimes ``propio`` y ``openhands``; tras el ADR 0006 solo queda el propio y la
etiqueta ``CEREBRO_RUNTIME`` solo sirve para nombrar la pasada. Sirve para repetir la medición con otro runtime.

Uso (un proceso por runtime, cada uno con su base de consumo):

    set -a; . .env; set +a
    MODULOS_ACTIVOS_POR_DEFECTO=todos CEREBRO_RUNTIME=propio DECISION_STATE_DB_PATH=/tmp/propio.sqlite3 \\
        python scripts/comparar_runtimes.py --out /tmp/propio.json

Guarda por turno: modo, citas, avisos, violaciones de la puerta, acciones propuestas, especialistas, pasos,
llamadas a herramientas, latencia, tokens, coste (de la tabla ``llm_usage``) y el error si lo hubo.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TURNOS: list[dict[str, str]] = [
    {"id": "fiscal_alquiler", "tipo": "fiscal", "q": "¿Qué reducción tiene en el IRPF el rendimiento neto de alquilar una vivienda habitual a un inquilino?"},
    {"id": "fiscal_donativos", "tipo": "fiscal", "q": "¿Cuánto me puedo deducir en el IRPF estatal por donativos a una ONG acogida a la Ley 49/2002?"},
    {"id": "fiscal_plazo_renta", "tipo": "fiscal", "q": "¿Estoy obligado a declarar el IRPF si solo cobro un salario de 18.000 euros de un único pagador?"},
    {"id": "fiscal_maternidad", "tipo": "fiscal", "q": "¿Cómo funciona la deducción por maternidad del IRPF y quién puede cobrarla por adelantado?"},
    {"id": "multi_hipoteca_irpf", "tipo": "multi", "q": "Tengo una hipoteca de 150.000 € al 3 % a 25 años firmada en 2012 para mi vivienda habitual. ¿Cuánto pago de cuota y puedo deducirme algo en la renta?"},
    {"id": "multi_luz_ayudas", "tipo": "multi", "q": "Pago mucho de luz y somos familia numerosa. ¿Qué tarifa me conviene y tengo derecho a algún bono o ayuda?"},
    {"id": "multi_caducidades_fiscal", "tipo": "multi", "q": "¿Qué plazos tengo este año para la renta y qué documentos del hogar me caducan pronto?"},
    {"id": "abst_fuera", "tipo": "abstencion", "q": "¿Quién va a ganar la Champions este año?"},
    {"id": "abst_futuro", "tipo": "abstencion", "q": "¿Cuál será el tipo marginal máximo del IRPF en 2031?"},
    {"id": "accion_baja_suscripcion", "tipo": "accion", "q": "Da de baja mi suscripción a Netflix, ya no la uso."},
    {"id": "accion_luz_cambio", "tipo": "accion", "q": "Cámbiame a la tarifa de luz más barata que encuentres."},
    {"id": "suscripciones_resumen", "tipo": "modulo", "q": "¿Cuánto me gasto al mes en suscripciones y cuáles podría quitar?"},
]


def coste_y_tokens(db: str, desde: int) -> tuple[int, float, int, int]:
    if not Path(db).exists():
        return desde, 0.0, 0, 0
    con = sqlite3.connect(db)
    try:
        filas = con.execute(
            "SELECT id, COALESCE(cost_usd,0), COALESCE(input_tokens,0), COALESCE(output_tokens,0) FROM llm_usage WHERE id > ?",
            (desde,),
        ).fetchall()
    except sqlite3.OperationalError:
        return desde, 0.0, 0, 0
    finally:
        con.close()
    if not filas:
        return desde, 0.0, 0, 0
    return max(f[0] for f in filas), sum(f[1] for f in filas), sum(f[2] for f in filas), sum(f[3] for f in filas)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--solo", default="")
    args = parser.parse_args()

    from asistente.nucleo.agent.agent import build_agent
    from asistente.nucleo.modulos.preferencias import modulos_activos_para
    from asistente.plataforma import settings

    runtime = os.environ.get("CEREBRO_RUNTIME", "propio")
    db = settings.DECISION_STATE_DB_PATH
    cerebro = build_agent()
    modulos = modulos_activos_para(None, None)
    ultimo = coste_y_tokens(db, 0)[0]
    salida = []
    for turno in TURNOS:
        if args.solo and turno["id"] not in args.solo.split(","):
            continue
        eventos: list[dict] = []
        inicio = time.monotonic()
        fila: dict = {"id": turno["id"], "tipo": turno["tipo"], "runtime": runtime}
        try:
            r = cerebro.run(turno["q"], on_event=eventos.append, modulos=modulos)
            inf = r.cerebro
            fila.update(
                modo=r.mode.value if hasattr(r.mode, "value") else str(r.mode),
                citas=len(r.citations),
                avisos=len(r.warnings),
                violaciones_puerta=(len(inf.puerta.violaciones) if inf and inf.puerta else 0),
                acciones=len(r.acciones_propuestas),
                especialistas=sorted({i.especialista for i in inf.informes}) if inf else [],
                ruta=inf.ruta if inf else None,
                pasos=r.steps,
                herramientas=sum(1 for e in eventos if e.get("type") == "tool_call"),
                respuesta=r.answer[:600],
                error=None,
            )
        except Exception as exc:  # noqa: BLE001
            fila.update(error=f"{type(exc).__name__}: {exc}", traza=traceback.format_exc()[-1500:])
        fila["latencia_s"] = round(time.monotonic() - inicio, 1)
        ultimo, coste, entrada, salida_t = coste_y_tokens(db, ultimo)
        fila.update(coste_usd=round(coste, 4), tokens_entrada=entrada, tokens_salida=salida_t)
        print(json.dumps({k: v for k, v in fila.items() if k not in ("respuesta", "traza")}, ensure_ascii=False), flush=True)
        salida.append(fila)
        Path(args.out).write_text(json.dumps(salida, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
    main()
