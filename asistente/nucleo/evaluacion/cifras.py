"""Expected figures, computed by the deterministic calculators on the case snapshot — never by a model.

A suite case does not store "the answer must say 5.140,12 €". It stores a recipe (:class:`CalculoEsperado`):
which tool, with which arguments, and a regex that reads the figure from the tool's output. Running the
suite re-runs that tool on the pseudonymised snapshot, so when a parameter is recoded from the BOE the
expectation follows the law instead of freezing an old number (the old one is kept as ``registrada`` only
to report the drift).

Tax tools run through the agent's own :class:`~asistente.nucleo.agent.tools.Toolbox`; module tools through the
module's ``ejecutar`` with the same :class:`ContextoModulo` a specialist gives them. A tool that acts outside
the app (``efecto="actua"``) is never executed here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any

from asistente.conocimiento.fragmento import FragmentoRecuperado
from asistente.nucleo.agent.evidence import EvidenceLedger
from asistente.nucleo.agent.providers.base import ToolCall
from asistente.nucleo.agent.search import SearchFilters
from asistente.nucleo.agent.tools import Toolbox
from asistente.nucleo.decision.schemas import CaseState, Fact, FactValueType
from asistente.nucleo.evaluacion.modelos import CalculoEsperado, Expediente
from asistente.nucleo.modulos.contrato import ContextoModulo, HerramientaModulo, Modulo
from asistente.nucleo.modulos.perfil import perfil_de, tipo_esperado
from asistente.nucleo.proactive.jobs import hoy_en_espana


class CalculoError(ValueError):
    """The expected figure cannot be computed (tool error, unknown tool, pattern without a match)."""


class SinCorpus:
    """A searcher with nothing in it: the calculators never search."""

    def search(self, query: str, *, k: int, filters: SearchFilters) -> list[FragmentoRecuperado]:
        return []


def _tipo_de(nombre: str, valor: Any) -> FactValueType:
    conocido = tipo_esperado(nombre)
    if conocido is not None:
        return conocido
    if isinstance(valor, bool):
        return FactValueType.BOOLEAN
    if isinstance(valor, int | float):
        return FactValueType.NUMBER
    return FactValueType.STRING


def _valor_guardado(valor: Any) -> Any:
    # Structured facts are stored as canonical JSON text, as the case store does.
    if isinstance(valor, dict | list):
        return json.dumps(valor, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if isinstance(valor, int) and not isinstance(valor, bool):
        return float(valor)
    return valor


def caso_desde_expediente(expediente: Expediente | None, *, case_id: str = "eval") -> CaseState | None:
    """A throw-away case holding the snapshot's facts (None without a snapshot)."""
    if expediente is None:
        return None
    ahora = datetime.now(UTC)
    hechos = [
        Fact(
            fact_id=f"fact_{nombre}",
            name=nombre,
            value=_valor_guardado(valor),
            value_type=_tipo_de(nombre, valor),
            source="evaluacion",
            confidence=1.0,
            updated_at=ahora,
        )
        for nombre, valor in expediente.hechos.items()
    ]
    return CaseState(
        case_id=case_id,
        user_id="evaluacion",
        jurisdiction=expediente.jurisdiction,
        tax_period=expediente.tax_period,
        facts=hechos,
    )


def herramientas_fiscales() -> frozenset[str]:
    """Names of the central agent's own (tax) tools."""
    caja = Toolbox(searcher=SinCorpus(), ledger=EvidenceLedger(), case=caso_desde_expediente(Expediente()))
    return frozenset(spec.name for spec in caja.specs())


def _herramienta_de_modulo(nombre: str, extra: Sequence[Modulo]) -> HerramientaModulo | None:
    from asistente.nucleo.modulos.registro import registro

    for modulo in extra:
        for herramienta in modulo.herramientas:
            if herramienta.spec.name == nombre:
                return herramienta
    encontrada = registro().herramienta(nombre)
    return encontrada[1] if encontrada is not None else None


def salida_de_herramienta(
    nombre: str,
    argumentos: dict[str, Any],
    expediente: Expediente | None,
    *,
    modulos_extra: Sequence[Modulo] = (),
) -> str:
    """The text a tool returns on the snapshot. Raises :class:`CalculoError`."""
    caso = caso_desde_expediente(expediente)
    if nombre in herramientas_fiscales():
        caja = Toolbox(searcher=SinCorpus(), ledger=EvidenceLedger(), case=caso)
        salida = caja.execute(ToolCall(id="evaluacion", name=nombre, arguments=dict(argumentos)))
        if salida.is_error:
            raise CalculoError(f"{nombre} devolvió un error: {salida.content[:300]}")
        return salida.content
    herramienta = _herramienta_de_modulo(nombre, modulos_extra)
    if herramienta is None:
        raise CalculoError(f"Herramienta desconocida: {nombre}.")
    if herramienta.efecto == "actua":
        raise CalculoError(f"{nombre} actúa fuera de la aplicación: nunca se ejecuta para calcular una cifra.")
    contexto = ContextoModulo(perfil=perfil_de(caso), hoy=hoy_en_espana())
    resultado = herramienta.ejecutar(dict(argumentos), contexto)
    if resultado.es_error:
        raise CalculoError(f"{nombre} devolvió un error: {resultado.contenido[:300]}")
    return resultado.contenido


def calcular_cifra(
    calculo: CalculoEsperado, expediente: Expediente | None, *, modulos_extra: Sequence[Modulo] = ()
) -> str:
    """The expected figure: run the tool, read ``patron``'s ``cifra`` group. Raises :class:`CalculoError`."""
    salida = salida_de_herramienta(
        calculo.herramienta,
        calculo.argumentos,
        expediente if calculo.con_expediente else None,
        modulos_extra=modulos_extra,
    )
    encontrado = re.search(calculo.patron, salida, flags=re.MULTILINE)
    if encontrado is None:
        raise CalculoError(f"El patrón «{calculo.patron}» no encuentra la cifra en la salida de {calculo.herramienta}.")
    cifra = encontrado.group("cifra").strip()
    if not cifra:
        raise CalculoError(f"El patrón de {calculo.herramienta} captura una cifra vacía.")
    return cifra


# --------------------------------------------------------------------------- #
# Finding a figure in an answer
# --------------------------------------------------------------------------- #

_NUMERO_RE = re.compile(r"\d[\d.,]*\d|\d")
_CIFRA_RE = re.compile(r"^-?\s*\d[\d.,]*\s*(?:€|%|euros?)?$", re.IGNORECASE)


def numero(texto: str) -> Decimal | None:
    """Spanish-formatted number → Decimal ('1.234,56' → 1234.56; '18.36' → 18.36; '1.600' → 1600)."""
    limpio = texto.strip()
    if "," in limpio and "." in limpio:
        limpio = limpio.replace(".", "").replace(",", ".")
    elif "," in limpio:
        if limpio.count(",") > 1:
            return None
        limpio = limpio.replace(",", ".")
    elif "." in limpio:
        partes = limpio.split(".")
        if all(len(p) == 3 for p in partes[1:]) and len(partes[0]) <= 3:
            limpio = limpio.replace(".", "")  # thousands separators
        elif len(partes) != 2:
            return None  # otherwise a single decimal point (e.g. "18.36 %")
    try:
        return Decimal(limpio)
    except InvalidOperation:
        return None


def valor_de_cifra(cifra: str) -> Decimal | None:
    """The numeric value of a figure like '-1.234,56 €' (absolute), or None for a date or a text."""
    if not _CIFRA_RE.match(cifra.strip()):
        return None
    encontrado = _NUMERO_RE.search(cifra)
    valor = numero(encontrado.group(0)) if encontrado else None
    return abs(valor) if valor is not None else None


def numeros_en(texto: str) -> set[Decimal]:
    valores: set[Decimal] = set()
    for token in _NUMERO_RE.findall(texto):
        valor = numero(token.strip(".,"))
        if valor is not None:
            valores.add(abs(valor))
    return valores


def _normalizar(texto: str) -> str:
    return " ".join(texto.split()).casefold()


def aparece(cifra: str, texto: str) -> bool:
    """True when the figure is in the text: as a number (any Spanish formatting, sign in words) or literally."""
    valor = valor_de_cifra(cifra)
    if valor is not None:
        return valor in numeros_en(texto)
    return _normalizar(cifra) in _normalizar(texto)


__all__ = [
    "CalculoError",
    "SinCorpus",
    "aparece",
    "calcular_cifra",
    "caso_desde_expediente",
    "herramientas_fiscales",
    "numero",
    "numeros_en",
    "salida_de_herramienta",
    "valor_de_cifra",
]
