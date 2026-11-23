"""The only thing this module stores: the derived list of recurring services (fact
``suscripciones_servicios``). Raw statements and movements are never stored.

Stored per service: id, label, category (and where it came from), legal regime of the cancellation,
periodicity, last amount (or the average, if it varies), payment means, first/last/next charge dates,
status, confidence, number of charges, and the last price change, double charge and trial detected.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from typing import Any

from pydantic import ValidationError

from asistente.ambitos.suscripciones.deteccion import sigue_activa
from asistente.ambitos.suscripciones.modelos import ServicioGuardado, ServiciosGuardados

NOMBRE_HECHO = "suscripciones_servicios"


def validar_servicios(valor: Any) -> dict[str, Any]:
    """Validator of the fact: canonical JSON of :class:`ServiciosGuardados`, or ValueError in Spanish."""
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except json.JSONDecodeError as exc:
            raise ValueError("La lista de servicios no es un JSON válido.") from exc
    try:
        modelo = ServiciosGuardados.model_validate(valor)
    except ValidationError as exc:
        raise ValueError(f"La lista de servicios no tiene el formato esperado: {exc.errors()[0]['msg']}.") from exc
    salida: dict[str, Any] = modelo.model_dump(mode="json")
    return salida


def leer(perfil: Any) -> ServiciosGuardados | None:
    """The stored list, or None if there is none. Raises ValueError if it is corrupt."""
    valor = perfil.get(NOMBRE_HECHO) if hasattr(perfil, "get") else None
    if valor in (None, "", {}):
        return None
    return ServiciosGuardados.model_validate(validar_servicios(valor))


def fusionar(
    anterior: ServiciosGuardados | None, nuevos: list[ServicioGuardado], hoy: date, hasta: date | None
) -> ServiciosGuardados:
    """Merge a new analysis into the stored list: per id, the most recent data wins; services of other
    statements (another account, an older period) are kept, but one with no charge for longer than its
    period allows by the end of the latest statement is marked inactive, so it stops adding to the totals."""
    por_id: dict[str, ServicioGuardado] = {s.id: s for s in (anterior.servicios if anterior else [])}
    for servicio in nuevos:
        previo = por_id.get(servicio.id)
        if previo is None or servicio.ultima_fecha >= previo.ultima_fecha:
            por_id[servicio.id] = servicio
    extracto_hasta = max(
        (d for d in (hasta, anterior.extracto_hasta if anterior else None) if d is not None), default=None
    )
    if extracto_hasta is not None:
        for id_, servicio in por_id.items():
            if servicio.estado == "activa" and not sigue_activa(
                servicio.periodicidad, servicio.ultima_fecha, extracto_hasta
            ):
                por_id[id_] = servicio.model_copy(update={"estado": "inactiva", "proxima_fecha": None})
    servicios = sorted(por_id.values(), key=lambda s: (s.estado != "activa", s.etiqueta, s.id))
    return ServiciosGuardados(actualizado_el=hoy, extracto_hasta=extracto_hasta, servicios=servicios)


def total(servicios: list[ServicioGuardado]) -> Decimal:
    """Yearly cost in euros of the active services; amounts in another currency are not added (no rate)."""
    return sum((s.coste_anual for s in servicios if s.estado == "activa" and s.divisa == "EUR"), Decimal(0))
