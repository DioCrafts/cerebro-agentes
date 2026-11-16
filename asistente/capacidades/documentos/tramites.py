"""Paperwork operations of the documents capability, used by long-running tasks (``asistente/nucleo/tareas``).

Moved here from ``tareas/capacidades.py``: a checklist of what to have at hand for a procedure (for passports and
ID cards from the expiry module's data, with its BOE references; for the income tax return an orientative list
derived from the case's facts), a draft letter (marked as such, never sent) and a request to the user to have a
document ready. Deterministic; no model; nothing leaves the app.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from asistente.capacidades.contrato import ContextoPeticion, ResultadoCapacidad

ORIGENES_LISTA = ("caducidades:pasaporte", "caducidades:dni", "renta")
MODELOS_BORRADOR = ("solicitud_cambio_comercializadora",)

# The income tax checklist: (fact names that make the item relevant, item). Orientative, said so: it is a
# list of papers the user's own data point to, not a legal list of requirements.
_LISTA_RENTA: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("rendimiento_trabajo_bruto", "retenciones", "num_pagadores_trabajo"),
        "Certificado de retenciones de cada pagador",
    ),
    (
        ("intereses_prestamo_vivienda", "pagos_vivienda_habitual_pre2013"),
        "Certificado del banco de lo pagado del préstamo de la vivienda",
    ),
    (
        ("alquiler_vivienda_habitual", "dias_alquiler_vivienda_habitual"),
        "Contrato y recibos del alquiler de tu vivienda",
    ),
    (
        ("rendimiento_alquiler_neto", "rendimiento_alquiler_vivienda_sin_reducir"),
        "Ingresos y gastos de los inmuebles que alquilas",
    ),
    (
        ("aportacion_plan_pensiones", "contribucion_empresa_plan_pensiones"),
        "Certificado de aportaciones a planes de pensiones",
    ),
    (("donativos",), "Certificados de los donativos"),
    (("rendimiento_capital_mobiliario",), "Información fiscal de tus cuentas e inversiones"),
    (
        ("ganancias_patrimoniales", "perdidas_patrimoniales_pendientes"),
        "Justificantes de compras y ventas de inversiones o inmuebles",
    ),
    (
        ("es_autonomo", "actividad_economica", "actividad_ingresos_integros"),
        "Libros de ingresos y gastos de tu actividad",
    ),
)


def _schema(propiedades: dict[str, Any], requeridas: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": propiedades,
        **({"required": list(requeridas)} if requeridas else {}),
    }


ENTRADA_LISTA = _schema({"origen": {"type": "string", "enum": list(ORIGENES_LISTA)}}, ("origen",))
ENTRADA_BORRADOR = _schema({"modelo": {"type": "string", "enum": list(MODELOS_BORRADOR)}}, ("modelo",))
ENTRADA_PEDIR = _schema({"documento": {"type": "string", "minLength": 3, "maxLength": 120}}, ("documento",))
SALIDA_VACIA: dict[str, Any] = {"type": "object"}


def lista_de_comprobacion(args: Mapping[str, Any], ctx: ContextoPeticion) -> ResultadoCapacidad:
    origen = str(args["origen"])
    if origen.startswith("caducidades:"):
        from asistente.ambitos.caducidades.reglas import cargar_reglas

        regla = getattr(cargar_reglas(), origen.split(":", 1)[1])
        tramite = regla.tramite
        lineas = [f"{tramite.nombre} ({tramite.organismo}):"] + [f"- {p}" for p in tramite.pasos]
        evidencias = tuple(dict.fromkeys(f"{e.boe_id} ({e.articulo})" for e in regla.evidencia))
        return ResultadoCapacidad(resumen="\n".join(lineas), citas=evidencias)
    perfil = ctx.perfil or {}
    elementos = [
        texto for nombres, texto in _LISTA_RENTA if any(perfil.get(n) not in (None, "", 0, False) for n in nombres)
    ]
    if not elementos:
        elementos = ["Tus datos fiscales de la Agencia Tributaria, para comprobarlos con tu expediente"]
    resumen = (
        "Lista orientativa de papeles, según los datos de tu expediente (no es una relación legal de "
        "requisitos):\n" + "\n".join(f"- {e}" for e in elementos)
    )
    return ResultadoCapacidad(resumen=resumen, citas=("expediente",))


def borrador(args: Mapping[str, Any], ctx: ContextoPeticion) -> ResultadoCapacidad:
    del args  # one model today: the closed enum is checked by the input schema and the plan gate
    cups = (ctx.perfil or {}).get("luz_cups")
    texto = (
        "BORRADOR — revísalo y envíalo tú a la comercializadora que elijas; HaciendaGPT no lo envía.\n\n"
        "Asunto: Solicitud de contratación del suministro eléctrico\n\n"
        "Solicito contratar con ustedes el suministro eléctrico de mi vivienda con la oferta [nombre de la "
        "oferta elegida], manteniendo la potencia contratada actual.\n"
        f"CUPS: {cups if isinstance(cups, str) and cups else '[tu CUPS, viene en la factura]'}\n"
        "Titular: [nombre y apellidos] — DNI/NIE: [número]\n"
        "Dirección del suministro: [dirección]\n"
        "Cuenta para la domiciliación: [IBAN]\n\n"
        "Comprueba antes en tu contrato actual si tienes permanencia o penalización por baja."
    )
    return ResultadoCapacidad(
        resumen="Borrador preparado para que lo revises y lo envíes tú (no se ha enviado nada).",
        borrador=texto,
        aviso=(
            "Revisa el borrador de tu solicitud",
            "Tienes un borrador listo en «Tareas». No se ha enviado nada.",
        ),
    )


def pedir_documento(args: Mapping[str, Any], ctx: ContextoPeticion) -> ResultadoCapacidad:
    documento = str(args["documento"])
    cuerpo = f"Ten preparado: {documento}. Cuando lo tengas, márcalo como hecho en «Tareas»."
    return ResultadoCapacidad(estado="esperando_usuario", resumen=cuerpo, aviso=(f"Prepara: {documento}", cuerpo))


__all__ = [
    "ENTRADA_BORRADOR",
    "ENTRADA_LISTA",
    "ENTRADA_PEDIR",
    "MODELOS_BORRADOR",
    "ORIGENES_LISTA",
    "SALIDA_VACIA",
    "borrador",
    "lista_de_comprobacion",
    "pedir_documento",
]
