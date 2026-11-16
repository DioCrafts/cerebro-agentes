"""``hacienda_gpt.auth`` (``AuthStore``): accounts, sessions, consents, case ownership, module choices.

Baseline copied byte for byte from ``auth/store.py`` (SQLite keeps the statement text).
"""

from __future__ import annotations

from hacienda_gpt.plataforma.migraciones.motor import Migracion, Propietario

USERS = """
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        email TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
AUTH_SESSIONS = """
                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        token_hash TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        revoked_at TEXT
                    )
                    """
USER_CONSENTS = """
                    CREATE TABLE IF NOT EXISTS user_consents (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        granted INTEGER NOT NULL,
                        timestamp TEXT NOT NULL,
                        text_version TEXT NOT NULL
                    )
                    """
CASE_OWNERS = """
                    CREATE TABLE IF NOT EXISTS case_owners (
                        case_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL
                    )
                    """
USER_MODULE_CHOICES = """
                    CREATE TABLE IF NOT EXISTS user_module_choices (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        modulo TEXT NOT NULL,
                        activo INTEGER NOT NULL,
                        timestamp TEXT NOT NULL
                    )
                    """

PROPIETARIO = Propietario(
    nombre="auth",
    paquete="hacienda_gpt.auth",
    descripcion="Cuentas, sesiones (hash del token), consentimientos, dueño de cada caso y módulos elegidos.",
    tablas=("users", "auth_sessions", "user_consents", "case_owners", "user_module_choices"),
    otros_objetos=(
        "idx_auth_sessions_user",
        "idx_user_consents_user",
        "idx_case_owners_user",
        "idx_user_module_choices_user",
    ),
    migraciones=(
        Migracion(
            1,
            "esquema_inicial",
            (
                USERS,
                AUTH_SESSIONS,
                "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)",
                USER_CONSENTS,
                "CREATE INDEX IF NOT EXISTS idx_user_consents_user ON user_consents(user_id, type, seq)",
                CASE_OWNERS,
                "CREATE INDEX IF NOT EXISTS idx_case_owners_user ON case_owners(user_id)",
                USER_MODULE_CHOICES,
                "CREATE INDEX IF NOT EXISTS idx_user_module_choices_user ON user_module_choices(user_id, modulo, seq)",
            ),
        ),
    ),
)
