"""``asistente.nucleo.recuerdos`` (``AlmacenPausa``): the conversation memory's only table in the cases' database.

The memory itself lives in Hindsight (one bank per owner). Here is just the user's «pausar la memoria» switch:
one row per paused owner (``case_id``, ``user_id``; ``''`` when absent), erased with the case and with the account.

Version 1 also drops what the previous, SQLite-based memory left in existing databases (its five tables and two
triggers, all empty or no longer read) and its rows in ``schema_migrations``, so no trace of it remains.
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

RETIRADA: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS recuerdos_borrar_con_caso",
    "DROP TRIGGER IF EXISTS recuerdos_borrar_con_usuario",
    "DROP TABLE IF EXISTS recuerdos_mensajes",
    "DROP TABLE IF EXISTS recuerdos_lotes",
    "DROP TABLE IF EXISTS recuerdos_interruptor",
    "DROP TABLE IF EXISTS recuerdos",
    "DROP TABLE IF EXISTS recuerdos_conversaciones",
    "DELETE FROM schema_migrations WHERE propietario = 'recuerdos'",
)
PAUSA = """
    CREATE TABLE IF NOT EXISTS recuerdos_pausa (
        case_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        desde TEXT NOT NULL,
        PRIMARY KEY (case_id, user_id)
    )
    """
DISPARADOR_CASO = """
    CREATE TRIGGER IF NOT EXISTS recuerdos_pausa_borrar_con_caso AFTER DELETE ON case_states
    BEGIN
        DELETE FROM recuerdos_pausa WHERE case_id = OLD.case_id;
    END
    """
DISPARADOR_USUARIO = """
    CREATE TRIGGER IF NOT EXISTS recuerdos_pausa_borrar_con_usuario AFTER DELETE ON users
    BEGIN
        DELETE FROM recuerdos_pausa WHERE user_id = OLD.id;
    END
    """

PROPIETARIO = Propietario(
    nombre="recuerdos_hindsight",
    paquete="asistente.nucleo.recuerdos",
    descripcion="Memoria de conversaciones (en Hindsight): el interruptor «pausar la memoria» de cada usuario.",
    tablas=("recuerdos_pausa",),
    depende_de=("decision", "auth"),
    otros_objetos=("recuerdos_pausa_borrar_con_caso", "recuerdos_pausa_borrar_con_usuario"),
    migraciones=(
        Migracion(1, "pausa_y_retirada_de_la_memoria_sqlite", (*RETIRADA, PAUSA)),
        Migracion(
            2,
            "borrar_con_el_caso_y_la_cuenta",
            (DISPARADOR_CASO, DISPARADOR_USUARIO),
            requiere_tablas=("case_states", "users"),
        ),
    ),
)
