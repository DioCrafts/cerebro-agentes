"""``hacienda_gpt.evaluacion`` (``FeedbackStore``): review rows of the users' feedback (no conversation text).

Baseline copied byte for byte from ``evaluacion/captura.py``; the trigger waits for ``case_states``.
"""

from __future__ import annotations

from hacienda_gpt.plataforma.migraciones.motor import Migracion, Propietario

TABLA = """
    CREATE TABLE IF NOT EXISTS evaluacion_feedback (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        case_id TEXT,
        payload_json TEXT NOT NULL
    )
"""
DISPARADOR = """
    CREATE TRIGGER IF NOT EXISTS evaluacion_feedback_borra_con_el_caso
    AFTER DELETE ON case_states
    BEGIN
        DELETE FROM evaluacion_feedback WHERE case_id = OLD.case_id;
    END
"""

PROPIETARIO = Propietario(
    nombre="evaluacion",
    paquete="hacienda_gpt.evaluacion",
    descripcion="Valoraciones de las respuestas y su revisión (el contenido vive en audit_events).",
    tablas=("evaluacion_feedback",),
    otros_objetos=("idx_evaluacion_feedback_case", "evaluacion_feedback_borra_con_el_caso"),
    migraciones=(
        Migracion(
            1,
            "esquema_inicial",
            (TABLA, "CREATE INDEX IF NOT EXISTS idx_evaluacion_feedback_case ON evaluacion_feedback(case_id)"),
        ),
        Migracion(2, "borrar_con_el_caso", (DISPARADOR,), requiere_tablas=("case_states",)),
    ),
)
