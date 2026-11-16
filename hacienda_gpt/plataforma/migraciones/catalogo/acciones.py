"""``hacienda_gpt.modulos.acciones`` (``AccionStore``): actions waiting for the user's confirmation.

Baseline copied byte for byte from ``modulos/acciones.py``.
"""

from __future__ import annotations

from hacienda_gpt.plataforma.migraciones.motor import Migracion, Propietario

ACCIONES_PENDIENTES = """
                    CREATE TABLE IF NOT EXISTS acciones_pendientes (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        case_id TEXT NOT NULL,
                        estado TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """

PROPIETARIO = Propietario(
    nombre="acciones",
    paquete="hacienda_gpt.modulos",
    descripcion="Acciones de los módulos pendientes de confirmación del usuario.",
    tablas=("acciones_pendientes",),
    otros_objetos=("idx_acciones_case",),
    migraciones=(
        Migracion(
            1,
            "esquema_inicial",
            (
                ACCIONES_PENDIENTES,
                "CREATE INDEX IF NOT EXISTS idx_acciones_case ON acciones_pendientes(case_id, seq)",
            ),
        ),
    ),
)
