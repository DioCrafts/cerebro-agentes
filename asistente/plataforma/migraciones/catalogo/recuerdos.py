"""``asistente.nucleo.recuerdos`` (``AlmacenRecuerdos``): the conversation memory.

Five tables, every row tagged with its owner (``case_id``, ``user_id``; ``''`` when absent):

* ``recuerdos_conversaciones`` — one row per conversation of an owner (title, summary and the user's words it
  rests on, state);
* ``recuerdos_mensajes`` — the raw messages, kept ``MEMORIA_RETENCION_MENSAJES_DIAS`` and then purged;
* ``recuerdos`` — «lo que sé de ti»: each item with its origin (conversation, date, the user's literal words);
* ``recuerdos_lotes`` — summaries waiting in the provider's batch API (ids only, never text);
* ``recuerdos_interruptor`` — the user's «pausar la memoria» switch.

The two triggers erase an owner's memory with the case (``case_states``) and with the account (``users``),
so no code path that erases either can leave it behind; that is why the owner depends on ``decision`` and
``auth``. The store (``asistente/nucleo/recuerdos/almacen.py``) runs exactly these statements when the
migrations are off, so both paths build the same schema byte for byte.
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

CONVERSACIONES = """
        CREATE TABLE IF NOT EXISTS recuerdos_conversaciones (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            titulo TEXT NOT NULL,
            creada TEXT NOT NULL,
            actualizada TEXT NOT NULL,
            estado TEXT NOT NULL,
            resumen TEXT,
            cita TEXT,
            resumida_en TEXT,
            mensajes INTEGER NOT NULL DEFAULT 0,
            resumidos INTEGER NOT NULL DEFAULT 0,
            intentos INTEGER NOT NULL DEFAULT 0,
            terminos TEXT NOT NULL DEFAULT '',
            embedding BLOB,
            UNIQUE (case_id, user_id, id)
        )
        """
IDX_CONVERSACIONES = (
    "CREATE INDEX IF NOT EXISTS idx_recuerdos_conversaciones_titular "
    "ON recuerdos_conversaciones(case_id, user_id, actualizada)"
)
IDX_CONVERSACIONES_ESTADO = (
    "CREATE INDEX IF NOT EXISTS idx_recuerdos_conversaciones_estado ON recuerdos_conversaciones(estado, actualizada)"
)
MENSAJES = """
        CREATE TABLE IF NOT EXISTS recuerdos_mensajes (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            conversacion_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            n INTEGER NOT NULL,
            rol TEXT NOT NULL,
            texto TEXT NOT NULL,
            fecha TEXT NOT NULL,
            meta_json TEXT NOT NULL DEFAULT '{}'
        )
        """
IDX_MENSAJES = (
    "CREATE INDEX IF NOT EXISTS idx_recuerdos_mensajes_conversacion "
    "ON recuerdos_mensajes(case_id, user_id, conversacion_id, n)"
)
IDX_MENSAJES_FECHA = "CREATE INDEX IF NOT EXISTS idx_recuerdos_mensajes_fecha ON recuerdos_mensajes(fecha)"
RECUERDOS = """
        CREATE TABLE IF NOT EXISTS recuerdos (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            case_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            tipo TEXT NOT NULL,
            clave TEXT NOT NULL,
            texto TEXT NOT NULL,
            conversacion_id TEXT NOT NULL,
            fecha TEXT NOT NULL,
            cita TEXT NOT NULL,
            fuentes_json TEXT NOT NULL DEFAULT '[]',
            creado TEXT NOT NULL,
            actualizado TEXT NOT NULL,
            corregido INTEGER NOT NULL DEFAULT 0,
            sensible INTEGER NOT NULL DEFAULT 0,
            sustituido_por TEXT,
            estado TEXT NOT NULL DEFAULT 'activo',
            usos INTEGER NOT NULL DEFAULT 0,
            ultimo_uso TEXT,
            terminos TEXT NOT NULL DEFAULT '',
            embedding BLOB
        )
        """
IDX_RECUERDOS = "CREATE INDEX IF NOT EXISTS idx_recuerdos_titular ON recuerdos(case_id, user_id, estado)"
LOTES = """
        CREATE TABLE IF NOT EXISTS recuerdos_lotes (
            custom_id TEXT PRIMARY KEY,
            lote_id TEXT NOT NULL,
            proveedor TEXT NOT NULL,
            modelo TEXT NOT NULL,
            case_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversacion_id TEXT NOT NULL,
            mensajes INTEGER NOT NULL,
            enviado_el TEXT NOT NULL
        )
        """
IDX_LOTES = "CREATE INDEX IF NOT EXISTS idx_recuerdos_lotes_lote ON recuerdos_lotes(lote_id)"
INTERRUPTOR = """
        CREATE TABLE IF NOT EXISTS recuerdos_interruptor (
            titular TEXT PRIMARY KEY,
            activa INTEGER NOT NULL,
            cambiado_el TEXT NOT NULL
        )
        """
DISPARADOR_CASO = """
        CREATE TRIGGER IF NOT EXISTS recuerdos_borrar_con_caso AFTER DELETE ON case_states
        BEGIN
            DELETE FROM recuerdos_mensajes WHERE case_id = OLD.case_id;
            DELETE FROM recuerdos WHERE case_id = OLD.case_id;
            DELETE FROM recuerdos_lotes WHERE case_id = OLD.case_id;
            DELETE FROM recuerdos_conversaciones WHERE case_id = OLD.case_id;
            DELETE FROM recuerdos_interruptor WHERE titular = 'caso:' || OLD.case_id;
        END
        """
DISPARADOR_USUARIO = """
        CREATE TRIGGER IF NOT EXISTS recuerdos_borrar_con_usuario AFTER DELETE ON users
        BEGIN
            DELETE FROM recuerdos_mensajes WHERE user_id = OLD.id;
            DELETE FROM recuerdos WHERE user_id = OLD.id;
            DELETE FROM recuerdos_lotes WHERE user_id = OLD.id;
            DELETE FROM recuerdos_conversaciones WHERE user_id = OLD.id;
            DELETE FROM recuerdos_interruptor WHERE titular = OLD.id;
        END
        """

# Tables and indexes, in creation order (the store runs the same list when the migrations are off).
TABLAS_E_INDICES: tuple[str, ...] = (
    CONVERSACIONES,
    IDX_CONVERSACIONES,
    IDX_CONVERSACIONES_ESTADO,
    MENSAJES,
    IDX_MENSAJES,
    IDX_MENSAJES_FECHA,
    RECUERDOS,
    IDX_RECUERDOS,
    LOTES,
    IDX_LOTES,
    INTERRUPTOR,
)

PROPIETARIO = Propietario(
    nombre="recuerdos",
    paquete="asistente.nucleo.recuerdos",
    descripcion=(
        "Memoria de conversaciones: conversaciones, mensajes (retención limitada), «lo que sé de ti» con su "
        "origen, lotes pendientes y el interruptor del usuario."
    ),
    tablas=(
        "recuerdos_conversaciones",
        "recuerdos_mensajes",
        "recuerdos",
        "recuerdos_lotes",
        "recuerdos_interruptor",
    ),
    depende_de=("decision", "auth"),
    otros_objetos=(
        "idx_recuerdos_conversaciones_titular",
        "idx_recuerdos_conversaciones_estado",
        "idx_recuerdos_mensajes_conversacion",
        "idx_recuerdos_mensajes_fecha",
        "idx_recuerdos_titular",
        "idx_recuerdos_lotes_lote",
        "recuerdos_borrar_con_caso",
        "recuerdos_borrar_con_usuario",
    ),
    migraciones=(Migracion(1, "esquema_inicial", (*TABLAS_E_INDICES, DISPARADOR_CASO, DISPARADOR_USUARIO)),),
)
