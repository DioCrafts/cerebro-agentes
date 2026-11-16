"""Versioned prompts and the ACTIVE variant (prompts + provider/model) the agent runs with.

A **prompt version** is an id ``<rol>@<version>`` for one of the three prompts a variant can change:

* ``agente`` — the central agent's system prompt (``AGENT_SYSTEM_PROMPT``);
* ``modulos`` — the addendum listing the household specialists (``MODULES_ADDENDUM``, a format template);
* ``especialista`` — a module specialist's system prompt (``SPECIALIST_SYSTEM_PROMPT``, a format template).

The **integrated** versions are the texts in :mod:`hacienda_gpt.agent.prompts`, pinned by their SHA-256 in
``prompts/registro.json`` (the last entry of each role is the current one). Editing a prompt there without
registering a new version makes ``tests/evaluacion`` fail, so a prompt change cannot slip in unnoticed: it is
registered with the evaluation report that backs it, or with a written reason when it could not be evaluated
(``python -m hacienda_gpt.cli.evaluacion prompts registrar``). Other versions are text files in
``hacienda_gpt/evaluacion/prompts/<rol>@<version>.txt`` (or embedded in a variant's ``textos``).

A **variant** names a version per role plus, optionally, a provider/model/effort. The **active** variant
is read from ``EVALUACION_VARIANTE_ACTIVA_PATH`` (default: ``variante_activa.json`` next to this file),
written only by ``python -m hacienda_gpt.cli.evaluacion promote`` after the candidate beat the baseline.
Without that file — today — the active variant is :data:`VARIANTE_BASE`: the integrated prompts and the
provider/model from settings, i.e. exactly the behaviour before this registry existed. A missing, broken
or unreadable active file never breaks the agent: it falls back to the base variant and logs an error.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import json
import logging
from pathlib import Path
import re
import string
import threading
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hacienda_gpt import settings
from hacienda_gpt.agent import prompts as _prompts

logger = logging.getLogger(__name__)

Rol = Literal["agente", "modulos", "especialista"]
ROLES: tuple[Rol, ...] = get_args(Rol)

PAQUETE = Path(__file__).resolve().parent
PROMPTS_DIR = PAQUETE / "prompts"
ACTIVA_POR_DEFECTO = PAQUETE / "variante_activa.json"
_VERSION_RE = re.compile(r"^(agente|modulos|especialista)@[A-Za-z0-9_.-]{1,64}$")


REGISTRO_PROMPTS = PROMPTS_DIR / "registro.json"
_ATRIBUTOS: dict[Rol, str] = {
    "agente": "AGENT_SYSTEM_PROMPT",
    "modulos": "MODULES_ADDENDUM",
    "especialista": "SPECIALIST_SYSTEM_PROMPT",
}


@dataclass(frozen=True)
class PromptIntegrado:
    version: str
    atributo: str  # name in hacienda_gpt.agent.prompts
    huella: str  # SHA-256 of the text the version id stands for


class VersionRegistrada(BaseModel):
    """One registered version of an integrated prompt (``prompts/registro.json``)."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(pattern=_VERSION_RE.pattern)
    huella: str = Field(pattern=r"^[0-9a-f]{64}$")
    registrado_el: date
    # The evaluation that justified it (variant, suite, pass rates), or None.
    evaluacion: dict[str, Any] | None = None
    nota: str = ""


def cargar_registro(ruta: Path | None = None) -> dict[Rol, list[VersionRegistrada]]:
    datos = json.loads((ruta or REGISTRO_PROMPTS).read_text(encoding="utf-8"))
    return {rol: [VersionRegistrada.model_validate(v) for v in datos[rol]] for rol in ROLES}


def _integrados(registro: dict[Rol, list[VersionRegistrada]]) -> dict[Rol, PromptIntegrado]:
    return {rol: PromptIntegrado(registro[rol][-1].version, _ATRIBUTOS[rol], registro[rol][-1].huella) for rol in ROLES}


# The current integrated version of each role: the last one registered.
INTEGRADOS: dict[Rol, PromptIntegrado] = _integrados(cargar_registro())


def huella(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def texto_integrado(rol: Rol) -> str:
    texto: str = getattr(_prompts, _ATRIBUTOS[rol])
    return texto


def comprobar_integrados() -> list[str]:
    """Integrated prompts whose text no longer matches its registered version (empty = all good)."""
    problemas: list[str] = []
    for rol, integrado in INTEGRADOS.items():
        actual = huella(texto_integrado(rol))
        if actual != integrado.huella:
            problemas.append(
                f"{integrado.atributo} (hacienda_gpt/agent/prompts.py) ha cambiado y sigue registrado como "
                f"«{integrado.version}». Un cambio de prompt es una versión nueva: evalúalo (`python -m "
                "hacienda_gpt.cli.evaluacion promote` con una variante que lleve el texto nuevo) y regístralo con "
                f"`python -m hacienda_gpt.cli.evaluacion prompts registrar --rol {rol} --informe <informe>` "
                '(o, si no se ha podido evaluar, con --sin-evaluar "<motivo>", que queda escrito en el registro).'
            )
    return problemas


def registrar_version(
    rol: Rol, *, nota: str, evaluacion: dict[str, Any] | None = None, ruta: Path | None = None
) -> VersionRegistrada:
    """Pin the current text of ``rol`` in prompts.py as a new version. Raises ``ValueError`` if unchanged."""
    destino = ruta or REGISTRO_PROMPTS
    datos = json.loads(destino.read_text(encoding="utf-8"))
    historial = [VersionRegistrada.model_validate(v) for v in datos[rol]]
    actual = huella(texto_integrado(rol))
    if historial and historial[-1].huella == actual:
        raise ValueError(f"El prompt «{rol}» no ha cambiado desde {historial[-1].version}.")
    numeros = [int(n) for v in historial if (n := v.version.split("@", 1)[1]).isdigit()]
    nueva = VersionRegistrada(
        version=f"{rol}@{max(numeros, default=0) + 1}",
        huella=actual,
        registrado_el=datetime.now(UTC).date(),
        evaluacion=evaluacion,
        nota=nota,
    )
    datos[rol] = [*datos[rol], nueva.model_dump(mode="json", exclude_none=True)]
    destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if destino == REGISTRO_PROMPTS:
        INTEGRADOS[rol] = PromptIntegrado(nueva.version, _ATRIBUTOS[rol], nueva.huella)
    return nueva


def _campos(plantilla: str) -> set[str]:
    return {campo for _texto, campo, _formato, _conv in string.Formatter().parse(plantilla) if campo is not None}


class VarianteInvalidaError(ValueError):
    """The variant or one of its prompt versions cannot be used. The message is Spanish."""


class Variante(BaseModel):
    """A prompt version per role and, optionally, another provider / model / effort (None = settings)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    descripcion: str = ""
    prompts: dict[Rol, str] = Field(default_factory=lambda: {rol: INTEGRADOS[rol].version for rol in ROLES})
    # Texts of non-integrated versions, embedded so an active variant is self-contained.
    textos: dict[str, str] = Field(default_factory=dict)
    proveedor: str | None = None
    modelo: str | None = None
    esfuerzo: Literal["low", "medium", "high", "xhigh", "max"] | None = None

    @field_validator("prompts")
    @classmethod
    def _versiones(cls, value: dict[Rol, str]) -> dict[Rol, str]:
        completas = {rol: INTEGRADOS[rol].version for rol in ROLES}
        completas.update(value)
        for rol, version in completas.items():
            if not _VERSION_RE.fullmatch(version) or not version.startswith(f"{rol}@"):
                raise ValueError(f"versión de prompt no válida para «{rol}»: «{version}» (se espera {rol}@<versión>)")
        return completas

    @model_validator(mode="after")
    def _textos(self) -> Variante:
        for version in self.textos:
            if not _VERSION_RE.fullmatch(version):
                raise ValueError(f"versión de prompt no válida en «textos»: «{version}»")
        return self

    def es_base(self) -> bool:
        return (
            all(self.prompts[rol] == INTEGRADOS[rol].version for rol in ROLES)
            and self.proveedor is None
            and self.modelo is None
            and self.esfuerzo is None
        )


VARIANTE_BASE = Variante(
    id="base",
    descripcion=(
        "Prompts de hacienda_gpt/agent/prompts.py y el proveedor y modelo de la configuración "
        "(LLM_PROVIDER, ANTHROPIC_MODEL…): el comportamiento de siempre."
    ),
)


def texto_prompt(variante: Variante, rol: Rol, *, prompts_dir: Path | None = None) -> str:
    """The text a variant uses for ``rol``. Raises :class:`VarianteInvalidaError`."""
    version = variante.prompts.get(rol, INTEGRADOS[rol].version)
    if version == INTEGRADOS[rol].version:
        return texto_integrado(rol)
    texto = variante.textos.get(version)
    if texto is None:
        if any(v.version == version for v in cargar_registro()[rol]):
            raise VarianteInvalidaError(
                f"«{version}» es una versión integrada anterior: su texto ya no está en prompts.py (está en el "
                "historial de git). Ponlo en un fichero de prompts o en «textos» para usarlo."
            )
        if not _VERSION_RE.fullmatch(version):
            raise VarianteInvalidaError(f"Versión de prompt no válida: «{version}».")
        ruta = (prompts_dir or PROMPTS_DIR) / f"{version}.txt"
        try:
            texto = ruta.read_text(encoding="utf-8")
        except OSError as exc:
            raise VarianteInvalidaError(f"No encuentro el texto del prompt «{version}» ({ruta}).") from exc
    if not texto.strip():
        raise VarianteInvalidaError(f"El prompt «{version}» está vacío.")
    esperados = _campos(texto_integrado(rol))
    try:
        encontrados = _campos(texto)
    except ValueError as exc:
        raise VarianteInvalidaError(f"El prompt «{version}» no es una plantilla válida: {exc}.") from exc
    if encontrados != esperados:
        raise VarianteInvalidaError(
            f"El prompt «{version}» debe usar exactamente los campos {sorted(esperados) or 'ninguno'} "
            f"(usa {sorted(encontrados) or 'ninguno'})."
        )
    return texto


def autocontenida(variante: Variante, *, prompts_dir: Path | None = None) -> Variante:
    """The variant with the text of every non-integrated version embedded (validated)."""
    textos: dict[str, str] = {}
    for rol in ROLES:
        version = variante.prompts[rol]
        if version != INTEGRADOS[rol].version:
            textos[version] = texto_prompt(variante, rol, prompts_dir=prompts_dir)
    return variante.model_copy(update={"textos": textos})


def cargar_variante(ruta: Path, *, prompts_dir: Path | None = None) -> Variante:
    """A variant from a JSON file; prompt files are looked up next to it (``prompts/``) and in the package."""
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        variante = Variante.model_validate(datos)
    except (OSError, ValueError) as exc:
        raise VarianteInvalidaError(f"No se puede leer la variante {ruta}: {exc}") from exc
    locales = prompts_dir or (ruta.parent / "prompts")
    textos = dict(variante.textos)
    for rol in ROLES:
        version = variante.prompts[rol]
        if version == INTEGRADOS[rol].version or version in textos:
            continue
        local = locales / f"{version}.txt"
        if local.is_file():
            textos[version] = local.read_text(encoding="utf-8")
    return autocontenida(variante.model_copy(update={"textos": textos}))


# --------------------------------------------------------------------------- #
# The active variant
# --------------------------------------------------------------------------- #


class VarianteActiva(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variante: Variante
    promovida_el: datetime | None = None
    # The variant it replaced and the evaluation that justified it.
    frente_a: str | None = None
    resumen: dict[str, Any] | None = None


def ruta_activa() -> Path:
    configurada = str(getattr(settings, "EVALUACION_VARIANTE_ACTIVA_PATH", "") or "").strip()
    return Path(configurada) if configurada else ACTIVA_POR_DEFECTO


_cache_lock = threading.Lock()
_cache: dict[str, tuple[int, Variante]] = {}


def variante_activa() -> Variante:
    """The variant the agent runs with. Never raises: any problem falls back to :data:`VARIANTE_BASE`."""
    ruta = ruta_activa()
    try:
        marca = ruta.stat().st_mtime_ns
    except OSError:
        return VARIANTE_BASE
    clave = str(ruta)
    with _cache_lock:
        guardada = _cache.get(clave)
        if guardada is not None and guardada[0] == marca:
            return guardada[1]
    try:
        activa = VarianteActiva.model_validate_json(ruta.read_text(encoding="utf-8")).variante
        for rol in ROLES:
            texto_prompt(activa, rol)
    except Exception:
        logger.exception("Variante activa ilegible en %s: se usa la base (prompts de agent/prompts.py).", ruta)
        activa = VARIANTE_BASE
    with _cache_lock:
        _cache[clave] = (marca, activa)
    return activa


def escribir_activa(
    variante: Variante, *, frente_a: str | None, resumen: dict[str, Any] | None, ruta: Path | None = None
) -> Path:
    """Write the active variant (self-contained). Only :mod:`hacienda_gpt.evaluacion.gate` calls this."""
    destino = ruta or ruta_activa()
    activa = VarianteActiva(
        variante=autocontenida(variante),
        promovida_el=datetime.now(UTC),
        frente_a=frente_a,
        resumen=resumen,
    )
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_suffix(destino.suffix + ".tmp")
    temporal.write_text(activa.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporal.replace(destino)
    with _cache_lock:
        _cache.pop(str(destino), None)
    return destino


# --------------------------------------------------------------------------- #
# What the agent reads
# --------------------------------------------------------------------------- #

_EN_USO: ContextVar[Variante | None] = ContextVar("evaluacion_variante_en_uso", default=None)


@contextmanager
def usar_variante(variante: Variante) -> Iterator[Variante]:
    """Run the block with ``variante`` instead of the active one (the evaluation runner uses this)."""
    token = _EN_USO.set(variante)
    try:
        yield variante
    finally:
        _EN_USO.reset(token)


def variante_en_uso() -> Variante:
    return _EN_USO.get() or variante_activa()


def prompt_activo(rol: Rol) -> str:
    """The prompt text for ``rol`` in the variant in use; the integrated text if anything goes wrong."""
    try:
        return texto_prompt(variante_en_uso(), rol)
    except Exception:
        logger.exception("No se pudo leer el prompt «%s» de la variante en uso: se usa el integrado.", rol)
        return texto_integrado(rol)


def argumentos_proveedor(variante: Variante | None = None) -> dict[str, str]:
    """Keyword arguments for ``create_provider`` (empty for the base variant: settings decide)."""
    actual = variante or variante_activa()
    argumentos: dict[str, str] = {}
    if actual.proveedor:
        argumentos["name"] = actual.proveedor
    if actual.modelo:
        argumentos["model"] = actual.modelo
    if actual.esfuerzo:
        argumentos["effort"] = actual.esfuerzo
    return argumentos


__all__ = [
    "INTEGRADOS",
    "REGISTRO_PROMPTS",
    "VersionRegistrada",
    "cargar_registro",
    "registrar_version",
    "ROLES",
    "VARIANTE_BASE",
    "Rol",
    "Variante",
    "VarianteActiva",
    "VarianteInvalidaError",
    "argumentos_proveedor",
    "autocontenida",
    "cargar_variante",
    "comprobar_integrados",
    "escribir_activa",
    "huella",
    "prompt_activo",
    "ruta_activa",
    "texto_integrado",
    "texto_prompt",
    "usar_variante",
    "variante_activa",
    "variante_en_uso",
]
