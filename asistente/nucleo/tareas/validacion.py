"""The deterministic gate every plan passes before it can exist: a template, or one a model proposed.

A model may PROPOSE a plan (the central agent through ``crear_tarea``, or :mod:`asistente.nucleo.tareas.planificacion`);
code decides whether it is acceptable. Nothing the planner writes is trusted:

* the steps are :class:`~asistente.nucleo.tareas.modelo.PasoSpec` (``extra="forbid"``): only the closed set of step
  types, and no state field (``estado``, ``resultado``…) can be slipped in;
* ``calculo`` names a registered module tool whose effect is ``lectura`` or ``prepara``; ``accion`` one whose
  effect is ``actua`` (it will only ever become a pending action the owner confirms with a button); the
  arguments must validate against the tool's own JSON Schema (an unreadable schema rejects the step);
* ``consulta`` names a registered module with a specialist (not ``fiscal``, whose tools are the advisor's);
* ``capacidad`` names a known capability and one of its operations, with parameters valid for it (research
  only under the official site roots);
* ``espera`` waits for a case event of the allowed list, a known tax-calendar deadline, or a date the user
  gave in an earlier step this one depends on;
* text the user will read (titles, descriptions, questions) carries no links, markup or code, so a plan
  cannot smuggle a link or an instruction into the inbox or into a specialist's prompt;
* the steps form a DAG over earlier steps, at most :data:`MAX_PASOS_PLAN` for a proposed plan.

The result is a list of fresh :class:`~asistente.nucleo.tareas.modelo.Paso` (state ``pendiente``). Whatever passes
still becomes a ``propuesta`` that the user must accept before it runs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import re
from typing import Any, Final, Literal

from pydantic import ValidationError

from asistente.nucleo.tareas.capacidades import Capacidad, capacidades_por_defecto, raices_oficiales
from asistente.nucleo.tareas.modelo import MAX_PASOS, Paso, PasoSpec, problemas_del_grafo

MAX_PASOS_PLAN: Final = 15
_ENLACE = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(com|es|net|org|io|info|gob\.es)\b)", re.IGNORECASE)
_MARCADO = re.compile(r"[<>`{}\[\]\\]|\$\{")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class PlanInvalidoError(ValueError):
    """A plan the gate refuses. ``errores`` are Spanish sentences for the planner (or the user)."""

    def __init__(self, errores: Sequence[str]) -> None:
        self.errores = list(errores)
        super().__init__("; ".join(self.errores))


@dataclass
class PlanValidado:
    pasos: list[Paso]
    # Spanish notes for the user (a module that is off: the step will wait or be skipped).
    avisos: list[str] = field(default_factory=list)


def _problemas_de_texto(campo: str, texto: str | None) -> list[str]:
    if not texto:
        return []
    problemas = []
    if _ENLACE.search(texto):
        problemas.append(f"{campo}: no puede llevar enlaces ni direcciones web")
    if _MARCADO.search(texto):
        problemas.append(f"{campo}: no puede llevar marcado, código ni plantillas")
    if _CONTROL.search(texto):
        problemas.append(f"{campo}: lleva caracteres de control")
    return problemas


def fallo_de_esquema(datos: Mapping[str, Any], schema: Any) -> str | None:
    """Why ``datos`` does not match the JSON Schema ``schema`` (strict: an unreadable schema is a failure)."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        return "la herramienta no declara un esquema de argumentos comprobable"
    try:
        Draft202012Validator.check_schema(dict(schema))
    except SchemaError:
        return "la herramienta no declara un esquema de argumentos comprobable"
    error = next(iter(Draft202012Validator(dict(schema)).iter_errors(dict(datos))), None)
    if error is None:
        return None
    ruta = "/".join(str(p) for p in error.absolute_path) or "(raíz)"
    return f"{ruta}: {error.message[:200]}"


def _ids_calendario() -> frozenset[str]:
    from asistente.ambitos.fiscal.calculo.calendario import _load

    return frozenset(o.id for o in _load().obligaciones)


def _registro(reg: Any) -> Any:
    if reg is not None:
        return reg
    from asistente.nucleo.modulos.registro import registro

    return registro()


def validar_plan(
    pasos_crudos: Sequence[Any],
    *,
    reg: Any = None,
    capacidades: Mapping[str, Capacidad] | None = None,
    modulos_activos: Sequence[str] | None = None,
    origen: Literal["plan", "plantilla"] = "plan",
) -> PlanValidado:
    """Check a plan and return its fresh steps; raise :class:`PlanInvalidoError` with every problem found.

    ``modulos_activos`` (module keys on for the user) only adds notes: a step of a module that is off is
    allowed (it waits for the user to switch it on, or is skipped when optional).
    """
    reg = _registro(reg)
    capacidades = capacidades if capacidades is not None else capacidades_por_defecto()
    limite = MAX_PASOS_PLAN if origen == "plan" else MAX_PASOS
    if not isinstance(pasos_crudos, Sequence) or isinstance(pasos_crudos, str | bytes):
        raise PlanInvalidoError(["el plan debe ser una lista de pasos"])
    if not pasos_crudos:
        raise PlanInvalidoError(["el plan no tiene pasos"])
    if len(pasos_crudos) > limite:
        raise PlanInvalidoError([f"el plan tiene {len(pasos_crudos)} pasos; como mucho {limite}"])

    errores: list[str] = []
    avisos: list[str] = []
    specs: list[PasoSpec] = []
    for indice, crudo in enumerate(pasos_crudos, start=1):
        try:
            spec = PasoSpec.model_validate(crudo)
        except ValidationError as exc:
            detalle = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'paso'}: {e['msg']}" for e in exc.errors()[:4])
            errores.append(f"paso {indice}: {detalle}")
            continue
        specs.append(spec)
    if errores:
        raise PlanInvalidoError(errores)

    errores.extend(problemas_del_grafo(specs))
    por_id = {s.id: s for s in specs}
    activos = set(modulos_activos) if modulos_activos is not None else None
    calendario: frozenset[str] | None = None

    for spec in specs:
        donde = f"paso {spec.id!r}"
        for campo in ("titulo", "descripcion", "pregunta"):
            errores.extend(_problemas_de_texto(f"{donde}, {campo}", getattr(spec, campo)))
        if spec.tipo in ("calculo", "accion"):
            encontrada = reg.herramienta(spec.herramienta or "")
            if encontrada is None:
                errores.append(f"{donde}: la herramienta {spec.herramienta!r} no existe")
                continue
            modulo, herramienta = encontrada
            if spec.tipo == "calculo" and herramienta.efecto == "actua":
                errores.append(
                    f"{donde}: {spec.herramienta} actúa fuera de la aplicación; solo puede ir en un paso «accion», "
                    "que espera la confirmación del usuario"
                )
            if spec.tipo == "accion" and herramienta.efecto != "actua":
                errores.append(f"{donde}: {spec.herramienta} no es una acción; usa un paso «calculo»")
            fallo = fallo_de_esquema(spec.argumentos, herramienta.spec.input_schema)
            if fallo is not None:
                errores.append(f"{donde}: argumentos no válidos para {spec.herramienta}: {fallo}")
            if activos is not None and modulo.clave not in activos:
                avisos.append(
                    f"«{spec.titulo}» usa el módulo {modulo.nombre}, que no tienes activado: "
                    + ("se saltará si sigue apagado." if spec.opcional else "esperará a que lo actives.")
                )
        elif spec.tipo == "consulta":
            modulo = reg.get(spec.modulo or "")
            if modulo is None or spec.modulo == "fiscal":
                errores.append(f"{donde}: no hay especialista del módulo {spec.modulo!r}")
            elif activos is not None and modulo.clave not in activos:
                avisos.append(f"«{spec.titulo}» consulta al especialista de {modulo.nombre}, que no tienes activado.")
        elif spec.tipo == "capacidad":
            capacidad = capacidades.get(spec.capacidad or "")
            operacion = capacidad.operaciones.get(spec.operacion or "") if capacidad is not None else None
            if capacidad is None or operacion is None:
                errores.append(f"{donde}: la capacidad {spec.capacidad!r} no tiene la operación {spec.operacion!r}")
                continue
            fallo = fallo_de_esquema(spec.parametros, operacion.schema)
            if fallo is not None:
                errores.append(f"{donde}: parámetros no válidos para {spec.capacidad}.{spec.operacion}: {fallo}")
            if spec.capacidad == "investigacion":
                fuera = [r for r in spec.parametros.get("raices", []) if r not in raices_oficiales()]
                if fuera:
                    errores.append(f"{donde}: solo se puede investigar en webs oficiales; no en {fuera}")
            for clave, valor in spec.parametros.items():
                if isinstance(valor, str) and clave not in ("raices",):
                    errores.extend(_problemas_de_texto(f"{donde}, {clave}", valor))
        elif spec.tipo == "espera" and spec.espera is not None:
            espera = spec.espera
            if espera.fecha_de_paso is not None:
                origen_fecha = por_id.get(espera.fecha_de_paso)
                if origen_fecha is None or origen_fecha.tipo != "confirmacion_usuario":
                    errores.append(f"{donde}: fecha_de_paso debe ser un paso «confirmacion_usuario»")
                elif espera.fecha_de_paso not in spec.depende_de:
                    errores.append(f"{donde}: debe depender de {espera.fecha_de_paso!r} para usar su fecha")
            if espera.calendario is not None:
                calendario = calendario if calendario is not None else _ids_calendario()
                if espera.calendario not in calendario:
                    errores.append(f"{donde}: el calendario fiscal no tiene el plazo {espera.calendario!r}")
    if errores:
        raise PlanInvalidoError(errores)
    return PlanValidado(pasos=[Paso.model_validate(s.model_dump()) for s in specs], avisos=avisos)


__all__ = ["MAX_PASOS_PLAN", "PlanInvalidoError", "PlanValidado", "fallo_de_esquema", "validar_plan"]
