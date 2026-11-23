"""Versioned prompts and the ACTIVE variant (prompts + provider/model) the agent runs with.

A **prompt version** is an id ``<rol>@<version>`` for one of the prompts a variant can change:

* ``agente`` — the central agent's system prompt (``AGENT_SYSTEM_PROMPT``);
* ``modulos`` — the addendum listing the household specialists (``MODULES_ADDENDUM``, a format template);
* ``especialista`` — a module specialist's system prompt (``SPECIALIST_SYSTEM_PROMPT``, a format template);
* ``cerebro``, ``cerebro_intencion``, ``cerebro_sintesis`` — the household brain's prompts
  (``asistente.nucleo.cerebro.prompts``: ``CEREBRO_SYSTEM_PROMPT``, ``INTENCION_SYSTEM_PROMPT``,
  ``SINTESIS_SYSTEM_PROMPT``);
* ``repaso`` — the proactive weekly pass (``asistente.nucleo.repaso.prompts.REPASO_SYSTEM_PROMPT``);
* ``memoria_resumen`` — the conversation summary of the memory (``asistente.nucleo.recuerdos.prompts.
  RESUMEN_SYSTEM_PROMPT``);
* ``consejo_miembro``, ``consejo_critica``, ``consejo_revision``, ``consejo_presidente`` — the household council's
  member, anonymous critique, revision and chair prompts (``asistente.nucleo.consejo.prompts``:
  ``MIEMBRO_SYSTEM_PROMPT``, ``CRITICA_SYSTEM_PROMPT``, ``REVISION_SYSTEM_PROMPT``, ``PRESIDENTE_SYSTEM_PROMPT``);
* ``consejo_expediente`` — the council's dossier builder (``asistente.nucleo.consejo.prompts_expediente.
  EXPEDIENTE_SYSTEM_PROMPT``).

The first three live in :mod:`asistente.nucleo.agent.prompts` (imported here, as always). The others live in
packages that may not be installed or may not exist yet: they are imported LAZILY, the first time their text is
needed, so this module stays dependency-light and free of import cycles. A role whose module or attribute is
missing is **not available**: :func:`texto_integrado` raises :class:`RolNoDisponibleError` (a clear Spanish
message) and :func:`prompt_activo` lets it through for the caller to catch — the caller is the very package
that holds the text, so in practice it only happens in tests and half-installed deployments.

The **integrated** versions are the texts in those modules, pinned by their SHA-256 in ``prompts/registro.json``
(the last entry of each role is the current one). Editing a prompt without registering a new version makes
``tests/evaluacion`` fail, so a prompt change cannot slip in unnoticed: it is registered with the evaluation
report that backs it, or with a written reason when it could not be evaluated
(``python -m asistente.plataforma.cli.evaluacion prompts registrar``). A role that is available but has never
been registered is reported the same way (``prompts sincronizar`` registers it). Other versions are text files in
``asistente/nucleo/evaluacion/prompts/<rol>@<version>.txt`` (or embedded in a variant's ``textos``). The id
``<rol>@integrado`` always means «the text in the code, whatever its registered version».

A **variant** names a version per role (any role) plus, optionally, a provider/model/effort. The **active**
variant is read from ``EVALUACION_VARIANTE_ACTIVA_PATH`` (default: ``variante_activa.json`` next to this file),
written only by ``python -m asistente.plataforma.cli.evaluacion promote`` after the candidate passed the gate.
Without that file the active variant is :data:`VARIANTE_BASE`: the integrated prompts and the provider/model
from settings, i.e. exactly the behaviour before this registry existed. A missing, broken or unreadable active
file never breaks the agent: it falls back to the base variant and logs an error.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import importlib
import json
import logging
from pathlib import Path
import re
import string
import threading
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from asistente.nucleo.agent import prompts as _prompts
from asistente.plataforma import settings

logger = logging.getLogger(__name__)

Rol = Literal[
    "agente",
    "modulos",
    "especialista",
    "cerebro",
    "cerebro_intencion",
    "cerebro_sintesis",
    "repaso",
    "memoria_resumen",
    "consejo_miembro",
    "consejo_critica",
    "consejo_revision",
    "consejo_presidente",
    "consejo_expediente",
]
ROLES: tuple[Rol, ...] = get_args(Rol)

PAQUETE = Path(__file__).resolve().parent
PROMPTS_DIR = PAQUETE / "prompts"
ACTIVA_POR_DEFECTO = PAQUETE / "variante_activa.json"
# Where ``promote`` wrote it before the package moved (``hacienda_gpt/evaluacion``): an installation that promoted a
# variant keeps it after updating (the file is not in the repository, so the move did not take it along).
ACTIVA_HISTORICA = PAQUETE.parents[2] / "hacienda_gpt" / "evaluacion" / "variante_activa.json"
_VERSION_RE = re.compile(r"^(" + "|".join(ROLES) + r")@[A-Za-z0-9_.-]{1,64}$")
# The version id that always stands for the text in the code (used for roles not registered yet).
SIN_REGISTRAR = "integrado"


REGISTRO_PROMPTS = PROMPTS_DIR / "registro.json"
# The roles whose text lives in asistente/nucleo/agent/prompts.py (imported above, as it always was).
_ATRIBUTOS: dict[Rol, str] = {
    "agente": "AGENT_SYSTEM_PROMPT",
    "modulos": "MODULES_ADDENDUM",
    "especialista": "SPECIALIST_SYSTEM_PROMPT",
}
# The roles whose text lives in another package, imported lazily: (module, attribute).
ROLES_EXTERNOS: dict[Rol, tuple[str, str]] = {
    "cerebro": ("asistente.nucleo.cerebro.prompts", "CEREBRO_SYSTEM_PROMPT"),
    "cerebro_intencion": ("asistente.nucleo.cerebro.prompts", "INTENCION_SYSTEM_PROMPT"),
    "cerebro_sintesis": ("asistente.nucleo.cerebro.prompts", "SINTESIS_SYSTEM_PROMPT"),
    "repaso": ("asistente.nucleo.repaso.prompts", "REPASO_SYSTEM_PROMPT"),
    "memoria_resumen": ("asistente.nucleo.recuerdos.prompts", "RESUMEN_SYSTEM_PROMPT"),
    # The household council (asistente/nucleo/consejo, docs/consejo/evaluacion.md).
    "consejo_miembro": ("asistente.nucleo.consejo.prompts", "MIEMBRO_SYSTEM_PROMPT"),
    "consejo_critica": ("asistente.nucleo.consejo.prompts", "CRITICA_SYSTEM_PROMPT"),
    "consejo_revision": ("asistente.nucleo.consejo.prompts", "REVISION_SYSTEM_PROMPT"),
    "consejo_presidente": ("asistente.nucleo.consejo.prompts", "PRESIDENTE_SYSTEM_PROMPT"),
    "consejo_expediente": ("asistente.nucleo.consejo.prompts_expediente", "EXPEDIENTE_SYSTEM_PROMPT"),
}


def origen_de(rol: Rol) -> tuple[str, str]:
    """(module, attribute) holding the integrated text of ``rol``."""
    if rol in _ATRIBUTOS:
        return _prompts.__name__, _ATRIBUTOS[rol]
    return ROLES_EXTERNOS[rol]


def _fichero(modulo: str) -> str:
    return modulo.replace(".", "/") + ".py"


class RolNoDisponibleError(LookupError):
    """The integrated text of a role cannot be read (its package is missing or does not define it)."""

    def __init__(self, rol: str, modulo: str, atributo: str, causa: str) -> None:
        self.rol = rol
        super().__init__(
            f"El prompt «{rol}» no está disponible: no se puede leer {modulo}.{atributo} ({causa}). "
            "Ese paquete aún no existe o no define el texto."
        )


@dataclass(frozen=True)
class PromptIntegrado:
    version: str
    atributo: str  # name in the module that holds it (see origen_de)
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
    """Every role's registered versions (an empty list for a role never registered)."""
    datos = json.loads((ruta or REGISTRO_PROMPTS).read_text(encoding="utf-8"))
    return {rol: [VersionRegistrada.model_validate(v) for v in datos.get(rol, [])] for rol in ROLES}


def _integrados(registro: dict[Rol, list[VersionRegistrada]]) -> dict[Rol, PromptIntegrado]:
    return {
        rol: PromptIntegrado(registro[rol][-1].version, origen_de(rol)[1], registro[rol][-1].huella)
        for rol in ROLES
        if registro[rol]
    }


# The current integrated version of each REGISTERED role: the last one registered.
INTEGRADOS: dict[Rol, PromptIntegrado] = _integrados(cargar_registro())


def version_integrada(rol: Rol) -> str:
    """The id of the text in the code: its last registered version, or ``<rol>@integrado`` when unregistered."""
    integrado = INTEGRADOS.get(rol)
    return integrado.version if integrado is not None else f"{rol}@{SIN_REGISTRAR}"


def es_integrada(rol: Rol, version: str) -> bool:
    """True when ``version`` stands for the text in the code (the current id, or the ``@integrado`` alias)."""
    return version in (version_integrada(rol), f"{rol}@{SIN_REGISTRAR}")


def huella(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def texto_integrado(rol: Rol) -> str:
    """The text in the code for ``rol``. Raises :class:`RolNoDisponibleError` for a role that is not available."""
    if rol in _ATRIBUTOS:
        texto: str = getattr(_prompts, _ATRIBUTOS[rol])
        return texto
    modulo, atributo = ROLES_EXTERNOS[rol]
    try:
        paquete = importlib.import_module(modulo)
    except Exception as exc:  # missing package, or one that fails to import: the role is not available here
        raise RolNoDisponibleError(rol, modulo, atributo, f"{type(exc).__name__}: {exc}") from exc
    valor = getattr(paquete, atributo, None)
    if not isinstance(valor, str) or not valor.strip():
        raise RolNoDisponibleError(rol, modulo, atributo, "no está definido o está vacío")
    return valor


def rol_disponible(rol: Rol) -> bool:
    try:
        texto_integrado(rol)
    except RolNoDisponibleError:
        return False
    return True


def roles_disponibles() -> tuple[Rol, ...]:
    return tuple(rol for rol in ROLES if rol_disponible(rol))


def comprobar_integrados() -> list[str]:
    """Integrated prompts whose text no longer matches the registry, or is not registered (empty = all good).

    A role whose package is not available is skipped unless it is registered (then its text vanished: reported).
    """
    problemas: list[str] = []
    for rol in ROLES:
        integrado = INTEGRADOS.get(rol)
        modulo, atributo = origen_de(rol)
        try:
            actual = huella(texto_integrado(rol))
        except RolNoDisponibleError as exc:
            if integrado is not None:
                problemas.append(
                    f"«{rol}» está registrado como «{integrado.version}» pero su texto ya no se lee: {exc}"
                )
            continue
        if integrado is None:
            problemas.append(
                f"{atributo} ({_fichero(modulo)}) es el prompt «{rol}» y no está registrado. Evalúalo y regístralo "
                f"con `python -m asistente.plataforma.cli.evaluacion prompts registrar --rol {rol} --informe "
                '<informe>` (o, si no se ha podido evaluar, `prompts sincronizar --motivo "<motivo>"`).'
            )
            continue
        if actual != integrado.huella:
            problemas.append(
                f"{atributo} ({_fichero(modulo)}) ha cambiado y sigue registrado como "
                f"«{integrado.version}». Un cambio de prompt es una versión nueva: evalúalo (`python -m "
                "asistente.plataforma.cli.evaluacion promote` con una variante que lleve el texto nuevo) y regístralo con "
                f"`python -m asistente.plataforma.cli.evaluacion prompts registrar --rol {rol} --informe <informe>` "
                '(o, si no se ha podido evaluar, `prompts sincronizar --motivo "<motivo>"`, que registra todos los '
                "que han cambiado y deja el motivo escrito en el registro)."
            )
    return problemas


def pendientes_de_registro() -> list[Rol]:
    """Available roles whose text is not the last registered one (changed, or never registered)."""
    pendientes: list[Rol] = []
    for rol in ROLES:
        try:
            actual = huella(texto_integrado(rol))
        except RolNoDisponibleError:
            continue
        integrado = INTEGRADOS.get(rol)
        if integrado is None or integrado.huella != actual:
            pendientes.append(rol)
    return pendientes


def registrar_version(
    rol: Rol, *, nota: str, evaluacion: dict[str, Any] | None = None, ruta: Path | None = None
) -> VersionRegistrada:
    """Pin the current text of ``rol`` as a new version. Raises ``ValueError`` if unchanged.

    Raises :class:`RolNoDisponibleError` when the role's text cannot be read.
    """
    destino = ruta or REGISTRO_PROMPTS
    datos = json.loads(destino.read_text(encoding="utf-8"))
    historial = [VersionRegistrada.model_validate(v) for v in datos.get(rol, [])]
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
    datos[rol] = [*datos.get(rol, []), nueva.model_dump(mode="json", exclude_none=True)]
    destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if destino == REGISTRO_PROMPTS:
        INTEGRADOS[rol] = PromptIntegrado(nueva.version, origen_de(rol)[1], nueva.huella)
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
    prompts: dict[Rol, str] = Field(default_factory=lambda: {rol: version_integrada(rol) for rol in ROLES})
    # Texts of non-integrated versions, embedded so an active variant is self-contained.
    textos: dict[str, str] = Field(default_factory=dict)
    proveedor: str | None = None
    modelo: str | None = None
    esfuerzo: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    # SHA-256 of the text of each role when the variant was evaluated/promoted: the variant references its
    # prompts by CONTENT, so a text that changed afterwards is detected (see :func:`desajustes`).
    huellas: dict[Rol, str] = Field(default_factory=dict)

    @field_validator("prompts")
    @classmethod
    def _versiones(cls, value: dict[Rol, str]) -> dict[Rol, str]:
        completas = {rol: version_integrada(rol) for rol in ROLES}
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
            all(es_integrada(rol, self.prompts[rol]) for rol in ROLES)
            and self.proveedor is None
            and self.modelo is None
            and self.esfuerzo is None
        )

    def roles_cambiados(self) -> list[Rol]:
        """The roles whose prompt is not the integrated one."""
        return [rol for rol in ROLES if not es_integrada(rol, self.prompts[rol])]


VARIANTE_BASE = Variante(
    id="base",
    descripcion=(
        "Prompts integrados en el código (asistente/nucleo/agent/prompts.py y los de cada paquete) y el proveedor y "
        "modelo de la configuración (LLM_PROVIDER, ANTHROPIC_MODEL…): el comportamiento de siempre."
    ),
)


def _campos_integrados(rol: Rol) -> set[str] | None:
    """The format fields of the integrated text, or None when they cannot be known (role unavailable, or the
    integrated text is not a valid format template: then nothing is checked)."""
    try:
        return _campos(texto_integrado(rol))
    except RolNoDisponibleError, ValueError:
        return None


def texto_prompt(variante: Variante, rol: Rol, *, prompts_dir: Path | None = None) -> str:
    """The text a variant uses for ``rol``. Raises :class:`VarianteInvalidaError`, or
    :class:`RolNoDisponibleError` when the variant uses the integrated text of a role that is not available."""
    version = variante.prompts.get(rol, version_integrada(rol))
    esperada = variante.huellas.get(rol)
    if es_integrada(rol, version):
        # The code decides; a drift from the evaluated text is reported by desajustes(), never fatal.
        return texto_integrado(rol)
    texto = variante.textos.get(version)
    if texto is None:
        anterior = any(v.version == version for v in cargar_registro()[rol])
        if anterior and esperada is not None and rol_disponible(rol) and esperada == huella(texto_integrado(rol)):
            return texto_integrado(rol)  # same content, registered again under a newer id
        if anterior:
            raise VarianteInvalidaError(
                f"«{version}» es una versión integrada anterior: su texto ya no está en el código (está en el "
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
    if esperada is not None and huella(texto) != esperada:
        raise VarianteInvalidaError(f"El texto del prompt «{version}» no es el que se evaluó (su huella no coincide).")
    esperados = _campos_integrados(rol)
    if esperados is not None:
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


def huellas_de(variante: Variante, *, prompts_dir: Path | None = None) -> dict[Rol, str]:
    """SHA-256 of the text the variant uses for each role available here (unavailable roles are left out)."""
    huellas: dict[Rol, str] = {}
    for rol in ROLES:
        try:
            huellas[rol] = huella(texto_prompt(variante, rol, prompts_dir=prompts_dir))
        except RolNoDisponibleError:
            continue
    return huellas


def autocontenida(variante: Variante, *, prompts_dir: Path | None = None) -> Variante:
    """The variant with the text of every non-integrated version embedded (validated)."""
    textos: dict[str, str] = {}
    huellas: dict[Rol, str] = {}
    for rol in ROLES:
        version = variante.prompts[rol]
        try:
            texto = texto_prompt(variante, rol, prompts_dir=prompts_dir)
        except RolNoDisponibleError:
            continue  # the role's package is not here: nothing to embed or to fingerprint
        huellas[rol] = huella(texto)
        if not es_integrada(rol, version):
            textos[version] = texto
    return variante.model_copy(update={"textos": textos, "huellas": huellas})


def desajustes(variante: Variante) -> list[str]:
    """Roles whose integrated text changed since the variant recorded its content hash (warnings)."""
    avisos: list[str] = []
    for rol, esperada in variante.huellas.items():
        if not es_integrada(rol, variante.prompts.get(rol, version_integrada(rol))):
            continue
        try:
            actual = huella(texto_integrado(rol))
        except RolNoDisponibleError:
            avisos.append(f"La variante «{variante.id}» se evaluó con el prompt «{rol}», que aquí no está disponible.")
            continue
        if actual != esperada:
            avisos.append(
                f"La variante «{variante.id}» se evaluó con otro texto del prompt «{rol}» (el código ha cambiado "
                "después): vuelve a evaluarla."
            )
    return avisos


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
        if es_integrada(rol, version) or version in textos:
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
    if configurada:
        return Path(configurada)
    if not ACTIVA_POR_DEFECTO.exists() and ACTIVA_HISTORICA.exists():
        return ACTIVA_HISTORICA
    return ACTIVA_POR_DEFECTO


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
            try:
                texto_prompt(activa, rol)
            except RolNoDisponibleError:
                continue  # a role whose package is not here cannot be used anyway; the others still apply
    except Exception:
        logger.exception("Variante activa ilegible en %s: se usa la base (prompts integrados).", ruta)
        activa = VARIANTE_BASE
    with _cache_lock:
        _cache[clave] = (marca, activa)
    return activa


def escribir_activa(
    variante: Variante, *, frente_a: str | None, resumen: dict[str, Any] | None, ruta: Path | None = None
) -> Path:
    """Write the active variant (self-contained). Only :mod:`asistente.nucleo.evaluacion.gate` calls this."""
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
    """Run the block with ``variante`` instead of the active one (the evaluation runner uses this).

    A ``ContextVar``: a worker thread sees it only if it runs inside ``contextvars.copy_context().run``.
    """
    token = _EN_USO.set(variante)
    try:
        yield variante
    finally:
        _EN_USO.reset(token)


def variante_en_uso() -> Variante:
    return _EN_USO.get() or variante_activa()


def prompt_activo(rol: Rol) -> str:
    """The prompt text for ``rol`` in the variant in use; the integrated text if anything goes wrong.

    Raises :class:`RolNoDisponibleError` (a ``LookupError``) only when the integrated text itself cannot be read
    (the role's package is missing): the caller decides what to do then.
    """
    try:
        return texto_prompt(variante_en_uso(), rol)
    except RolNoDisponibleError:
        raise
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
    "ROLES",
    "ROLES_EXTERNOS",
    "SIN_REGISTRAR",
    "VARIANTE_BASE",
    "Rol",
    "RolNoDisponibleError",
    "Variante",
    "VarianteActiva",
    "VarianteInvalidaError",
    "VersionRegistrada",
    "argumentos_proveedor",
    "autocontenida",
    "cargar_registro",
    "cargar_variante",
    "comprobar_integrados",
    "desajustes",
    "es_integrada",
    "escribir_activa",
    "huella",
    "huellas_de",
    "origen_de",
    "pendientes_de_registro",
    "prompt_activo",
    "registrar_version",
    "rol_disponible",
    "roles_disponibles",
    "ruta_activa",
    "texto_integrado",
    "texto_prompt",
    "usar_variante",
    "variante_activa",
    "variante_en_uso",
    "version_integrada",
]
