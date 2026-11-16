"""The bus as ``ops doctor`` sees it: read-only, aggregates only (never a case id)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Any

from hacienda_gpt import settings
from hacienda_gpt.eventos.bus import salud_de


def estado(db_path: str | None = None, *, ahora: datetime | None = None) -> dict[str, Any]:
    """Backlog, dead letters, timers and workers of the bus in ``db_path`` (the cases' database)."""
    ruta = Path(db_path or settings.DECISION_STATE_DB_PATH)
    if not ruta.exists():
        return {"instalado": False}
    conn = sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        return salud_de(conn, ahora=ahora or datetime.now(UTC))
    finally:
        conn.close()


def problemas(db_path: str | None = None, *, ahora: datetime | None = None) -> list[str]:
    """What an operator must look at (Spanish, one line each). Empty when the bus is idle or healthy."""
    try:
        salud = estado(db_path, ahora=ahora)
    except sqlite3.Error as exc:
        return [f"Bus de eventos: no se puede leer su estado ({exc})."]
    if not salud.get("instalado"):
        return []
    out: list[str] = []
    if salud["muertas"]:
        out.append(
            f"Bus de eventos: {salud['muertas']} entrega(s) agotaron sus reintentos (cola de errores). "
            "Revísalas con «python -m hacienda_gpt.cli.eventos estado» y reintenta con «reintentar»."
        )
    try:
        maximo = float(getattr(settings, "EVENTOS_ATRASO_MAX_MINUTOS", 30)) * 60
    except (TypeError, ValueError):
        maximo = 1800.0
    if maximo > 0 and salud["atraso_s"] > maximo:
        out.append(
            f"Bus de eventos: hay trabajo esperando desde hace {round(salud['atraso_s'] / 60)} min "
            f"({salud['pendientes']} entrega(s) pendiente(s); sin repartir: {salud['sin_repartir']}). "
            "¿Está en marcha el trabajador («python -m hacienda_gpt.cli.eventos worker») o la API?"
        )
    if salud["consumidores_con_error"]:
        out.append(
            "Bus de eventos: consumidores reintentando tras un error: " + ", ".join(salud["consumidores_con_error"])
        )
    return out


def para_el_doctor(db_path: str | None = None) -> dict[str, Any]:
    """``{"eventos": <health>}`` for the doctor's report, or ``{}`` where the bus was never used."""
    try:
        salud = estado(db_path)
    except sqlite3.Error:
        return {}
    return {"eventos": salud} if salud.get("instalado") else {}


__all__ = ["estado", "para_el_doctor", "problemas"]
