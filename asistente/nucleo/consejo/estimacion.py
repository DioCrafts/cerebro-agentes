"""What a council session will cost and take, BEFORE anything runs (the user confirms it).

The estimate is code, not a model: the number of calls of every phase (the dossier's specialists, the proposals,
the anonymous critiques, the revision, the chair, the brain's verifier), an approximate prompt and output size for
each from the dossier's size and the number of members, and the price of the model that serves each purpose — the
routing table (``agent/enrutado.py``) and the project's price table (``ops/data/precios_llm.json``, USD per million
tokens, Decimal). A model without a known price is priced at the most expensive known model, so the estimate stays
an upper bound rather than a guess; a local model costs nothing.

The hard cap of the session (``tope_usd``) is the estimate × ``CONSEJO_FACTOR_TOPE``, never above
``CONSEJO_TOPE_MAX_USD``. A session whose ESTIMATE is already above that maximum is not offered (``excede_maximo``).
The duration is a rough wall-clock estimate (phases are sequential, members in a phase run in parallel), rounded
to minutes for the user.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_UP, Decimal, InvalidOperation
import logging
import math

from asistente.plataforma import settings

logger = logging.getLogger(__name__)

_MILLON = Decimal(1_000_000)
_CENTIMO = Decimal("0.01")
# Prompt / output tokens of the fixed parts (system prompts, rubric, schema), per call.
SISTEMA_MIEMBRO = 1800
SISTEMA_PRESIDENTE = 2500
SALIDA_PROPUESTA = 1300
SALIDA_CRITICA_POR_PROPUESTA = 450
SALIDA_SINTESIS = 2200
PROPUESTA_COMO_ENTRADA = 1100  # a proposal as another member reads it (anonymised, capped)
# The dossier's own phase.
EXPEDIENTE_ENTRADA, EXPEDIENTE_SALIDA = 1800, 700
ESPECIALISTA_PASOS, ESPECIALISTA_ENTRADA, ESPECIALISTA_SALIDA = 3, 6000, 900
FISCAL_PASOS, FISCAL_ENTRADA, FISCAL_SALIDA = 4, 9000, 1200
VERIFICADOR_ENTRADA, VERIFICADOR_SALIDA = 5000, 900
# Seconds, for the duration estimate.
SEG_EXPEDIENTE, SEG_ESPECIALISTA, SEG_RONDA, SEG_SINTESIS, SEG_PUERTAS = 10, 45, 35, 45, 20
# Prompt tokens of a dossier before it exists (the question, options, a few facts, figures of two specialists).
EXPEDIENTE_BASE_TOKENS = 2500


def tokens_aprox(texto: str) -> int:
    """~4 characters per token (Spanish prose); never 0 for a non-empty text."""
    return max(1, math.ceil(len(texto or "") / 4)) if texto else 0


def _decimal(nombre: str, por_defecto: str) -> Decimal:
    try:
        return Decimal(str(getattr(settings, nombre, por_defecto)))
    except InvalidOperation, ValueError:
        return Decimal(por_defecto)


def factor_tope() -> Decimal:
    factor = _decimal("CONSEJO_FACTOR_TOPE", "2.0")
    return factor if factor >= 1 else Decimal(1)


def tope_maximo() -> Decimal:
    maximo = _decimal("CONSEJO_TOPE_MAX_USD", "3.00")
    return maximo if maximo > 0 else Decimal("3.00")


def familias() -> list[str]:
    """The model families configured for the members (``CONSEJO_FAMILIAS``), else the configured provider."""
    crudas = [f.strip().lower() for f in str(getattr(settings, "CONSEJO_FAMILIAS", "") or "").split(",")]
    return [f for f in crudas if f] or [str(getattr(settings, "LLM_PROVIDER", "anthropic") or "anthropic").lower()]


def _modelo_base(familia: str) -> str:
    if familia in ("anthropic", "claude"):
        return str(getattr(settings, "ANTHROPIC_MODEL", "") or "")
    if familia == "openai":
        return str(getattr(settings, "OPENAI_AGENT_MODEL", "") or "")
    if familia == "local":
        return str(getattr(settings, "LOCAL_LLM_MODEL", "") or "")
    return ""


@dataclass(frozen=True)
class Precio:
    """USD per million tokens (input, output) of the model serving one purpose."""

    modelo: str
    entrada: Decimal
    salida: Decimal
    conocido: bool


def precio_de(proposito: str, familia: str | None = None) -> Precio:
    """The price of the model that serves ``proposito`` on ``familia`` (routing + price table)."""
    from asistente.nucleo.agent import enrutado
    from asistente.plataforma.ops.consumo import cargar_precios

    familia = (familia or familias()[0]).lower()
    if familia == "local":
        return Precio(modelo=_modelo_base("local") or "local", entrada=Decimal(0), salida=Decimal(0), conocido=True)
    try:
        ruta = enrutado.resolver(proposito, proveedor=familia)
        modelo = ruta.modelo_efectivo(_modelo_base(familia))
    except Exception:  # an invalid routing setting: price the base model
        logger.warning("routing of %s unavailable for the council's estimate", proposito, exc_info=True)
        modelo = _modelo_base(familia)
    try:
        tabla = cargar_precios()
    except Exception:
        logger.warning("price table unavailable: the council's estimate uses a conservative price", exc_info=True)
        return Precio(modelo=modelo, entrada=Decimal("15"), salida=Decimal("75"), conocido=False)
    precio = tabla.precio_de(modelo) if modelo else None
    if precio is not None and precio.input is not None and precio.output is not None:
        return Precio(modelo=modelo, entrada=precio.input, salida=precio.output, conocido=True)
    # Unknown: the most expensive known model, so the estimate is an upper bound.
    conocidos = [p for p in tabla.modelos.values() if p.input is not None and p.output is not None]
    entrada = max((p.input for p in conocidos if p.input is not None), default=Decimal("15"))
    salida = max((p.output for p in conocidos if p.output is not None), default=Decimal("75"))
    return Precio(modelo=modelo or "desconocido", entrada=entrada, salida=salida, conocido=False)


@dataclass
class Estimacion:
    """The estimate shown to the user before confirming."""

    usd: Decimal
    tope_usd: Decimal
    segundos: int
    llamadas: int
    miembros: int
    # USD per phase («instruccion», «propuestas», «criticas», «revision», «sintesis», «puertas»).
    desglose: dict[str, Decimal] = field(default_factory=dict)
    # Every price came from the table (False: some model was priced at the most expensive known one).
    precios_conocidos: bool = True
    # The estimate itself is above CONSEJO_TOPE_MAX_USD: the session cannot be offered.
    excede_maximo: bool = False

    @property
    def minutos(self) -> int:
        return max(1, round(self.segundos / 60))


def _coste(precio: Precio, entrada: int, salida: int, veces: int = 1) -> Decimal:
    return (Decimal(entrada) * precio.entrada + Decimal(salida) * precio.salida) * veces / _MILLON


def estimar(
    n_miembros: int,
    expediente_aprox_tokens: int,
    *,
    rondas: int = 2,
    especialistas: Sequence[str] = (),
    max_paralelo: int | None = None,
    familias_miembros: Sequence[str] | None = None,
) -> Estimacion:
    """The full estimate: cost per phase, cap, calls and duration.

    ``expediente_aprox_tokens``: the dossier's size as members read it (before the dossier exists, the question's
    size plus :data:`EXPEDIENTE_BASE_TOKENS`). ``rondas``: proposal rounds (1 = no revision round). ``especialistas``:
    the specialists phase 0 will consult (``fiscal`` runs the tax advisor, dearer). ``familias_miembros``: the family
    of each seat (cycled); default ``CONSEJO_FAMILIAS``.
    """
    n = max(1, int(n_miembros))
    rondas = 2 if rondas >= 2 else 1
    paralelo = max(1, int(max_paralelo or getattr(settings, "CONSEJO_MAX_PARALELO", 3) or 3))
    expediente = max(0, int(expediente_aprox_tokens))
    fams = list(familias_miembros or familias())
    desglose: dict[str, Decimal] = {}
    conocidos = True
    llamadas = 0

    def precio(proposito: str, familia: str | None = None) -> Precio:
        nonlocal conocidos
        p = precio_de(proposito, familia)
        conocidos = conocidos and p.conocido
        return p

    # Phase 0: the neutral question and the options, then each specialist on every option.
    instruccion = _coste(precio("consejo:expediente"), EXPEDIENTE_ENTRADA + expediente // 3, EXPEDIENTE_SALIDA)
    llamadas += 1
    for clave in dict.fromkeys(especialistas):
        if clave == "fiscal":
            instruccion += _coste(precio("agente"), FISCAL_ENTRADA, FISCAL_SALIDA, FISCAL_PASOS)
            instruccion += _coste(precio("verificador"), VERIFICADOR_ENTRADA, VERIFICADOR_SALIDA)
            llamadas += FISCAL_PASOS + 1
        else:
            instruccion += _coste(
                precio(f"especialista:{clave}"), ESPECIALISTA_ENTRADA, ESPECIALISTA_SALIDA, ESPECIALISTA_PASOS
            )
            llamadas += ESPECIALISTA_PASOS
    desglose["instruccion"] = instruccion

    por_miembro = [precio(f"consejo:miembro:{i}", fams[i % len(fams)]) for i in range(n)]
    otros = max(0, n - 1)
    desglose["propuestas"] = sum(
        (_coste(p, SISTEMA_MIEMBRO + expediente, SALIDA_PROPUESTA) for p in por_miembro), Decimal(0)
    )
    desglose["criticas"] = sum(
        (
            _coste(
                p,
                SISTEMA_MIEMBRO + expediente + otros * PROPUESTA_COMO_ENTRADA,
                otros * SALIDA_CRITICA_POR_PROPUESTA + 200,
            )
            for p in por_miembro
        ),
        Decimal(0),
    )
    llamadas += 2 * n
    if rondas == 2:
        desglose["revision"] = sum(
            (
                _coste(
                    p,
                    SISTEMA_MIEMBRO + expediente + PROPUESTA_COMO_ENTRADA + otros * SALIDA_CRITICA_POR_PROPUESTA + 150,
                    SALIDA_PROPUESTA,
                )
                for p in por_miembro
            ),
            Decimal(0),
        )
        llamadas += n
    desglose["sintesis"] = _coste(
        precio("consejo:presidente"),
        SISTEMA_PRESIDENTE + expediente + n * PROPUESTA_COMO_ENTRADA + n * otros * 120,
        SALIDA_SINTESIS,
    )
    desglose["puertas"] = _coste(precio("verificador"), VERIFICADOR_ENTRADA, VERIFICADOR_SALIDA)
    llamadas += 2

    total = sum(desglose.values(), Decimal(0)).quantize(_CENTIMO, rounding=ROUND_UP)
    if total <= 0 and not all(f == "local" for f in fams):
        total = _CENTIMO
    tope = min((total * factor_tope()).quantize(_CENTIMO, rounding=ROUND_UP), tope_maximo())
    lotes = math.ceil(n / paralelo)
    segundos = (
        SEG_EXPEDIENTE
        + (SEG_ESPECIALISTA if especialistas else 0)
        + lotes * SEG_RONDA * (3 if rondas == 2 else 2)
        + SEG_SINTESIS
        + SEG_PUERTAS
    )
    return Estimacion(
        usd=total,
        tope_usd=max(tope, total) if total <= tope_maximo() else tope,
        segundos=segundos,
        llamadas=llamadas,
        miembros=n,
        desglose={k: v.quantize(Decimal("0.0001"), rounding=ROUND_UP) for k, v in desglose.items()},
        precios_conocidos=conocidos,
        excede_maximo=total > tope_maximo(),
    )


def estimar_coste(
    n_miembros: int,
    expediente_aprox_tokens: int,
    *,
    rondas: int = 2,
    especialistas: Sequence[str] = (),
    max_paralelo: int | None = None,
) -> tuple[Decimal, Decimal]:
    """``(estimacion_usd, tope_usd)`` (the shared interface); :func:`estimar` gives the details."""
    estimacion = estimar(
        n_miembros, expediente_aprox_tokens, rondas=rondas, especialistas=especialistas, max_paralelo=max_paralelo
    )
    return estimacion.usd, estimacion.tope_usd


def formatear_usd(valor: Decimal) -> str:
    """«0,40 USD» (Spanish decimal comma, two decimals)."""
    return f"{valor.quantize(_CENTIMO, rounding=ROUND_UP):.2f}".replace(".", ",") + " USD"


__all__ = [
    "EXPEDIENTE_BASE_TOKENS",
    "Estimacion",
    "Precio",
    "estimar",
    "estimar_coste",
    "factor_tope",
    "familias",
    "formatear_usd",
    "precio_de",
    "tokens_aprox",
    "tope_maximo",
]
