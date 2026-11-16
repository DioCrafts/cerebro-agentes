"""``asistente.nucleo.tareas`` (``TareaStore``): long-running tasks, their append-only history and their signals.

Baseline copied byte for byte from ``tareas/almacen.py`` (``_DDL``, ``_DISPARADOR``). The erasure trigger hangs
on ``case_states``, so (as the store always did) version 2 waits until that table exists.
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

DDL = (
    """
    CREATE TABLE IF NOT EXISTS tareas (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        case_id TEXT NOT NULL,
        estado TEXT NOT NULL,
        proxima_revision TEXT,
        version INTEGER NOT NULL DEFAULT 0,
        bloqueado_por TEXT,
        bloqueado_hasta TEXT,
        actualizada TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tareas_case ON tareas(case_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_tareas_estado ON tareas(estado)",
    """
    CREATE TABLE IF NOT EXISTS tareas_historial (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        tarea_id TEXT NOT NULL,
        case_id TEXT NOT NULL,
        momento TEXT NOT NULL,
        evento TEXT NOT NULL,
        paso_id TEXT,
        detalle_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tareas_historial ON tareas_historial(tarea_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_tareas_historial_case ON tareas_historial(case_id)",
    """
    CREATE TABLE IF NOT EXISTS tareas_senales (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        tarea_id TEXT NOT NULL,
        paso_id TEXT NOT NULL,
        case_id TEXT NOT NULL,
        evento_id INTEGER NOT NULL,
        tipo TEXT NOT NULL,
        creada TEXT NOT NULL,
        procesada INTEGER NOT NULL DEFAULT 0,
        UNIQUE (tarea_id, paso_id, evento_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tareas_senales ON tareas_senales(procesada, tarea_id)",
)
DISPARADOR = """
    CREATE TRIGGER IF NOT EXISTS tareas_borrar_con_caso AFTER DELETE ON case_states
    BEGIN
        DELETE FROM tareas WHERE case_id = OLD.case_id;
        DELETE FROM tareas_historial WHERE case_id = OLD.case_id;
        DELETE FROM tareas_senales WHERE case_id = OLD.case_id;
    END
"""

PROPIETARIO = Propietario(
    nombre="tareas",
    paquete="asistente.nucleo.tareas",
    descripcion="Tareas de larga duración: plan, pasos, historial (sin valores) y señales del bus.",
    tablas=("tareas", "tareas_historial", "tareas_senales"),
    otros_objetos=(
        "idx_tareas_case",
        "idx_tareas_estado",
        "idx_tareas_historial",
        "idx_tareas_historial_case",
        "idx_tareas_senales",
        "tareas_borrar_con_caso",
    ),
    migraciones=(
        Migracion(1, "esquema_inicial", DDL),
        Migracion(2, "borrar_con_el_caso", (DISPARADOR,), requiere_tablas=("case_states",)),
    ),
)
