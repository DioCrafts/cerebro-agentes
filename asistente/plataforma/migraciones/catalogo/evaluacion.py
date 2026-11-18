"""``asistente.nucleo.evaluacion`` (``FeedbackStore``, ``CandidatoStore``): review rows of the users' feedback (no
conversation text), the candidate cases of the review queue (masked) and the per-case consent to learn from
detected mistakes.

Baseline copied byte for byte from ``evaluacion/captura.py``; the triggers wait for ``case_states``. The stores
import the DDL of versions 3 and 4 from here, so the old-code path and the migrations create the same text.
(Accepted real cases live in a separate private file, ``evaluacion/privado.py``, outside this catalogue.)
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

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

TABLA_CANDIDATOS = """
    CREATE TABLE IF NOT EXISTS evaluacion_candidatos (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        clave TEXT NOT NULL UNIQUE,
        case_id TEXT,
        grupo TEXT NOT NULL,
        estado TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
"""
INDICE_CANDIDATOS_CASO = "CREATE INDEX IF NOT EXISTS idx_evaluacion_candidatos_case ON evaluacion_candidatos(case_id)"
INDICE_CANDIDATOS_GRUPO = (
    "CREATE INDEX IF NOT EXISTS idx_evaluacion_candidatos_grupo ON evaluacion_candidatos(grupo, estado)"
)
TABLA_APRENDIZAJE = """
    CREATE TABLE IF NOT EXISTS evaluacion_aprendizaje (
        case_id TEXT PRIMARY KEY,
        version TEXT NOT NULL,
        otorgado_el TEXT NOT NULL
    )
"""
DISPARADOR_CANDIDATOS = """
    CREATE TRIGGER IF NOT EXISTS evaluacion_candidatos_borra_con_el_caso
    AFTER DELETE ON case_states
    BEGIN
        DELETE FROM evaluacion_candidatos WHERE case_id = OLD.case_id;
        DELETE FROM evaluacion_aprendizaje WHERE case_id = OLD.case_id;
    END
"""

PROPIETARIO = Propietario(
    nombre="evaluacion",
    paquete="asistente.nucleo.evaluacion",
    descripcion=(
        "Valoraciones de las respuestas y su revisión (el contenido vive en audit_events), casos candidatos "
        "enmascarados y el consentimiento para aprender de los fallos detectados."
    ),
    tablas=("evaluacion_feedback", "evaluacion_candidatos", "evaluacion_aprendizaje"),
    otros_objetos=(
        "idx_evaluacion_feedback_case",
        "evaluacion_feedback_borra_con_el_caso",
        "idx_evaluacion_candidatos_case",
        "idx_evaluacion_candidatos_grupo",
        "evaluacion_candidatos_borra_con_el_caso",
    ),
    migraciones=(
        Migracion(
            1,
            "esquema_inicial",
            (TABLA, "CREATE INDEX IF NOT EXISTS idx_evaluacion_feedback_case ON evaluacion_feedback(case_id)"),
        ),
        Migracion(2, "borrar_con_el_caso", (DISPARADOR,), requiere_tablas=("case_states",)),
        Migracion(
            3,
            "candidatos_y_aprendizaje",
            (TABLA_CANDIDATOS, INDICE_CANDIDATOS_CASO, INDICE_CANDIDATOS_GRUPO, TABLA_APRENDIZAJE),
        ),
        Migracion(4, "candidatos_con_el_caso", (DISPARADOR_CANDIDATOS,), requiere_tablas=("case_states",)),
    ),
)
