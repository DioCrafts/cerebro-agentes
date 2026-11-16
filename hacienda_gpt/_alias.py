"""The import hook of the compatibility package: every old module path is an alias of its new module.

``hacienda_gpt.<old path>`` resolves to the module of :data:`NUEVOS` (longest prefix wins) — the SAME module
object, so module state, ``isinstance`` checks and monkeypatching behave exactly as with the new path. Packages
whose children went to different places (``tax``, ``modulos``, ``ops``, ``conocimiento``) are small stand-in
packages (:data:`REPARTIDOS`) so that importing one child never adds an attribute to another real package.

``python -m hacienda_gpt.<old path>`` works too: the loader hands :mod:`runpy` the new module's code.
"""

from __future__ import annotations

from collections.abc import Sequence
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
import types
from typing import Any
import warnings

RAIZ = "hacienda_gpt"

# Old dotted prefix -> new dotted prefix. Every old module path (the shims of earlier releases included:
# ``tax.<calculator>``, ``tax.documentos``, ``modulos.correo``, ``modulos.navegador``, ``navegador``) is covered.
NUEVOS: dict[str, str] = {
    "hacienda_gpt.settings": "asistente.plataforma.settings",
    # The core.
    "hacienda_gpt.agent": "asistente.nucleo.agent",
    "hacienda_gpt.tareas": "asistente.nucleo.tareas",
    "hacienda_gpt.eventos": "asistente.nucleo.eventos",
    "hacienda_gpt.memoria": "asistente.nucleo.memoria",
    "hacienda_gpt.evaluacion": "asistente.nucleo.evaluacion",
    "hacienda_gpt.evals": "asistente.nucleo.evals",
    "hacienda_gpt.proactive": "asistente.nucleo.proactive",
    "hacienda_gpt.decision": "asistente.nucleo.decision",
    "hacienda_gpt.planificador": "asistente.nucleo.planificador",
    "hacienda_gpt.modulos": "asistente.nucleo.modulos",
    # The domains.
    "hacienda_gpt.modulos.ayudas": "asistente.ambitos.ayudas",
    "hacienda_gpt.modulos.caducidades": "asistente.ambitos.caducidades",
    "hacienda_gpt.modulos.fiscal": "asistente.ambitos.fiscal",
    "hacienda_gpt.modulos.hipoteca": "asistente.ambitos.hipoteca",
    "hacienda_gpt.modulos.luz": "asistente.ambitos.luz",
    "hacienda_gpt.modulos.suscripciones": "asistente.ambitos.suscripciones",
    "hacienda_gpt.tax": "asistente.ambitos.fiscal.calculo",
    "hacienda_gpt.tax.documentos": "asistente.ambitos.fiscal.documentos",
    "hacienda_gpt.tax.profile": "asistente.ambitos.fiscal.profile",
    # The capabilities.
    "hacienda_gpt.capacidades": "asistente.capacidades",
    "hacienda_gpt.modulos.correo": "asistente.capacidades.correo",
    "hacienda_gpt.modulos.navegador": "asistente.capacidades.navegador",
    "hacienda_gpt.navegador": "asistente.capacidades.navegador",
    # Knowledge.
    "hacienda_gpt.conocimiento": "asistente.conocimiento",
    "hacienda_gpt.conocimiento.recodificacion": "asistente.conocimiento.recodificacion.de_modulos",
    "hacienda_gpt.tax.recodificacion": "asistente.conocimiento.recodificacion",
    "hacienda_gpt.tax.fuentes": "asistente.conocimiento.fuentes",
    "hacienda_gpt.tax.rollover": "asistente.conocimiento.rollover",
    "hacienda_gpt.tax.verificacion": "asistente.conocimiento.verificacion",
    "hacienda_gpt.tax.vigencia_normas": "asistente.conocimiento.vigencia_normas",
    "hacienda_gpt.crawler": "asistente.conocimiento.crawler",
    "hacienda_gpt.processor": "asistente.conocimiento.processor",
    "hacienda_gpt.doctrine": "asistente.conocimiento.doctrine",
    "hacienda_gpt.llm": "asistente.conocimiento.llm",
    **{
        f"hacienda_gpt.ops.{nombre}": f"asistente.conocimiento.{nombre}"
        for nombre in (
            "formatos",
            "formatos_agente",
            "formatos_boe",
            "formatos_calendario",
            "formatos_corpus",
            "formatos_documentos",
            "formatos_esqueleto",
            "formatos_fuentes",
        )
    },
    # The platform.
    "hacienda_gpt.plataforma": "asistente.plataforma",
    "hacienda_gpt.api": "asistente.plataforma.api",
    "hacienda_gpt.auth": "asistente.plataforma.auth",
    "hacienda_gpt.push": "asistente.plataforma.push",
    "hacienda_gpt.cli": "asistente.plataforma.cli",
    "hacienda_gpt.ui": "asistente.plataforma.ui",
    "hacienda_gpt.observability": "asistente.plataforma.observability",
    "hacienda_gpt.governance": "asistente.plataforma.governance",
    "hacienda_gpt.utils": "asistente.plataforma.utils",
    "hacienda_gpt.ops": "asistente.plataforma.ops",
}

# Old packages whose children now live in different places: stand-in packages, never the new package itself
# (``hacienda_gpt.conocimiento.recodificacion`` must not become ``asistente.conocimiento.recodificacion``'s value).
# A name that is not a child module is read from the package the old one mostly became.
REPARTIDOS: dict[str, str] = {
    "hacienda_gpt.tax": "asistente.ambitos.fiscal.calculo",
    "hacienda_gpt.modulos": "asistente.nucleo.modulos",
    "hacienda_gpt.ops": "asistente.plataforma.ops",
    "hacienda_gpt.conocimiento": "asistente.conocimiento",
}

_MIOS = (
    os.path.dirname(os.path.abspath(__file__)) + os.sep,
    os.path.dirname(os.path.abspath(importlib.__file__)) + os.sep,
    "<frozen importlib",
)


def nuevo_nombre(viejo: str) -> str | None:
    """The new dotted path of an old one (longest prefix of :data:`NUEVOS`), or None when it is not an old path."""
    partes = viejo.split(".")
    for corte in range(len(partes), 1, -1):
        prefijo = ".".join(partes[:corte])
        if prefijo in NUEVOS:
            return ".".join([NUEVOS[prefijo], *partes[corte:]])
    return None


def avisar(viejo: str, nuevo: str) -> None:
    warnings.warn(
        f"«{viejo}» está obsoleto y desaparecerá en la próxima versión: usa «{nuevo}».",
        DeprecationWarning,
        stacklevel=2,
        skip_file_prefixes=_MIOS,
    )


class _Repartido(types.ModuleType):
    """A stand-in for an old package split across the new layout."""

    def __getattr__(self, nombre: str) -> Any:
        if nombre.startswith("__"):
            raise AttributeError(nombre)
        hijo = f"{self.__name__}.{nombre}"
        if nuevo_nombre(hijo) is not None and importlib.util.find_spec(hijo) is not None:
            return importlib.import_module(hijo)
        return getattr(importlib.import_module(REPARTIDOS[self.__name__]), nombre)


class _Cargador(importlib.abc.Loader):
    def __init__(self, nuevo: str, destino: importlib.machinery.ModuleSpec | None) -> None:
        self.nuevo = nuevo
        self.destino = destino

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        avisar(spec.name, self.nuevo)
        if spec.name in REPARTIDOS:
            repartido = _Repartido(spec.name, f"Paquete antiguo, repartido: ver {REPARTIDOS[spec.name]}.")
            repartido.__path__ = []
            return repartido
        # The new module itself: ``sys.modules`` gets the same object under the old name.
        modulo = importlib.import_module(self.nuevo)
        self._spec_propio = modulo.__spec__
        return modulo

    def exec_module(self, module: types.ModuleType) -> None:
        # The import system has just set ``module.__spec__`` to the alias' spec: the module keeps its own (its
        # name, loader and location), or ``importlib.reload`` and friends would act on the alias.
        propio = getattr(self, "_spec_propio", None)
        if propio is not None:
            module.__spec__ = propio

    # ``python -m hacienda_gpt.<old path>``: runpy runs the new module's code.
    def get_code(self, fullname: str) -> Any:
        if self.destino is None or self.destino.loader is None:
            return None
        get_code = getattr(self.destino.loader, "get_code", None)
        return get_code(self.nuevo) if get_code is not None else None

    def is_package(self, fullname: str) -> bool:
        return self.destino is not None and self.destino.submodule_search_locations is not None


class Buscador(importlib.abc.MetaPathFinder):
    """Resolves ``hacienda_gpt.<old path>`` to the module of its new path."""

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: types.ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if not fullname.startswith(RAIZ + "."):
            return None
        nuevo = nuevo_nombre(fullname)
        if nuevo is None:
            return None
        if fullname in REPARTIDOS:
            spec = importlib.machinery.ModuleSpec(fullname, _Cargador(nuevo, None), is_package=True)
            spec.submodule_search_locations = []
            return spec
        try:
            destino = importlib.util.find_spec(nuevo)
        except ModuleNotFoundError:
            return None
        if destino is None:
            return None
        es_paquete = destino.submodule_search_locations is not None
        spec = importlib.machinery.ModuleSpec(
            fullname, _Cargador(nuevo, destino), origin=destino.origin, is_package=es_paquete
        )
        if es_paquete:
            spec.submodule_search_locations = list(destino.submodule_search_locations or [])
        spec.has_location = destino.has_location
        return spec


def instalar() -> None:
    if not any(isinstance(b, Buscador) for b in sys.meta_path):
        sys.meta_path.insert(0, Buscador())


__all__ = ["NUEVOS", "REPARTIDOS", "Buscador", "instalar", "nuevo_nombre"]
