"""``asistente.nucleo.repaso`` (``RepasoStore``): the brain's proactive passes (one row per case and period).

The store creates exactly these statements when the migrations are off (``repaso/almacen.py`` imports them from
here, so the baseline is byte for byte the store's own DDL). The erasure trigger hangs on ``case_states``, so
version 2 waits until that table exists, like the decisions' and the tasks' triggers.
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

PASADAS = """
    CREATE TABLE IF NOT EXISTS repaso_pasadas (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id TEXT NOT NULL,
        periodo TEXT NOT NULL,
        estado TEXT NOT NULL,
        origen TEXT,
        notification_id TEXT,
        lote_id TEXT,
        custom_id TEXT,
        evento_hasta INTEGER NOT NULL DEFAULT 0,
        creada TEXT NOT NULL,
        actualizada TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        UNIQUE (case_id, periodo)
    )
    """
INDICE_LOTES = "CREATE INDEX IF NOT EXISTS idx_repaso_pasadas_lote ON repaso_pasadas(estado, lote_id)"
DISPARADOR = """
    CREATE TRIGGER IF NOT EXISTS repaso_borrar_con_caso AFTER DELETE ON case_states
    BEGIN
        DELETE FROM repaso_pasadas WHERE case_id = OLD.case_id;
    END
    """

PROPIETARIO = Propietario(
    nombre="repaso",
    paquete="asistente.nucleo.repaso",
    descripcion="Repaso proactivo del asistente: una pasada por caso y periodo (puntos, redacción, lote en curso).",
    tablas=("repaso_pasadas",),
    otros_objetos=("idx_repaso_pasadas_lote", "repaso_borrar_con_caso"),
    migraciones=(
        Migracion(1, "esquema_inicial", (PASADAS, INDICE_LOTES)),
        Migracion(2, "borrar_con_el_caso", (DISPARADOR,), requiere_tablas=("case_states",)),
    ),
)
