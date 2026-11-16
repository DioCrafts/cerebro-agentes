"""Task templates: declarative, versioned plans in ``tareas/plantillas/<id>.json``.

A template is data: its id and version, a title and a description for the user, the memory ámbito a
cancellation with a reason is recorded on, how many days the task has (optional), the JSON Schema of its
parameters and the steps (the same :class:`~hacienda_gpt.tareas.modelo.PasoSpec` a model may propose). A
string value ``"${nombre}"`` in a step's ``argumentos`` or ``parametros`` is replaced by that parameter.
``"x-defecto": "ejercicio_anterior"`` in a parameter's schema makes its default last year.

Every template goes through the same gate as a model's plan (:func:`~hacienda_gpt.tareas.validacion.validar_plan`)
when it is instantiated, and a test checks them all against the real module registry. Changing a template's
steps means a new ``version``: tasks keep the version they were created with.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hacienda_gpt.tareas.capacidades import Capacidad
from hacienda_gpt.tareas.modelo import ParametroValor, Tarea
from hacienda_gpt.tareas.validacion import PlanInvalidoError, fallo_de_esquema, validar_plan

DIRECTORIO = Path(__file__).parent / "plantillas"


class Plantilla(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,59}$")
    version: int = Field(ge=1)
    titulo: str = Field(min_length=1, max_length=120)
    descripcion: str = Field(min_length=1, max_length=600)
    ambito_decision: str | None = None
    vence_dias: int | None = Field(default=None, ge=1, le=3650)
    parametros: dict[str, Any]
    pasos: list[dict[str, Any]] = Field(min_length=1)


@lru_cache(maxsize=1)
def _cargadas() -> dict[str, Plantilla]:
    plantillas: dict[str, Plantilla] = {}
    for ruta in sorted(DIRECTORIO.glob("*.json")):
        plantilla = Plantilla.model_validate(json.loads(ruta.read_text(encoding="utf-8")))
        if plantilla.id != ruta.stem:
            raise ValueError(f"la plantilla {ruta.name} declara el id {plantilla.id!r}")
        plantillas[plantilla.id] = plantilla
    return plantillas


def plantillas() -> list[Plantilla]:
    return list(_cargadas().values())


def plantilla(plantilla_id: str) -> Plantilla | None:
    return _cargadas().get(plantilla_id)


def _defectos(plantilla: Plantilla, hoy: date) -> dict[str, ParametroValor]:
    defectos: dict[str, ParametroValor] = {}
    for nombre, schema in (plantilla.parametros.get("properties") or {}).items():
        if schema.get("x-defecto") == "ejercicio_anterior":
            defectos[nombre] = hoy.year - 1
        elif "default" in schema:
            defectos[nombre] = schema["default"]
    return defectos


def _sustituir(valor: Any, parametros: Mapping[str, ParametroValor]) -> Any:
    if isinstance(valor, str) and valor.startswith("${") and valor.endswith("}"):
        nombre = valor[2:-1]
        if nombre not in parametros:
            raise PlanInvalidoError([f"falta el parámetro {nombre!r} de la plantilla"])
        return parametros[nombre]
    if isinstance(valor, dict):
        return {k: _sustituir(v, parametros) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_sustituir(v, parametros) for v in valor]
    return valor


def parametros_de(plantilla: Plantilla, dados: Mapping[str, Any] | None, *, hoy: date) -> dict[str, ParametroValor]:
    """The template's parameters: defaults plus the given ones, validated against its schema."""
    schema = {k: v for k, v in plantilla.parametros.items()}
    schema["properties"] = {
        nombre: {k: v for k, v in prop.items() if not k.startswith("x-")}
        for nombre, prop in (plantilla.parametros.get("properties") or {}).items()
    }
    valores = {**_defectos(plantilla, hoy), **dict(dados or {})}
    fallo = fallo_de_esquema(valores, schema)
    if fallo is not None:
        raise PlanInvalidoError([f"parámetros no válidos para la plantilla {plantilla.id}: {fallo}"])
    return valores


def instanciar(
    plantilla_id: str,
    *,
    case_id: str,
    user_id: str | None,
    objetivo: str,
    hoy: date,
    parametros: Mapping[str, Any] | None = None,
    reg: Any = None,
    capacidades: Mapping[str, Capacidad] | None = None,
    modulos_activos: Sequence[str] | None = None,
) -> tuple[Tarea, list[str]]:
    """A proposed task (``estado="propuesta"``) from a template, and the gate's notes for the user."""
    elegida = plantilla(plantilla_id)
    if elegida is None:
        raise PlanInvalidoError([f"no hay ninguna plantilla {plantilla_id!r}"])
    valores = parametros_de(elegida, parametros, hoy=hoy)
    pasos = [_sustituir(p, valores) for p in elegida.pasos]
    plan = validar_plan(pasos, reg=reg, capacidades=capacidades, modulos_activos=modulos_activos, origen="plantilla")
    tarea = Tarea(
        case_id=case_id,
        user_id=user_id,
        titulo=elegida.titulo,
        objetivo=objetivo,
        origen="plantilla",
        plantilla=elegida.id,
        plantilla_version=elegida.version,
        parametros=valores,
        pasos=plan.pasos,
        ambito_decision=elegida.ambito_decision,
        vence=hoy + timedelta(days=elegida.vence_dias) if elegida.vence_dias else None,
    )
    return tarea, plan.avisos


def catalogo() -> list[dict[str, Any]]:
    """What the UI and the agent show: id, version, title, description, parameters, and the steps' titles."""
    return [
        {
            "id": p.id,
            "version": p.version,
            "titulo": p.titulo,
            "descripcion": p.descripcion,
            "parametros": p.parametros,
            "pasos": [{"id": s["id"], "titulo": s["titulo"], "tipo": s["tipo"]} for s in p.pasos],
        }
        for p in plantillas()
    ]


__all__ = ["DIRECTORIO", "Plantilla", "catalogo", "instanciar", "parametros_de", "plantilla", "plantillas"]
