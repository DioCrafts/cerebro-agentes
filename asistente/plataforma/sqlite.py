"""One way to open the shared SQLite files: connect, switch to WAL without tripping over a busy file, set pragmas.

``PRAGMA journal_mode=WAL`` on a file that another process is creating at the same moment can fail with
"database is locked" straight away: SQLite does not apply the busy timeout to that change. :func:`abrir_sqlite`
retries it until the connection's own timeout runs out, so every process sees the same patience.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

_PAUSA_INICIAL = 0.01
_PAUSA_MAXIMA = 0.25


def _activar_wal(conn: sqlite3.Connection, timeout: float) -> None:
    limite = time.monotonic() + timeout
    pausa = _PAUSA_INICIAL
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= limite:
                raise
        time.sleep(pausa)
        pausa = min(pausa * 2, _PAUSA_MAXIMA)


def abrir_sqlite(
    ruta: str,
    *,
    timeout: float = 30.0,
    filas: bool = False,
    secure_delete: bool = False,
    foreign_keys: bool = False,
    **conectar: Any,
) -> sqlite3.Connection:
    """Open ``ruta`` in WAL mode (except ``:memory:``) and set the requested pragmas.

    ``filas`` sets :class:`sqlite3.Row` as the row factory; any other keyword (``check_same_thread``,
    ``isolation_level``…) goes to :func:`sqlite3.connect` unchanged.
    """
    conn: sqlite3.Connection = sqlite3.connect(ruta, timeout=timeout, **conectar)
    try:
        if filas:
            conn.row_factory = sqlite3.Row
        if ruta != ":memory:":
            _activar_wal(conn, timeout)
        if foreign_keys:
            conn.execute("PRAGMA foreign_keys=ON;")
        if secure_delete:
            conn.execute("PRAGMA secure_delete=ON;")
    except BaseException:
        conn.close()
        raise
    return conn
