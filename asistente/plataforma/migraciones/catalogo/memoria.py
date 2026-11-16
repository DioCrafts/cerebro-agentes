"""``asistente.nucleo.memoria`` (``DecisionStore``): the user's decisions and their history.

Baseline copied byte for byte from ``memoria/almacen.py``. The erasure trigger hangs on ``case_states``,
so (as the store always did) it waits until that table exists: version 2 is deferred until then.
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

DECISIONES = """
        CREATE TABLE IF NOT EXISTS decisiones (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            case_id TEXT NOT NULL,
            user_id TEXT,
            ambito TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
DECISIONES_HISTORIAL = """
        CREATE TABLE IF NOT EXISTS decisiones_historial (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            momento TEXT NOT NULL,
            evento TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
DISPARADOR = """
        CREATE TRIGGER IF NOT EXISTS decisiones_borrar_con_caso AFTER DELETE ON case_states
        BEGIN
            DELETE FROM decisiones WHERE case_id = OLD.case_id;
            DELETE FROM decisiones_historial WHERE case_id = OLD.case_id;
        END
        """

PROPIETARIO = Propietario(
    nombre="memoria",
    paquete="asistente.nucleo.memoria",
    descripcion="Decisiones del usuario sobre propuestas y su historial (redactado al borrar).",
    tablas=("decisiones", "decisiones_historial"),
    otros_objetos=("idx_decisiones_case", "idx_decisiones_historial_case", "decisiones_borrar_con_caso"),
    migraciones=(
        Migracion(
            1,
            "esquema_inicial",
            (
                DECISIONES,
                "CREATE INDEX IF NOT EXISTS idx_decisiones_case ON decisiones(case_id, seq)",
                DECISIONES_HISTORIAL,
                "CREATE INDEX IF NOT EXISTS idx_decisiones_historial_case ON decisiones_historial(case_id, seq)",
            ),
        ),
        Migracion(2, "borrar_con_el_caso", (DISPARADOR,), requiere_tablas=("case_states",)),
    ),
)
