"""Discovery and validation of the capabilities (see ``contrato.py``).

Every package ``asistente.capacidades.<clave>`` whose ``__init__`` exposes ``CAPACIDAD`` is imported and
checked. A capability that fails to import or breaks a rule is logged and left out; the others keep working.

Rules checked here (``pedir`` relies on them):

* the key is a lower-case identifier equal to the package name, unique, and not reserved by the core;
* name and description are non-empty; consent is ``ninguno`` or ``usuario``; ``ambitos`` is None or a set of keys;
* every operation is an :class:`OperacionCapacidad` with a unique key, a known effect, a callable ``ejecutar``,
  JSON Schema objects as input and output, a non-empty description and a metering purpose that is None or text;
* an operation that acts outside the app (``actua``) cannot be offered to every domain (``ambitos`` declared).

``CAPACIDADES_DESACTIVADAS`` (setting, optional) switches capabilities off for everyone.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import importlib
import logging
import os
import pkgutil
import re
import threading
from typing import Any, get_args

from asistente.capacidades.contrato import Capacidad, Consentimiento, Efecto, OperacionCapacidad

logger = logging.getLogger(__name__)

PAQUETE = "asistente.capacidades"
_CLAVE_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
_EFECTOS = frozenset(get_args(Efecto))
_CONSENTIMIENTOS = frozenset(get_args(Consentimiento))
# The core's own files in the package and the core's own caller key.
CLAVES_RESERVADAS = frozenset({"contrato", "registro", "peticion", "consentimiento", "nucleo"})


@dataclass
class RegistroCapacidades:
    capacidades: dict[str, Capacidad] = field(default_factory=dict)
    errores: dict[str, str] = field(default_factory=dict)

    def __iter__(self) -> Any:
        return iter(self.capacidades.values())

    def __contains__(self, clave: object) -> bool:
        return clave in self.capacidades

    def get(self, clave: str) -> Capacidad | None:
        return self.capacidades.get(clave)

    def activas(self) -> list[Capacidad]:
        apagadas = desactivadas_por_operador()
        return [c for clave, c in self.capacidades.items() if clave not in apagadas]

    def operacion(self, capacidad: str, operacion: str) -> OperacionCapacidad | None:
        cap = self.capacidades.get(capacidad)
        if cap is None:
            return None
        return next((op for op in cap.operaciones if op.clave == operacion), None)


def desactivadas_por_operador() -> frozenset[str]:
    from asistente.plataforma import settings

    valor = str(getattr(settings, "CAPACIDADES_DESACTIVADAS", None) or os.environ.get("CAPACIDADES_DESACTIVADAS", ""))
    return frozenset(part.strip().lower() for part in valor.split(",") if part.strip())


def _texto(valor: Any) -> bool:
    return isinstance(valor, str) and bool(valor.strip())


def _es_esquema_objeto(valor: Any) -> bool:
    return isinstance(valor, Mapping) and valor.get("type") == "object"


def problemas_de(capacidad: Any, *, paquete: str | None = None) -> list[str]:
    """Every contract rule ``capacidad`` breaks on its own."""
    if not isinstance(capacidad, Capacidad):
        return ["no cumple el protocolo Capacidad (faltan atributos)"]
    clave = capacidad.clave
    if not isinstance(clave, str) or not _CLAVE_RE.match(clave):
        return [f"clave no válida: {clave!r}"]
    problemas: list[str] = []
    if clave in CLAVES_RESERVADAS:
        problemas.append(f"la clave {clave!r} está reservada por el núcleo")
    if paquete is not None and paquete != clave:
        problemas.append(f"la clave {clave!r} no coincide con el nombre del paquete {paquete!r}")
    for atributo in ("nombre", "descripcion"):
        if not _texto(getattr(capacidad, atributo, None)):
            problemas.append(f"{atributo} vacío")
    if capacidad.consentimiento not in _CONSENTIMIENTOS:
        problemas.append(f"consentimiento desconocido: {capacidad.consentimiento!r}")
    ambitos = capacidad.ambitos
    if ambitos is not None and (
        not isinstance(ambitos, frozenset) or not all(isinstance(a, str) and _CLAVE_RE.match(a) for a in ambitos)
    ):
        problemas.append("ambitos debe ser None o un frozenset de claves de ámbito")
    vistas: set[str] = set()
    for op in capacidad.operaciones or ():
        if not isinstance(op, OperacionCapacidad):
            problemas.append(f"operación que no es OperacionCapacidad: {op!r}")
            continue
        nombre = op.clave
        if not isinstance(nombre, str) or not _CLAVE_RE.match(nombre):
            problemas.append(f"clave de operación no válida: {nombre!r}")
            continue
        if nombre in vistas:
            problemas.append(f"operación repetida: {nombre}")
        vistas.add(nombre)
        if op.efecto not in _EFECTOS:
            problemas.append(f"{nombre}: efecto desconocido {op.efecto!r}")
        if not callable(op.ejecutar):
            problemas.append(f"{nombre}: ejecutar no es invocable")
        if not _es_esquema_objeto(op.entrada):
            problemas.append(f"{nombre}: la entrada debe ser un JSON Schema de tipo object")
        if not _es_esquema_objeto(op.salida):
            problemas.append(f"{nombre}: la salida debe ser un JSON Schema de tipo object")
        if not _texto(op.descripcion):
            problemas.append(f"{nombre}: descripción vacía")
        if op.proposito is not None and not _texto(op.proposito):
            problemas.append(f"{nombre}: el propósito de medición debe ser None o un texto")
        if op.ambitos is not None and not isinstance(op.ambitos, frozenset):
            problemas.append(f"{nombre}: ambitos debe ser None o un frozenset")
        if op.efecto == "actua" and op.ambitos is None and ambitos is None:
            problemas.append(f"{nombre}: una operación que actúa fuera de la app debe declarar qué ámbitos la usan")
    if not vistas:
        problemas.append("no declara ninguna operación")
    return problemas


def construir_registro(
    candidatos: Iterable[tuple[str, Any]], *, errores: Mapping[str, str] | None = None
) -> RegistroCapacidades:
    """Validate ``(origin, capability)`` pairs into a registry (accepted whole or not at all; first key wins)."""
    registro = RegistroCapacidades(errores=dict(errores or {}))
    for origen, capacidad in sorted(candidatos, key=lambda par: str(getattr(par[1], "clave", par[0]))):
        problemas = problemas_de(capacidad, paquete=origen)
        if not problemas and capacidad.clave in registro.capacidades:
            problemas.append(f"la clave {capacidad.clave!r} ya la usa otra capacidad")
        if problemas:
            motivo = "; ".join(problemas)
            registro.errores[origen] = motivo
            logger.error("Capability %s left out: %s", origen, motivo)
            continue
        registro.capacidades[capacidad.clave] = capacidad
    return registro


def descubrir(paquete: str = PAQUETE) -> tuple[list[tuple[str, Any]], dict[str, str]]:
    """Import every sub-package of ``paquete`` that exposes ``CAPACIDAD``. Import errors are returned."""
    base = importlib.import_module(paquete)
    encontrados: list[tuple[str, Any]] = []
    errores: dict[str, str] = {}
    for info in pkgutil.iter_modules(base.__path__):
        if not info.ispkg:
            continue
        nombre = f"{paquete}.{info.name}"
        try:
            paquete_capacidad = importlib.import_module(nombre)
        except Exception as exc:  # a broken capability must never crash the app
            errores[info.name] = f"no se pudo importar: {type(exc).__name__}: {exc}"
            logger.exception("Capability package %s failed to import", nombre)
            continue
        capacidad = getattr(paquete_capacidad, "CAPACIDAD", None)
        if capacidad is None:
            errores[info.name] = "el paquete no expone CAPACIDAD"
            logger.error("Capability package %s exposes no CAPACIDAD", nombre)
            continue
        encontrados.append((info.name, capacidad))
    return encontrados, errores


_lock = threading.Lock()
_cache: list[RegistroCapacidades] = []


def registro() -> RegistroCapacidades:
    """The process-wide registry, discovered once. :func:`reiniciar` forgets or replaces it (tests)."""
    if _cache:
        return _cache[0]
    with _lock:
        if not _cache:
            encontrados, errores = descubrir()
            _cache.append(construir_registro(encontrados, errores=errores))
        return _cache[0]


def reiniciar(nuevo: RegistroCapacidades | None = None) -> None:
    with _lock:
        _cache.clear()
        if nuevo is not None:
            _cache.append(nuevo)


__all__ = [
    "CLAVES_RESERVADAS",
    "RegistroCapacidades",
    "construir_registro",
    "desactivadas_por_operador",
    "descubrir",
    "problemas_de",
    "registro",
    "reiniciar",
]
