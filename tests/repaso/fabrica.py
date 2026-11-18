"""Builders of signals and proposals for the pass's tests."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from asistente.nucleo.repaso.senales import Fuente, Hechos, Propuesta, Senal
from .conftest import HOY


def EN(*, days: int) -> date:  # noqa: N802 - reads like the tables it fills («EN(days=3)»)
    return HOY + timedelta(days=days)


def senal(id_: str = "aviso:x", **kw: Any) -> Senal:
    hechos = dict(kw.pop("hechos", {}))
    if "vence" in hechos and "dias" not in hechos:
        hechos["dias"] = (hechos["vence"] - HOY).days
    datos: dict[str, Any] = {
        "id": id_,
        "ambito": "luz",
        "tipo": "aviso",
        "titulo": id_,
        "fuente": Fuente(tipo="aviso", id=id_, modulo=kw.get("ambito", "luz"), clave=id_.removeprefix("aviso:")),
        "hechos": Hechos(**hechos),
    }
    datos.update(kw)
    return Senal(**datos)


def propuesta(id_: str = "accion:acc_1", ambito: str | None = None, resumen: str = "Hacer algo.") -> Propuesta:
    return Propuesta(
        id=id_, tipo="accion", ref=id_.split(":", 1)[1], etiqueta="Confirmar", resumen=resumen, ambito=ambito
    )
