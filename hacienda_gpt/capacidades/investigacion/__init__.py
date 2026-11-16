"""Research with sources («investigar con fuentes»): a capability any domain, the brain and the tasks can use.

* ``capacidad.py`` — ``CAPACIDAD`` under the capability contract: operation ``investigar`` (read-only);
* ``servicio.py`` — the method: plan → gather (corpus, BOE, web) → synthesise → deterministic gate;
* ``puerta.py`` — the citation gate (literal quotes, figures, injection), conflicts, confidence;
* ``fuentes.py`` / ``web.py`` — the sources; ``dominios.py`` — official sites, allow / block lists;
* ``presupuesto.py`` — caps per research; ``almacen.py`` — web consent and cached reports (export, erasure);
* ``herramienta.py`` — the agent tool ``investigar``; ``tareas.py`` — the long-running-task step.

See ``docs/capacidades/investigacion.md``.
"""

from hacienda_gpt.capacidades.investigacion.capacidad import (
    CAPACIDAD,
    borrar_usuario,
    configurar,
    exportar_usuario,
    investigador_por_defecto,
)
from hacienda_gpt.capacidades.investigacion.modelo import Informe, PeticionInvestigacion
from hacienda_gpt.capacidades.investigacion.servicio import Investigador

__all__ = [
    "CAPACIDAD",
    "Informe",
    "Investigador",
    "PeticionInvestigacion",
    "borrar_usuario",
    "configurar",
    "exportar_usuario",
    "investigador_por_defecto",
]
