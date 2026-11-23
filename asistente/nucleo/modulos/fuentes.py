"""The household modules' sources, for the maintenance loop (like the tax laws in ``tax/fuentes.py``).

Every :class:`FuenteModulo` whose identifier (or URL) names a BOE document joins the BOE watch:

* ``boe-novedades`` treats it as a watched law (a new provision that amends it is detected);
* ``params-check`` asks the BOE when the consolidated text was last updated and lists the module's law for
  re-reading when that date is later than the module's reading. The reading date is the one the module's legal
  data file records (``fuentes_principales[*].consultado_el``, declared to :mod:`asistente.conocimiento`), the
  same field the tax files use: ``params-verify``/``params-autopilot`` move it when every group quoting the law
  still matches, and the weekly pull request of readings carries it, so nothing depends on state kept between
  runs. Only a source no data file records (a module outside the shared library) falls back to the first date
  seen, kept as a baseline in ``MODULOS_FUENTES_STATE_PATH``; a ``consultado_el`` attribute on the source, when a
  module provides one, takes precedence over both.

A source whose URL is the BOE's daily text (``diario_boe/…``, ``tipo_texto: "diario"`` in the module's data) has
no consolidated version: the BOE answers 404 when asked for its update date, and the text it names never changes.
``params-check`` leaves it out (:attr:`FuenteVigilada.consolidada`) instead of reporting an error every run;
``boe-novedades`` still watches it.

Sources outside the BOE (REE, Banco de España…) are listed for the operator; their formats are watched by
each module's own adapter, since the core cannot know how to parse them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import json
import logging
from pathlib import Path
import re
from typing import Any

from asistente.nucleo.modulos.ganchos import fuentes_vigiladas_aparte
from asistente.nucleo.modulos.registro import Registro, registro
from asistente.plataforma import settings

logger = logging.getLogger(__name__)

_BOE_ID = re.compile(r"BOE-[A-Z]-\d{4}-\d+")
_TEXTO_DIARIO = "boe.es/diario_boe/"


@dataclass(frozen=True)
class FuenteVigilada:
    modulo: str
    identificador: str
    descripcion: str
    url: str
    boe_id: str | None
    consultado_el: date | None
    # Where that reading date comes from: "datos" (the module's legal data file), "fuente" (an attribute of the
    # source) or "" (none: the state file's baseline is used).
    origen_lectura: str = ""
    # False when the source is the BOE's daily text (no consolidated version to ask about).
    consolidada: bool = True


def _fecha(valor: Any) -> date | None:
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str):
        try:
            return date.fromisoformat(valor[:10])
        except ValueError:
            return None
    return None


def lecturas_en_datos() -> dict[tuple[str, str], date]:
    """(module, BOE id) -> the earliest reading of the law's CURRENT text recorded in that module's legal data files
    (a source pinned to a version or to the daily BOE only counts when nothing else names the law)."""
    try:
        from asistente.conocimiento import lecturas_de_modulos

        lecturas = lecturas_de_modulos()
    except Exception:  # a broken declaration must not hide the BOE watch: the baselines still work
        logger.exception("Could not read the reading dates of the modules' legal data")
        return {}
    out: dict[tuple[str, str], date] = {}
    for clave in {(x.modulo, x.boe_id) for x in lecturas}:
        suyas = [x for x in lecturas if (x.modulo, x.boe_id) == clave]
        fechas = [f for x in suyas if not x.fijada and (f := _fecha(x.consultado_el))] or [
            f for x in suyas if (f := _fecha(x.consultado_el))
        ]
        if fechas:
            out[clave] = min(fechas)
    return out


def fuentes_de_modulos(reg: Registro | None = None) -> list[FuenteVigilada]:
    """Sources of every registered household module, except those a domain declares watched by its own
    maintenance (``Ganchos.fuentes_vigiladas_aparte``: the tax module's laws, in ``tax/fuentes.py``)."""
    out: list[FuenteVigilada] = []
    en_datos = lecturas_en_datos()
    for modulo in (reg or registro()).modulos.values():
        if fuentes_vigiladas_aparte(modulo):
            continue
        for fuente in modulo.fuentes:
            encontrado = _BOE_ID.search(fuente.identificador) or _BOE_ID.search(fuente.url or "")
            boe_id = encontrado.group(0) if encontrado else None
            propia = _fecha(getattr(fuente, "consultado_el", None))
            de_datos = en_datos.get((modulo.clave, boe_id)) if boe_id else None
            out.append(
                FuenteVigilada(
                    modulo=modulo.clave,
                    identificador=fuente.identificador,
                    descripcion=fuente.descripcion,
                    url=fuente.url,
                    boe_id=boe_id,
                    consultado_el=propia or de_datos,
                    origen_lectura="fuente" if propia else "datos" if de_datos else "",
                    consolidada=_TEXTO_DIARIO not in (fuente.url or ""),
                )
            )
    return out


def boe_ids_de_modulos(reg: Registro | None = None) -> frozenset[str]:
    return frozenset(f.boe_id for f in fuentes_de_modulos(reg) if f.boe_id)


def leer_estado(path: str | Path | None = None) -> dict[str, Any]:
    ruta = Path(path or settings.MODULOS_FUENTES_STATE_PATH)
    if not ruta.exists():
        return {"fuentes": {}}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {"fuentes": {}}
    return datos if isinstance(datos, dict) and isinstance(datos.get("fuentes"), dict) else {"fuentes": {}}


def guardar_estado(estado: Mapping[str, Any], path: str | Path | None = None) -> None:
    ruta = Path(path or settings.MODULOS_FUENTES_STATE_PATH)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(ruta)


def revisar(
    fechas_boe: Mapping[str, date],
    estado: Mapping[str, Any],
    *,
    fuentes: list[FuenteVigilada],
    marcar_revisadas: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(module laws to re-read, new state).

    A law whose reading date the module's data records is compared with it, like the tax laws. Otherwise a law seen
    for the first time sets its baseline and is not listed. With ``marcar_revisadas`` the operator says the modules
    were re-read: every baseline moves to the BOE's date (a date recorded in a data file only moves when the file
    records a new reading).
    """
    lineas: dict[str, Any] = dict(estado.get("fuentes", {}))
    pendientes: list[dict[str, Any]] = []
    for fuente in fuentes:
        if fuente.boe_id is None or fuente.boe_id not in fechas_boe:
            continue
        fecha = fechas_boe[fuente.boe_id]
        if fuente.consultado_el is not None:
            referencia = fuente.consultado_el
        else:
            registrada = _fecha((lineas.get(fuente.boe_id) or {}).get("linea_base"))
            if registrada is None or marcar_revisadas:
                lineas[fuente.boe_id] = {"linea_base": fecha.isoformat()}
                registrada = fecha
            referencia = registrada
        if fecha > referencia:
            pendiente: dict[str, Any] = {
                "modulo": fuente.modulo,
                "boe_id": fuente.boe_id,
                "norma": fuente.descripcion,
                "leida_el": referencia.isoformat(),
                "actualizada_en_boe_el": fecha.isoformat(),
            }
            if fuente.origen_lectura == "datos":
                pendiente["lectura_en"] = "datos del módulo (consultado_el)"
            pendientes.append(pendiente)
    return pendientes, {**dict(estado), "fuentes": lineas}


__all__ = [
    "FuenteVigilada",
    "boe_ids_de_modulos",
    "fuentes_de_modulos",
    "guardar_estado",
    "lecturas_en_datos",
    "leer_estado",
    "revisar",
]
