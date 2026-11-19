"""``asistente.plataforma.mensajeria`` (``AlmacenMensajeria``): the messaging gateway to the household brain.

* ``mensajeria_codigos`` — one-time pairing codes the owner generated in the app: only the SHA-256 of the code
  (``huella``), the channel, the owner (``user_id``, ``''`` without accounts, and ``case_id``), when it expires and
  when it was used. Never the code.
* ``mensajeria_vinculos`` — the paired chats: the channel and the adapter's opaque contact id, the owner, the server
  conversation id the chat talks in, the display name the contact chose (untrusted, for the UI), dates and whether
  the outgoing notices are paused. One row per (channel, contact).
* ``mensajeria_procesados`` — hashes of the message ids already handled (a redelivered message never runs twice);
  purged after ``MENSAJERIA_RETENCION_DIAS``.
* ``mensajeria_intentos`` — failed pairing attempts and the once-a-day reply to unknown contacts, by a hash of the
  contact (the brute-force limits); purged with the processed ids.
* ``mensajeria_confirmaciones`` — one-time confirmation codes in chat (an action, a council convocation, unpairing):
  the item, the hash of the code, expiry and use. Never the code.
* ``mensajeria_entregas`` — which inbox notice was delivered to which paired chat (deduplication).
* ``mensajeria_desconexiones`` — contacts the worker must remove on the channel (unpaired from the app, or their
  account or case erased): a short-lived queue, emptied as soon as the channel confirms.
* ``mensajeria_canales`` — the state each channel last published (connected, the bot's address), so the API process
  can show it: the channel itself runs in the worker.

The two triggers erase a case's or an account's bindings, codes, confirmations and deliveries in the same statement
that erases the case or the account, and queue the contacts for disconnection, so no code path that erases either
can leave a chat paired; that is why the owner depends on ``decision`` and ``auth``. The store runs exactly these
statements when the migrations are off.
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

CODIGOS = """
        CREATE TABLE IF NOT EXISTS mensajeria_codigos (
            id TEXT PRIMARY KEY,
            huella TEXT NOT NULL UNIQUE,
            canal TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT '',
            case_id TEXT NOT NULL,
            creado TEXT NOT NULL,
            caduca TEXT NOT NULL,
            usado TEXT
        )
        """
IDX_CODIGOS = "CREATE INDEX IF NOT EXISTS idx_mensajeria_codigos_usuario ON mensajeria_codigos(user_id, canal, creado)"
VINCULOS = """
        CREATE TABLE IF NOT EXISTS mensajeria_vinculos (
            id TEXT PRIMARY KEY,
            canal TEXT NOT NULL,
            contacto TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT '',
            case_id TEXT NOT NULL,
            conversacion_id TEXT NOT NULL,
            nombre_mostrado TEXT,
            creado TEXT NOT NULL,
            ultimo_uso TEXT,
            pausado INTEGER NOT NULL DEFAULT 0,
            UNIQUE (canal, contacto)
        )
        """
IDX_VINCULOS_USUARIO = (
    "CREATE INDEX IF NOT EXISTS idx_mensajeria_vinculos_usuario ON mensajeria_vinculos(user_id, canal)"
)
IDX_VINCULOS_CASO = "CREATE INDEX IF NOT EXISTS idx_mensajeria_vinculos_caso ON mensajeria_vinculos(case_id)"
PROCESADOS = """
        CREATE TABLE IF NOT EXISTS mensajeria_procesados (
            huella TEXT PRIMARY KEY,
            recibido TEXT NOT NULL
        )
        """
IDX_PROCESADOS = "CREATE INDEX IF NOT EXISTS idx_mensajeria_procesados_recibido ON mensajeria_procesados(recibido)"
INTENTOS = """
        CREATE TABLE IF NOT EXISTS mensajeria_intentos (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            huella TEXT NOT NULL,
            momento TEXT NOT NULL
        )
        """
IDX_INTENTOS = "CREATE INDEX IF NOT EXISTS idx_mensajeria_intentos ON mensajeria_intentos(tipo, huella, momento)"
CONFIRMACIONES = """
        CREATE TABLE IF NOT EXISTS mensajeria_confirmaciones (
            id TEXT PRIMARY KEY,
            vinculo_id TEXT NOT NULL,
            tipo TEXT NOT NULL,
            objeto_id TEXT NOT NULL,
            huella TEXT NOT NULL,
            creado TEXT NOT NULL,
            caduca TEXT NOT NULL,
            usado TEXT
        )
        """
IDX_CONFIRMACIONES = (
    "CREATE INDEX IF NOT EXISTS idx_mensajeria_confirmaciones_vinculo ON mensajeria_confirmaciones(vinculo_id, huella)"
)
ENTREGAS = """
        CREATE TABLE IF NOT EXISTS mensajeria_entregas (
            vinculo_id TEXT NOT NULL,
            notification_id TEXT NOT NULL,
            estado TEXT NOT NULL,
            intentos INTEGER NOT NULL DEFAULT 0,
            momento TEXT NOT NULL,
            PRIMARY KEY (vinculo_id, notification_id)
        )
        """
DESCONEXIONES = """
        CREATE TABLE IF NOT EXISTS mensajeria_desconexiones (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            canal TEXT NOT NULL,
            contacto TEXT NOT NULL,
            motivo TEXT NOT NULL,
            solicitado TEXT NOT NULL,
            intentos INTEGER NOT NULL DEFAULT 0
        )
        """
CANALES = """
        CREATE TABLE IF NOT EXISTS mensajeria_canales (
            canal TEXT PRIMARY KEY,
            estado_json TEXT NOT NULL,
            actualizado TEXT NOT NULL
        )
        """
DISPARADOR_CASO = """
        CREATE TRIGGER IF NOT EXISTS mensajeria_borrar_con_caso AFTER DELETE ON case_states
        BEGIN
            INSERT INTO mensajeria_desconexiones (canal, contacto, motivo, solicitado)
                SELECT canal, contacto, 'borrado', strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
                FROM mensajeria_vinculos WHERE case_id = OLD.case_id;
            DELETE FROM mensajeria_entregas
                WHERE vinculo_id IN (SELECT id FROM mensajeria_vinculos WHERE case_id = OLD.case_id);
            DELETE FROM mensajeria_confirmaciones
                WHERE vinculo_id IN (SELECT id FROM mensajeria_vinculos WHERE case_id = OLD.case_id);
            DELETE FROM mensajeria_vinculos WHERE case_id = OLD.case_id;
            DELETE FROM mensajeria_codigos WHERE case_id = OLD.case_id;
        END
        """
DISPARADOR_USUARIO = """
        CREATE TRIGGER IF NOT EXISTS mensajeria_borrar_con_usuario AFTER DELETE ON users
        BEGIN
            INSERT INTO mensajeria_desconexiones (canal, contacto, motivo, solicitado)
                SELECT canal, contacto, 'borrado', strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
                FROM mensajeria_vinculos WHERE user_id = OLD.id;
            DELETE FROM mensajeria_entregas
                WHERE vinculo_id IN (SELECT id FROM mensajeria_vinculos WHERE user_id = OLD.id);
            DELETE FROM mensajeria_confirmaciones
                WHERE vinculo_id IN (SELECT id FROM mensajeria_vinculos WHERE user_id = OLD.id);
            DELETE FROM mensajeria_vinculos WHERE user_id = OLD.id;
            DELETE FROM mensajeria_codigos WHERE user_id = OLD.id;
        END
        """

# Tables and indexes, in creation order (the store runs the same list when the migrations are off).
TABLAS_E_INDICES: tuple[str, ...] = (
    CODIGOS,
    IDX_CODIGOS,
    VINCULOS,
    IDX_VINCULOS_USUARIO,
    IDX_VINCULOS_CASO,
    PROCESADOS,
    IDX_PROCESADOS,
    INTENTOS,
    IDX_INTENTOS,
    CONFIRMACIONES,
    IDX_CONFIRMACIONES,
    ENTREGAS,
    DESCONEXIONES,
    CANALES,
)

PROPIETARIO = Propietario(
    nombre="mensajeria",
    paquete="asistente.plataforma.mensajeria",
    descripcion=(
        "Mensajería con el cerebro del hogar (SimpleX): códigos de emparejamiento (solo su huella), chats vinculados, "
        "mensajes ya procesados, intentos, códigos de confirmación (solo su huella), avisos entregados, desconexiones "
        "pendientes y estado de los canales."
    ),
    tablas=(
        "mensajeria_codigos",
        "mensajeria_vinculos",
        "mensajeria_procesados",
        "mensajeria_intentos",
        "mensajeria_confirmaciones",
        "mensajeria_entregas",
        "mensajeria_desconexiones",
        "mensajeria_canales",
    ),
    depende_de=("decision", "auth"),
    otros_objetos=(
        "idx_mensajeria_codigos_usuario",
        "idx_mensajeria_vinculos_usuario",
        "idx_mensajeria_vinculos_caso",
        "idx_mensajeria_procesados_recibido",
        "idx_mensajeria_intentos",
        "idx_mensajeria_confirmaciones_vinculo",
        "mensajeria_borrar_con_caso",
        "mensajeria_borrar_con_usuario",
    ),
    migraciones=(Migracion(1, "esquema_inicial", (*TABLAS_E_INDICES, DISPARADOR_CASO, DISPARADOR_USUARIO)),),
)
