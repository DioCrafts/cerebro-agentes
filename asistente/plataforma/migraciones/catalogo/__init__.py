"""The product's owners and their migrations, in the order a new database is built.

One module per owner package; each exports ``PROPIETARIO`` (and ``proactive`` also
``PROPIETARIO_ENTREGAS``). Importing this package imports none of the owners' packages: the migrations are
data, so the stores can call the engine without import cycles.
"""

from __future__ import annotations

from asistente.plataforma.migraciones.catalogo import (
    acciones,
    auth,
    consejo,
    consumo,
    correo,
    decision,
    doctrina,
    evaluacion,
    eventos,
    memoria,
    navegador,
    plataforma,
    proactive,
    push,
    recuerdos_hindsight,
    repaso,
    tareas,
)
from asistente.plataforma.migraciones.motor import Propietario

PROPIETARIOS: tuple[Propietario, ...] = (
    decision.PROPIETARIO,
    auth.PROPIETARIO,
    proactive.PROPIETARIO,
    proactive.PROPIETARIO_ENTREGAS,
    acciones.PROPIETARIO,
    memoria.PROPIETARIO,
    evaluacion.PROPIETARIO,
    eventos.PROPIETARIO,
    correo.PROPIETARIO,
    consumo.PROPIETARIO,
    navegador.PROPIETARIO,
    plataforma.PROPIETARIO,
    doctrina.PROPIETARIO,
    tareas.PROPIETARIO,
    push.PROPIETARIO,
    recuerdos_hindsight.PROPIETARIO,
    repaso.PROPIETARIO,
    consejo.PROPIETARIO,
)

__all__ = ["PROPIETARIOS"]
