"""``asistente.plataforma.push`` (``PushStore``): Web Push subscriptions and the users' opt-in.

Baseline copied byte for byte from ``push/almacen.py``. The trigger erases a user's rows when the account
is deleted (``users`` belongs to ``auth``, hence the dependency).
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

PUSH_SUSCRIPCIONES = """
                CREATE TABLE IF NOT EXISTS push_suscripciones (
                    id TEXT PRIMARY KEY,
                    propietario TEXT NOT NULL,
                    endpoint TEXT NOT NULL UNIQUE,
                    p256dh TEXT NOT NULL,
                    auth TEXT NOT NULL,
                    creada TEXT NOT NULL,
                    ultimo_envio TEXT,
                    fallos_seguidos INTEGER NOT NULL DEFAULT 0
                )
                """
PUSH_CONSENTIMIENTOS = """
                CREATE TABLE IF NOT EXISTS push_consentimientos (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    propietario TEXT NOT NULL,
                    concedido INTEGER NOT NULL,
                    momento TEXT NOT NULL,
                    version_texto TEXT NOT NULL
                )
                """
DISPARADOR_BORRADO = """
            CREATE TRIGGER IF NOT EXISTS push_borrar_suscripciones_con_usuario AFTER DELETE ON users
            BEGIN
                DELETE FROM push_suscripciones WHERE propietario = OLD.id;
                DELETE FROM push_consentimientos WHERE propietario = OLD.id;
            END
            """

PROPIETARIO = Propietario(
    nombre="push",
    paquete="asistente.plataforma.push",
    descripcion="Avisos en el móvil (Web Push): dispositivos suscritos y consentimiento de cada usuario.",
    tablas=("push_suscripciones", "push_consentimientos"),
    depende_de=("auth",),
    otros_objetos=(
        "idx_push_suscripciones_propietario",
        "idx_push_consentimientos",
        "push_borrar_suscripciones_con_usuario",
    ),
    migraciones=(
        Migracion(
            1,
            "esquema_inicial",
            (
                PUSH_SUSCRIPCIONES,
                "CREATE INDEX IF NOT EXISTS idx_push_suscripciones_propietario ON push_suscripciones(propietario)",
                PUSH_CONSENTIMIENTOS,
                "CREATE INDEX IF NOT EXISTS idx_push_consentimientos ON push_consentimientos(propietario, seq)",
                DISPARADOR_BORRADO,
            ),
        ),
    ),
)
