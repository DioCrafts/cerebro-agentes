"""``asistente.nucleo.consejo`` (``AlmacenConsejo``): the household council's sessions.

One row per session (``consejo_sesiones``), tagged with its owner (``case_id``, ``user_id``; ``''`` without
accounts): the state machine (``estado``, ``fase``, the worker's lease ``arriendo_id`` / ``arriendo_hasta``, runs
``intentos``), the estimate and the cap the user confirmed, the cost spent, and one JSON column per part of the
deliberation — the pseudonymised dossier, the proposals, the anonymous critiques, the rankings and the ruling — plus
``extra_json`` (origin, suggested options, live progress). ``pregunta_huella`` is a hash of the normalised question
(the same convocation is not created twice), never the question.

The two triggers erase the sessions with the case (``case_states``) and with the account (``users``), so no code
path that erases either can leave them behind; that is why the owner depends on ``decision`` and ``auth``. The store
(``asistente/nucleo/consejo/almacen.py``) runs exactly these statements when the migrations are off.
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

SESIONES = """
        CREATE TABLE IF NOT EXISTS consejo_sesiones (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            case_id TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT '',
            estado TEXT NOT NULL,
            fase TEXT,
            pregunta_usuario TEXT NOT NULL,
            pregunta_huella TEXT NOT NULL,
            creada TEXT NOT NULL,
            actualizada TEXT NOT NULL,
            estimacion_usd TEXT NOT NULL,
            tope_usd TEXT NOT NULL,
            coste_json TEXT NOT NULL DEFAULT '{}',
            arriendo_id TEXT,
            arriendo_hasta TEXT,
            intentos INTEGER NOT NULL DEFAULT 0,
            notificacion_id TEXT,
            error TEXT,
            expediente_json TEXT,
            propuestas_json TEXT NOT NULL DEFAULT '[]',
            criticas_json TEXT NOT NULL DEFAULT '[]',
            rankings_json TEXT NOT NULL DEFAULT '[]',
            dictamen_json TEXT,
            extra_json TEXT NOT NULL DEFAULT '{}'
        )
        """
IDX_CASO = "CREATE INDEX IF NOT EXISTS idx_consejo_sesiones_caso ON consejo_sesiones(case_id, user_id, creada)"
IDX_ESTADO = "CREATE INDEX IF NOT EXISTS idx_consejo_sesiones_estado ON consejo_sesiones(estado, arriendo_hasta)"
DISPARADOR_CASO = """
        CREATE TRIGGER IF NOT EXISTS consejo_borrar_con_caso AFTER DELETE ON case_states
        BEGIN
            DELETE FROM consejo_sesiones WHERE case_id = OLD.case_id;
        END
        """
DISPARADOR_USUARIO = """
        CREATE TRIGGER IF NOT EXISTS consejo_borrar_con_usuario AFTER DELETE ON users
        BEGIN
            DELETE FROM consejo_sesiones WHERE user_id = OLD.id;
        END
        """

# Tables and indexes, in creation order (the store runs the same list when the migrations are off).
TABLAS_E_INDICES: tuple[str, ...] = (SESIONES, IDX_CASO, IDX_ESTADO)

PROPIETARIO = Propietario(
    nombre="consejo",
    paquete="asistente.nucleo.consejo",
    descripcion=(
        "Consejo del hogar: sesiones de deliberación (estado, arriendo del trabajador, estimación y tope de coste, "
        "expediente seudonimizado, propuestas, críticas anónimas, rankings y dictamen)."
    ),
    tablas=("consejo_sesiones",),
    depende_de=("decision", "auth"),
    otros_objetos=(
        "idx_consejo_sesiones_caso",
        "idx_consejo_sesiones_estado",
        "consejo_borrar_con_caso",
        "consejo_borrar_con_usuario",
    ),
    migraciones=(Migracion(1, "esquema_inicial", (*TABLAS_E_INDICES, DISPARADOR_CASO, DISPARADOR_USUARIO)),),
)
