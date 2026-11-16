"""``asistente.capacidades.navegador`` (``navegador/persistencia.py``): browser tasks, shared by every process.

* ``navegador_tareas`` — one row per task: its state, steps and messages, which process runs it and that
  process's heartbeat, and the stop / close requests from other processes. The parameters the user typed
  are kept only while the task is ``preparada`` (so another process can start it) and dropped when it
  starts. Never a screenshot, the summary of values that would be sent, a session or a credential.
* ``navegador_inicios`` — when each user (SHA-256 of the key) started a task, for the 24-hour limit;
  rows older than 24 hours are purged.

Both go with the case (trigger on ``case_states``, deferred until that table exists).
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

TAREAS = """CREATE TABLE IF NOT EXISTS navegador_tareas (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    user_id TEXT,
    clave TEXT NOT NULL,
    estado TEXT NOT NULL,
    propietario TEXT,
    latido TEXT,
    detener INTEGER NOT NULL DEFAULT 0,
    cerrar INTEGER NOT NULL DEFAULT 0,
    creada TEXT NOT NULL,
    actualizada TEXT NOT NULL,
    terminada TEXT,
    parametros_json TEXT,
    payload_json TEXT NOT NULL
)"""
INICIOS = """CREATE TABLE IF NOT EXISTS navegador_inicios (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    clave_usuario TEXT NOT NULL,
    momento REAL NOT NULL,
    tarea_id TEXT,
    case_id TEXT
)"""
DISPARADOR = """CREATE TRIGGER IF NOT EXISTS navegador_borrar_con_caso AFTER DELETE ON case_states
BEGIN
    DELETE FROM navegador_tareas WHERE case_id = OLD.case_id;
    UPDATE navegador_inicios SET tarea_id = NULL, case_id = NULL WHERE case_id = OLD.case_id;
END"""

PROPIETARIO = Propietario(
    nombre="navegador",
    paquete="asistente.capacidades.navegador",
    descripcion="Tareas del agente con navegador y arranques por usuario (límite de 24 h).",
    tablas=("navegador_tareas", "navegador_inicios"),
    otros_objetos=("idx_navegador_tareas_case", "idx_navegador_inicios", "navegador_borrar_con_caso"),
    migraciones=(
        Migracion(
            1,
            "esquema_inicial",
            (
                TAREAS,
                "CREATE INDEX IF NOT EXISTS idx_navegador_tareas_case ON navegador_tareas(case_id)",
                INICIOS,
                "CREATE INDEX IF NOT EXISTS idx_navegador_inicios ON navegador_inicios(clave_usuario, momento)",
            ),
        ),
        Migracion(2, "borrar_con_el_caso", (DISPARADOR,), requiere_tablas=("case_states",)),
    ),
)
