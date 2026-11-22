"""Data one domain asks of another (``Declaracion.pide`` / ``Declaracion.ofrece``, see ``ganchos.py``).

A domain never reads another domain's facts or calls its tools on its own: it asks with :func:`pedir_dato`, which
serves the datum only when BOTH sides declared it (the asker in ``pide``, the owner in ``ofrece``). The datum is
the owner's stored fact or the result of one of its READ-ONLY tools, computed over the same household profile and
date; nothing is written (the owner's tool runs without ``registrar``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from asistente.nucleo.modulos.contrato import ContextoModulo, HerramientaModulo, ResultadoHerramienta
from asistente.nucleo.modulos.ganchos import DatoOfrecido, declaracion_de


class DatoNoDeclaradoError(LookupError):
    """The asker did not declare the request, or the owner does not offer that datum."""


@dataclass(frozen=True)
class RespuestaDato:
    modulo: str
    dato: str
    origen: str
    # The stored value (``hecho:``) or None when it comes from a tool.
    valor: Any = None
    # The tool's result (``herramienta:``) or None when it is a fact.
    resultado: ResultadoHerramienta | None = None

    @property
    def disponible(self) -> bool:
        if self.resultado is not None:
            return not self.resultado.es_error
        return self.valor is not None


def _por_clave(modulos: Iterable[Any], clave: str) -> Any:
    for modulo in modulos:
        if getattr(modulo, "clave", None) == clave:
            return modulo
    raise DatoNoDeclaradoError(f"no hay ningún ámbito {clave!r}")


def ofrecido(modulo: Any, dato: str) -> DatoOfrecido | None:
    return next((d for d in declaracion_de(modulo).ofrece if d.nombre == dato), None)


def pedir_dato(
    modulos: Sequence[Any], solicitante: str, modulo: str, dato: str, contexto: ContextoModulo
) -> RespuestaDato:
    """The datum ``dato`` of domain ``modulo`` for domain ``solicitante``, over ``contexto``'s profile."""
    pide = _por_clave(modulos, solicitante)
    if not any(p.modulo == modulo and p.dato == dato for p in declaracion_de(pide).pide):
        raise DatoNoDeclaradoError(f"{solicitante!r} no declara que pida {modulo}.{dato}")
    duenio = _por_clave(modulos, modulo)
    oferta = ofrecido(duenio, dato)
    if oferta is None:
        raise DatoNoDeclaradoError(f"{modulo!r} no ofrece el dato {dato!r}")
    clase, _, nombre = oferta.origen.partition(":")
    if clase == "hecho":
        return RespuestaDato(modulo=modulo, dato=dato, origen=oferta.origen, valor=contexto.perfil.get(nombre))
    herramienta = next(
        (h for h in duenio.herramientas if isinstance(h, HerramientaModulo) and h.spec.name == nombre), None
    )
    if herramienta is None or herramienta.efecto != "lectura":
        raise DatoNoDeclaradoError(f"{modulo}.{dato}: {nombre!r} no es una herramienta de lectura del ámbito")
    solo_lectura = replace(contexto, registrar=None)
    try:
        resultado = herramienta.ejecutar(dict(oferta.argumentos), solo_lectura)
    except Exception as exc:  # the owner's failure is reported, never raised into the asker
        resultado = ResultadoHerramienta(
            contenido=f"No se pudo obtener {modulo}.{dato}: {exc}", resumen="error", es_error=True
        )
    return RespuestaDato(modulo=modulo, dato=dato, origen=oferta.origen, resultado=resultado)


def problemas_de_datos(modulos: Sequence[Any]) -> list[str]:
    """Cross-domain check: every datum a domain asks for is offered by its owner."""
    claves = {getattr(m, "clave", None): m for m in modulos}
    problemas: list[str] = []
    for modulo in modulos:
        for pedido in declaracion_de(modulo).pide:
            duenio = claves.get(pedido.modulo)
            if duenio is None:
                problemas.append(f"{modulo.clave}: pide {pedido.modulo}.{pedido.dato} pero ese ámbito no existe")
            elif ofrecido(duenio, pedido.dato) is None:
                problemas.append(f"{modulo.clave}: pide {pedido.modulo}.{pedido.dato}, que {pedido.modulo} no ofrece")
    return problemas


__all__ = ["DatoNoDeclaradoError", "RespuestaDato", "ofrecido", "pedir_dato", "problemas_de_datos"]
