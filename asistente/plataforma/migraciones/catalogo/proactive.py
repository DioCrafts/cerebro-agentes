"""``asistente.nucleo.proactive``: notifications (``NotificationStore``) and their deliveries (``EntregaStore``).

Two owners because the stores are separate: the deliveries table exists only where something delivers.
Baseline copied byte for byte from ``proactive/store.py`` and ``proactive/entrega.py``.
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

NOTIFICATIONS = """
                CREATE TABLE IF NOT EXISTS notifications (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    case_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    read_at TEXT,
                    payload_json TEXT NOT NULL,
                    UNIQUE (case_id, key)
                )
                """
NOTIFICATION_DELIVERIES = """
                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    notification_id TEXT NOT NULL,
                    canal TEXT NOT NULL,
                    delivered_at TEXT NOT NULL,
                    PRIMARY KEY (notification_id, canal)
                )
                """

PROPIETARIO = Propietario(
    nombre="proactive",
    paquete="asistente.nucleo.proactive",
    descripcion="Avisos de cada caso (bandeja), únicos por (case_id, key).",
    tablas=("notifications",),
    otros_objetos=("idx_notifications_case",),
    migraciones=(
        Migracion(
            1,
            "esquema_inicial",
            (
                NOTIFICATIONS,
                "CREATE INDEX IF NOT EXISTS idx_notifications_case ON notifications(case_id, seq)",
            ),
        ),
    ),
)

PROPIETARIO_ENTREGAS = Propietario(
    nombre="proactive_entrega",
    paquete="asistente.nucleo.proactive",
    descripcion="Qué aviso se entregó por qué canal (webhook, correo).",
    tablas=("notification_deliveries",),
    depende_de=("proactive",),
    migraciones=(Migracion(1, "esquema_inicial", (NOTIFICATION_DELIVERIES,)),),
)
