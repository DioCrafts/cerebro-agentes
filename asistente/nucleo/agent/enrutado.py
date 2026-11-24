"""Which model serves each PURPOSE of a model call (routing by tier), and escalation.

Every model call of the app is made through a metering wrapper with a purpose
(:func:`asistente.plataforma.ops.consumo.medir` / ``con_proposito``): ``agente``,
``verificador``, ``especialista:<clave>``, ``documentos``, ``recodificacion``…
This module maps each purpose to a TIER — ``pequeno`` (classify, route,
extract), ``mediano`` (module specialists, researchers, readers) or ``grande``
(reasoning, critic, reviewers) — in a declarative table, and each tier to a
model. The wrapper then asks the provider for that model (``con_modelo``).

Default: no routing setting → every purpose keeps the provider's own model, the
historical behaviour, byte for byte. The documented profile ``recomendado`` (see
``docs/operacion.md``) moves the small and medium tiers to cheaper Claude models.

Precedence for one purpose (first that applies):

1. ``LLM_ROUTE_<PURPOSE>`` (environment) or an entry of ``LLM_ROUTES`` (JSON,
   glob patterns allowed): a tier name or a model id.
2. A purpose-specific setting that already existed (``RECODE_MODEL``,
   ``VERIFIER_MODEL``…): when set, the provider was built with it and is kept.
3. The tier's model: ``LLM_MODEL_SMALL|MEDIUM|LARGE``, else the profile's.
4. The provider's model.

Escalation: an output that fails its validation on one tier may be retried ONCE
on the next tier up (``LLM_ESCALATION``, on by default), only when that tier
resolves to another model — so with no routing setting it never happens.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import fnmatch
import json
import logging
import os
import re
from typing import Any, Literal

from asistente.plataforma import settings

logger = logging.getLogger(__name__)

Nivel = Literal["pequeno", "mediano", "grande"]
NIVELES: tuple[Nivel, ...] = ("pequeno", "mediano", "grande")
_ALIAS_NIVEL: dict[str, Nivel] = {
    "pequeno": "pequeno",
    "pequeño": "pequeno",
    "small": "pequeno",
    "mediano": "mediano",
    "medium": "mediano",
    "grande": "grande",
    "large": "grande",
}
_SETTING_NIVEL: dict[Nivel, str] = {"pequeno": "SMALL", "mediano": "MEDIUM", "grande": "LARGE"}


@dataclass(frozen=True)
class Proposito:
    """One row of the routing table: a purpose (glob) and its tier, with the reason."""

    patron: str
    nivel: Nivel
    por_que: str


# The routing table. First matching row wins; a purpose matching no row is
# "grande" (an unknown purpose gets the strongest model, never a weaker one).
TABLA: tuple[Proposito, ...] = (
    Proposito("agente", "grande", "Asesor central: razona sobre varias fuentes y redacta la respuesta."),
    # The household brain (asistente/nucleo/cerebro). Exact names: "cerebro" must not swallow "cerebro:*".
    Proposito("cerebro", "grande", "Cerebro del hogar: une los informes de los especialistas en una respuesta."),
    Proposito("cerebro:intencion", "pequeno", "Entiende la petición y reparte encargos cuando las reglas dudan."),
    # The household council (asistente/nucleo/consejo): members deliberate, the chair rules, the dossier is prepared.
    Proposito("consejo:miembro:*", "mediano", "Miembro del consejo: propone, critica de forma anónima y revisa."),
    Proposito("consejo:presidente", "grande", "Presidente del consejo: agrega, mide el acuerdo y redacta el dictamen."),
    Proposito("consejo:expediente", "pequeno", "Reformula la pregunta de forma neutral y extrae las opciones."),
    Proposito("verificador", "grande", "Revisa cada afirmación de la respuesta contra sus fuentes."),
    Proposito("equipo:planificador", "pequeno", "Clasifica la consulta (¿compleja?) y reparte encargos."),
    Proposito("equipo:investigador*", "mediano", "Busca normativa o doctrina con herramientas y resume."),
    Proposito("equipo:critico", "grande", "Crítica adversarial del borrador."),
    Proposito("especialista:*", "mediano", "Especialista de un módulo: instrucciones y herramientas acotadas."),
    # Reading figures from a scanned document has no deterministic check against the
    # image: kept one tier above plain extraction on purpose.
    Proposito("documentos", "mediano", "Lee cifras de un documento subido (escaneos incluidos)."),
    Proposito("hechos", "pequeno", "Extrae hechos de texto a un esquema."),
    Proposito("recodificacion", "mediano", "Lector de la recodificación: codifica la ley al esquema."),
    Proposito("recodificacion-revisor", "grande", "Revisor independiente de la recodificación."),
    Proposito("formatos", "mediano", "Diagnóstico de un formato externo que ha cambiado."),
    Proposito("formatos-revisor", "grande", "Reflexión independiente sobre el diagnóstico."),
    Proposito("ayudas*", "pequeno", "Clasificador de convocatorias de ayudas."),
    Proposito("correo:*", "pequeno", "Clasificador del correo: categoría de un correo ya reducido a datos."),
    Proposito("evaluacion-juez", "grande", "Juez de las evaluaciones del agente."),
    # The brain's proactive pass (asistente/nucleo/repaso): it only writes points code ranked, behind a gate.
    Proposito("cerebro:repaso", "pequeno", "Redacta el repaso semanal o diario a partir de puntos ya priorizados."),
    # The research capability (capacidades/investigacion): what it writes passes a literal-quote gate.
    Proposito("investigacion:planificar", "pequeno", "Divide una pregunta de investigación en subpreguntas."),
    Proposito("investigacion:web", "mediano", "Busca y lee páginas web con la herramienta del proveedor."),
    Proposito("investigacion:sintetizar", "mediano", "Redacta afirmaciones con citas literales de las fuentes."),
)
NIVEL_POR_DEFECTO: Nivel = "grande"

# Settings that already chose a model for one purpose. When set (in ``settings`` or,
# for those read straight from the environment, there), the provider of that purpose
# is built with it (cli/proactive.py, cli/formatos.py, agent.py, decision/fact_extractor.py)
# and the routing profile leaves it alone; an explicit LLM_ROUTE_* still wins.
AJUSTES_PREVIOS: dict[str, str] = {
    "verificador": "VERIFIER_MODEL",
    "recodificacion": "RECODE_MODEL",
    "recodificacion-revisor": "RECODE_REVIEWER_MODEL",
    "formatos": "FORMAT_MODEL",
    "formatos-revisor": "FORMAT_REVIEWER_MODEL",
    "hechos": "DECISION_EXTRACTOR_MODEL",
}

# Documented profiles: provider -> tier -> (model or None = keep, effort or None = keep).
# A model of None for "grande" means the configured main model (ANTHROPIC_MODEL).
PERFILES: dict[str, dict[str, dict[Nivel, tuple[str | None, str | None]]]] = {
    "recomendado": {
        "anthropic": {
            "pequeno": ("claude-haiku-5-5", "low"),
            "mediano": ("claude-sonnet-5-5", None),
            "grande": (None, None),
        },
    },
}


@dataclass(frozen=True)
class Ruta:
    """Where one purpose runs. ``modelo`` / ``effort`` None = the provider's own."""

    proposito: str
    nivel: Nivel
    modelo: str | None = None
    effort: str | None = None
    # Which rule decided it (for logs and reports): "por_defecto", "perfil:<p>",
    # "LLM_MODEL_SMALL", "LLM_ROUTE_X", "LLM_ROUTES", "RECODE_MODEL"…
    origen: str = "por_defecto"

    def modelo_efectivo(self, modelo_base: str) -> str:
        return self.modelo or modelo_base


def _proveedor_canonico(proveedor: str) -> str:
    nombre = proveedor.strip().lower()
    return "anthropic" if nombre in ("anthropic", "claude") else nombre


def nivel_de_tabla(proposito: str) -> Nivel:
    """The tier the routing table gives ``proposito``."""
    for fila in TABLA:
        if fnmatch.fnmatchcase(proposito, fila.patron):
            return fila.nivel
    return NIVEL_POR_DEFECTO


def _nombre_variable(proposito: str) -> str:
    return "LLM_ROUTE_" + re.sub(r"[^A-Z0-9]+", "_", proposito.upper()).strip("_")


def _rutas_json() -> dict[str, str]:
    texto = (getattr(settings, "LLM_ROUTES", "") or "").strip()
    if not texto:
        return {}
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM_ROUTES debe ser un objeto JSON propósito → nivel o modelo: {exc}") from exc
    if not isinstance(datos, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in datos.items()):
        raise ValueError("LLM_ROUTES debe ser un objeto JSON de textos: propósito → nivel o modelo.")
    return datos


def _sustitucion(proposito: str) -> tuple[str, str] | None:
    """An explicit per-purpose override: (value, where it came from)."""
    variable = _nombre_variable(proposito)
    valor = os.environ.get(variable, "").strip()
    if valor:
        return valor, variable
    for patron, destino in _rutas_json().items():
        if fnmatch.fnmatchcase(proposito, patron) and destino.strip():
            return destino.strip(), "LLM_ROUTES"
    return None


def perfil_activo() -> str:
    perfil = (getattr(settings, "LLM_ROUTING_PROFILE", "") or "").strip().lower()
    if perfil in ("", "actual", "ninguno"):
        return ""
    if perfil not in PERFILES:
        raise ValueError(
            f"LLM_ROUTING_PROFILE desconocido: {perfil!r} (vacío para el comportamiento de siempre, o "
            f"{', '.join(sorted(PERFILES))})."
        )
    return perfil


def modelo_de_nivel(nivel: Nivel, *, proveedor: str) -> tuple[str | None, str | None, str]:
    """(model, effort, origin) for ``nivel`` on ``proveedor``; None = keep the provider's."""
    sufijo = _SETTING_NIVEL[nivel]
    modelo = (getattr(settings, f"LLM_MODEL_{sufijo}", "") or "").strip() or None
    effort = (getattr(settings, f"LLM_EFFORT_{sufijo}", "") or "").strip() or None
    origen = f"LLM_MODEL_{sufijo}" if modelo else (f"LLM_EFFORT_{sufijo}" if effort else "por_defecto")
    perfil = perfil_activo()
    if perfil:
        del_perfil = PERFILES[perfil].get(_proveedor_canonico(proveedor), {}).get(nivel)
        if del_perfil is not None:
            modelo_perfil, effort_perfil = del_perfil
            if modelo_perfil is None and nivel == "grande" and _proveedor_canonico(proveedor) == "anthropic":
                modelo_perfil = settings.ANTHROPIC_MODEL
            if modelo is None and modelo_perfil is not None:
                modelo, origen = modelo_perfil, f"perfil:{perfil}"
            if effort is None and effort_perfil is not None:
                effort = effort_perfil
                origen = origen if origen != "por_defecto" else f"perfil:{perfil}"
    return modelo, effort, origen


def resolver(proposito: str, *, proveedor: str, nivel: Nivel | None = None) -> Ruta:
    """The route of ``proposito`` for a provider named ``proveedor``.

    ``nivel`` forces the tier (escalation); otherwise it comes from the overrides
    and the table. Raises ValueError on an invalid setting.
    """
    if nivel is None:
        sustitucion = _sustitucion(proposito)
        if sustitucion is not None:
            valor, origen = sustitucion
            nivel_pedido = _ALIAS_NIVEL.get(valor.lower())
            if nivel_pedido is None:
                # A model id pinned for this purpose.
                return Ruta(proposito, nivel_de_tabla(proposito), modelo=valor, origen=origen)
            modelo, effort, _ = modelo_de_nivel(nivel_pedido, proveedor=proveedor)
            return Ruta(proposito, nivel_pedido, modelo=modelo, effort=effort, origen=origen)
        nivel = nivel_de_tabla(proposito)
        previo = AJUSTES_PREVIOS.get(proposito)
        if previo and (getattr(settings, previo, "") or os.environ.get(previo, "") or "").strip():
            return Ruta(proposito, nivel, origen=previo)
    modelo, effort, origen = modelo_de_nivel(nivel, proveedor=proveedor)
    return Ruta(proposito, nivel, modelo=modelo, effort=effort, origen=origen)


def siguiente(ruta: Ruta, *, proveedor: str, modelo_base: str, effort_base: str | None = None) -> Ruta | None:
    """The route one tier up that runs on ANOTHER model (or effort), or None.

    Tiers that resolve to the same model are skipped, so escalation never repeats a
    call on the model that just failed. None when escalation is off or nothing is above.
    """
    if not getattr(settings, "LLM_ESCALATION", True):
        return None
    actual = (ruta.modelo_efectivo(modelo_base), ruta.effort or effort_base)
    for nivel in NIVELES[NIVELES.index(ruta.nivel) + 1 :]:
        candidata = resolver(ruta.proposito, proveedor=proveedor, nivel=nivel)
        if (candidata.modelo_efectivo(modelo_base), candidata.effort or effort_base) != actual:
            return Ruta(
                ruta.proposito,
                nivel,
                modelo=candidata.modelo_efectivo(modelo_base),
                effort=candidata.effort,
                origen=f"escalado:{candidata.origen}",
            )
    return None


_SIN_VARIANTES: set[tuple[str, str]] = set()


def aplicar(provider: Any, ruta: Ruta) -> Any:
    """The provider that serves ``ruta``: ``provider`` itself when nothing changes.

    Providers that cannot switch model (test doubles, third-party providers without
    ``con_modelo``) are kept as they are, with a warning once.
    """
    modelo = ruta.modelo_efectivo(provider.model)
    effort = ruta.effort
    if modelo == provider.model and (effort is None or effort == getattr(provider, "effort", None)):
        return provider
    con_modelo = getattr(provider, "con_modelo", None)
    if not callable(con_modelo):
        clave = (str(getattr(provider, "name", "?")), ruta.proposito)
        if clave not in _SIN_VARIANTES:
            _SIN_VARIANTES.add(clave)
            logger.warning(
                "Provider %s cannot switch model: purpose %s keeps %s instead of %s.",
                clave[0],
                ruta.proposito,
                provider.model,
                modelo,
            )
        return provider
    return con_modelo(modelo, effort=effort)


def para(provider: Any, proposito: str) -> Any:
    """``provider`` re-purposed (and routed) for ``proposito`` when it is metered; as is otherwise."""
    con_proposito = getattr(provider, "con_proposito", None)
    return con_proposito(proposito) if callable(con_proposito) else provider


# --------------------------------------------------------------------------- #
# Validation of a structured output (the deterministic gate escalation uses)
# --------------------------------------------------------------------------- #

Validador = Callable[[dict[str, Any]], str | None]


def fallo_de_esquema(datos: dict[str, Any], schema: dict[str, Any]) -> str | None:
    """Why ``datos`` does not match ``schema`` (JSON Schema), or None.

    None as well when the schema itself cannot be checked here: this gate only
    ever ADDS a retry, it never rejects what the caller would have accepted.
    """
    if not schema:
        return None
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
    except ImportError:  # pragma: no cover - jsonschema ships with the dependencies
        return None
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        return None
    error = next(iter(Draft202012Validator(schema).iter_errors(datos)), None)
    if error is None:
        return None
    ruta = "/".join(str(paso) for paso in error.absolute_path) or "(raíz)"
    return f"{ruta}: {error.message[:200]}"


def fallo_de_salida(datos: dict[str, Any], schema: dict[str, Any], validadores: Sequence[Validador]) -> str | None:
    """The first failure among the schema and the caller's own deterministic checks."""
    fallo = fallo_de_esquema(datos, schema)
    if fallo is not None:
        return f"esquema: {fallo}"
    for validar in validadores:
        fallo = validar(datos)
        if fallo:
            return fallo
    return None


def describir(proveedor: str, modelo_base: str, propositos: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """The effective routing table (for logs, docs and ``ops``): one row per purpose."""
    filas: list[dict[str, Any]] = []
    for proposito in propositos or [fila.patron for fila in TABLA]:
        ruta = resolver(proposito, proveedor=proveedor)
        filas.append(
            {
                "proposito": proposito,
                "nivel": ruta.nivel,
                "modelo": ruta.modelo_efectivo(modelo_base),
                "effort": ruta.effort,
                "origen": ruta.origen,
            }
        )
    return filas


__all__ = [
    "AJUSTES_PREVIOS",
    "NIVELES",
    "PERFILES",
    "TABLA",
    "Nivel",
    "Proposito",
    "Ruta",
    "Validador",
    "aplicar",
    "describir",
    "fallo_de_esquema",
    "fallo_de_salida",
    "modelo_de_nivel",
    "nivel_de_tabla",
    "para",
    "perfil_activo",
    "resolver",
    "siguiente",
]
