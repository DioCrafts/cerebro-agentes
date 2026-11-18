"""The council's seats (roles with sharp, non-overlapping mandates) and who fills them in a session.

Roles
-----
:data:`ROLES_POR_DEFECTO` are four seats whose mandates do not overlap, so the council covers the decision from
four angles instead of four times from the same one: financial prudence and risk; tax and legal consequences (never
beyond the dossier's cited sources); liquidity and the family's wellbeing; and the devil's advocate, who must build
the strongest case AGAINST the option that looks strongest. :data:`ROL_LARGO_PLAZO` is an optional fifth seat.
``CONSEJO_MIEMBROS`` picks the seats (:func:`roles_configurados`). The chair (:data:`PRESIDENTE`) does not vote.

Filling the seats (diversity without network)
---------------------------------------------
Each seat is served by a model of a provider FAMILY (``anthropic``, ``openai``, ``local``). With several families
configured (``CONSEJO_FAMILIAS``), seats go round-robin over the ones that are actually available — a seat whose
role prefers an available family gets it — so members of different families critique each other (models of one
family share blind spots). Availability is read from settings and keys, never from the network
(:func:`familia_disponible`): the configured ``LLM_PROVIDER`` always is; another family only with its credentials
(``ANTHROPIC_API_KEY``/``ANTHROPIC_AUTH_TOKEN``, ``OPENAI_API_KEY``, ``LOCAL_LLM_MODEL``). A family that is not
available is skipped, never an error; with none left, the configured provider serves every seat.

With ONE family, diversity comes from the role, the instructions and the reasoning depth: seats alternate the
configured effort and a deeper one (``xhigh``, or ``max`` when ``xhigh`` is the configured one; Claude only — the
provider contract exposes no temperature, and Claude's adaptive thinking ignores it). Effort never goes BELOW the configured one: the floor for legal and financial
reasoning stays where the operator set it. An explicit routing setting (``LLM_EFFORT_MEDIUM``…) still wins.

Every seat calls the model under its own purpose (``consejo:miembro:<id>``, tier ``mediano``) and the chair under
``consejo:presidente`` (``grande``): metered, budgeted and routed like every other call of the app. Who served each
seat is recorded as a :class:`~asistente.nucleo.consejo.contrato.Miembro` and revealed only in the ruling.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
import os
import threading
from typing import Any

from asistente.nucleo.agent.providers.base import LLMProvider
from asistente.nucleo.consejo.contrato import MAX_MIEMBROS, MIN_MIEMBROS, Miembro, Rol
from asistente.plataforma import settings

logger = logging.getLogger(__name__)

PREFIJO_MIEMBRO = "consejo:miembro:"
PROPOSITO_PRESIDENTE = "consejo:presidente"
FAMILIAS_CONOCIDAS = ("anthropic", "openai", "local")
# The deeper reasoning effort alternated with the configured one when a single family serves the whole council.
ESFUERZO_PROFUNDO = "xhigh"


def proposito_miembro(rol_id: str) -> str:
    return f"{PREFIJO_MIEMBRO}{rol_id}"


def _rol(rol_id: str, nombre: str, mandato: str) -> Rol:
    return Rol(id=rol_id, nombre=nombre, mandato=" ".join(mandato.split()), proposito=proposito_miembro(rol_id))


ROL_PRUDENTE = _rol(
    "prudente",
    "Prudencia y riesgo financiero",
    """Prudencia financiera y riesgo. Pesa por encima de todo lo que puede salir mal y lo que no tiene vuelta atrás:
    pérdidas posibles, volatilidad, endeudamiento, concentración del patrimonio, compromisos difíciles de deshacer y el
    peor escenario razonable según las cifras del expediente. Ante la duda, prefiere la opción que deja margen para
    corregir. No analices impuestos ni la liquidez del día a día: otros asientos se ocupan de ello.""",
)
ROL_FISCAL = _rol(
    "fiscal",
    "Fiscalidad y marco legal",
    """Consecuencias fiscales y legales de cada opción, SOLO con lo que dicen las fuentes del expediente ([F#]) y sus
    cifras. Nunca afirmes una norma, un plazo, un porcentaje o un límite que no esté en el expediente: si la decisión
    depende de una cuestión legal que el expediente no cubre, dilo como dato que falta. No valores el riesgo de mercado
    ni la comodidad de la familia: otros asientos se ocupan de ello.""",
)
ROL_LIQUIDEZ = _rol(
    "liquidez_familia",
    "Liquidez y bienestar familiar",
    """Liquidez y bienestar del hogar: el colchón para imprevistos, la capacidad de afrontar los gastos de cada mes,
    la dependencia de ingresos inciertos, la tranquilidad y la carga de gestión para la familia, y las restricciones que
    el hogar ha declarado. Pregúntate si esta familia puede vivir con cada opción durante los próximos años sin apuros.
    No analices impuestos ni rentabilidades de mercado: otros asientos se ocupan de ello.""",
)
ROL_ABOGADO = _rol(
    "abogado_del_diablo",
    "Abogado del diablo",
    """Abogado del diablo. Identifica la opción que parece más fuerte y construye el caso MÁS SÓLIDO en su contra, y el
    mejor caso a favor de la alternativa que parece más débil. Busca supuestos frágiles, datos que faltan y sesgos
    (optimismo, anclaje, aversión a la pérdida, seguir a la mayoría). Tu recomendación final puede coincidir con la
    opción fuerte si el caso en contra no se sostiene, pero tienes que haberlo intentado de verdad.""",
)
ROL_LARGO_PLAZO = _rol(
    "largo_plazo",
    "Largo plazo y patrimonio",
    """Visión a largo plazo: el efecto de cada opción dentro de diez o veinte años sobre el patrimonio, la jubilación
    y la flexibilidad futura, la inflación y los cambios previsibles en la familia (hijos, ingresos, vivienda). No
    repitas el análisis fiscal ni el de liquidez inmediata: otros asientos se ocupan de ello.""",
)
PRESIDENTE = Rol(
    id="presidente",
    nombre="Presidencia del consejo",
    mandato=(
        "Presidir el consejo: no vota ni añade su opinión. Sintetiza lo que el consejo ha deliberado, con el "
        "consenso y el disenso tal como son, y sin cifras que no estén en el expediente."
    ),
    proposito=PROPOSITO_PRESIDENTE,
)

ROLES_POR_DEFECTO: tuple[Rol, ...] = (ROL_PRUDENTE, ROL_FISCAL, ROL_LIQUIDEZ, ROL_ABOGADO)
ROLES_CONOCIDOS: dict[str, Rol] = {r.id: r for r in (*ROLES_POR_DEFECTO, ROL_LARGO_PLAZO)}
# Seats whose mandate needs to know which option leads after round 1 (to argue against it in the revision). The
# others never learn the provisional majority: it would pull them towards it.
ROLES_CONTRARIOS: frozenset[str] = frozenset({ROL_ABOGADO.id})


def roles_configurados(texto: str | None = None) -> tuple[Rol, ...]:
    """The seats named by ``CONSEJO_MIEMBROS`` (or ``texto``), in order; unknown ids are skipped with a warning.

    Fewer than ``MIN_MIEMBROS`` known seats → the default seats; more than ``MAX_MIEMBROS`` → the first ones.
    """
    valor = texto if texto is not None else str(getattr(settings, "CONSEJO_MIEMBROS", "") or "")
    roles: list[Rol] = []
    for parte in valor.split(","):
        clave = parte.strip().lower()
        if not clave:
            continue
        rol = ROLES_CONOCIDOS.get(clave)
        if rol is None:
            logger.warning("CONSEJO_MIEMBROS: unknown seat %r skipped (known: %s)", clave, ", ".join(ROLES_CONOCIDOS))
            continue
        if rol not in roles:
            roles.append(rol)
    if len(roles) < MIN_MIEMBROS:
        if valor.strip():
            logger.warning("CONSEJO_MIEMBROS names fewer than %d known seats: using the default seats", MIN_MIEMBROS)
        return ROLES_POR_DEFECTO
    return tuple(roles[:MAX_MIEMBROS])


# --------------------------------------------------------------------------------------------------------------
# Families
# --------------------------------------------------------------------------------------------------------------


def familia_canonica(nombre: str) -> str:
    valor = nombre.strip().lower()
    return "anthropic" if valor in ("anthropic", "claude") else valor


def familia_principal() -> str:
    return familia_canonica(str(getattr(settings, "LLM_PROVIDER", "anthropic") or "anthropic"))


def familia_disponible(familia: str) -> bool:
    """Whether ``familia`` can serve a seat, from settings and keys only (no network call)."""
    nombre = familia_canonica(familia)
    if nombre == familia_principal():
        return True  # the app already runs on it
    if nombre == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    if nombre == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    if nombre == "local":
        return bool(str(getattr(settings, "LOCAL_LLM_MODEL", "") or "").strip())
    return False


def familias_configuradas(
    familias: str | Sequence[str] | None = None,
    *,
    disponible: Callable[[str], bool] = familia_disponible,
) -> list[str]:
    """The available families of ``CONSEJO_FAMILIAS`` (or ``familias``), in order, without repeats.

    Empty, or none available → only the configured provider. A missing family is skipped with a log line.
    """
    if familias is None:
        familias = str(getattr(settings, "CONSEJO_FAMILIAS", "") or "")
    lista = familias.split(",") if isinstance(familias, str) else list(familias)
    elegidas: list[str] = []
    for nombre in lista:
        familia = familia_canonica(nombre)
        if not familia or familia in elegidas:
            continue
        if familia not in FAMILIAS_CONOCIDAS:
            logger.warning("CONSEJO_FAMILIAS: unknown family %r skipped", familia)
            continue
        if not disponible(familia):
            logger.info("Council family %s is not configured here: skipped", familia)
            continue
        elegidas.append(familia)
    return elegidas or [familia_principal()]


@dataclass(frozen=True)
class Asiento:
    """One seat filled for the sessions of an engine: its role, family and reasoning effort (None = configured)."""

    rol: Rol
    familia: str
    esfuerzo: str | None = None


def asignar_asientos(
    roles: Sequence[Rol],
    familias: Sequence[str],
    *,
    esfuerzo_base: str | None = None,
) -> list[Asiento]:
    """Round-robin the seats over ``familias`` (a role's available preferred family wins); one family → efforts.

    Deterministic: the same roles and families always give the same seats.
    """
    if not familias:
        raise ValueError("hace falta al menos una familia de modelos")
    asientos: list[Asiento] = []
    turno = 0
    for indice, rol in enumerate(roles):
        preferida = familia_canonica(rol.familia_preferida) if rol.familia_preferida else None
        familia: str
        if preferida is not None and preferida in familias:
            familia = preferida
        else:
            familia = familias[turno % len(familias)]
            turno += 1
        esfuerzo: str | None = None
        if len(familias) == 1 and familia == "anthropic" and indice % 2 == 1:
            base = (esfuerzo_base or str(getattr(settings, "ANTHROPIC_EFFORT", "high") or "high")).lower()
            if base == "max":
                esfuerzo = None  # already the deepest: nothing deeper to alternate with
            elif base == ESFUERZO_PROFUNDO:
                esfuerzo = "max"
            else:
                esfuerzo = ESFUERZO_PROFUNDO
        asientos.append(Asiento(rol=rol, familia=familia, esfuerzo=esfuerzo))
    return asientos


CrearProveedor = Callable[[str, str | None], LLMProvider]


def _crear_por_defecto(familia: str, esfuerzo: str | None) -> LLMProvider:
    from asistente.nucleo.agent.providers import create_provider

    return create_provider(familia, effort=esfuerzo)


def _medir(proveedor: LLMProvider, proposito: str) -> LLMProvider:
    from asistente.plataforma.ops.consumo import medir

    return medir(proveedor, proposito)


class FabricaProveedores:
    """``proveedor_para(proposito)`` for the council: the seat's family and effort, metered under its purpose.

    Base providers are built lazily (no network at construction) and cached per (family, effort); the metered
    wrapper per purpose. ``crear`` and ``disponible`` are injectable (tests, a pinned model); ``medir=False`` hands
    the base provider out as is (tests).
    """

    def __init__(
        self,
        roles: Sequence[Rol],
        *,
        presidente: Rol = PRESIDENTE,
        familias: str | Sequence[str] | None = None,
        disponible: Callable[[str], bool] = familia_disponible,
        crear: CrearProveedor | None = None,
        medir: bool = True,
    ) -> None:
        self.familias = familias_configuradas(familias, disponible=disponible)
        self.asientos: dict[str, Asiento] = {a.rol.proposito: a for a in asignar_asientos(roles, self.familias)}
        principal = familia_principal()
        self.familia_presidente = principal if principal in self.familias else self.familias[0]
        self.asientos[presidente.proposito] = Asiento(rol=presidente, familia=self.familia_presidente)
        self._crear = crear or _crear_por_defecto
        self._medir = medir
        self._lock = threading.Lock()
        self._bases: dict[tuple[str, str | None], LLMProvider] = {}
        self._medidos: dict[str, LLMProvider] = {}

    def asiento(self, proposito: str) -> Asiento | None:
        return self.asientos.get(proposito)

    def __call__(self, proposito: str) -> LLMProvider:
        with self._lock:
            if proposito in self._medidos:
                return self._medidos[proposito]
            asiento = self.asientos.get(proposito)
            clave = (asiento.familia, asiento.esfuerzo) if asiento else (self.familia_presidente, None)
            base = self._bases.get(clave)
            if base is None:
                base = self._crear(*clave)
                self._bases[clave] = base
            proveedor = _medir(base, proposito) if self._medir else base
            self._medidos[proposito] = proveedor
            return proveedor


def miembro_de(rol: Rol, proveedor: Any) -> Miembro:
    """Who actually served ``rol`` (the routed provider's name and model)."""
    return Miembro(
        rol=rol,
        proveedor=str(getattr(proveedor, "name", "") or "desconocido"),
        modelo=str(getattr(proveedor, "model", "") or "desconocido"),
    )


__all__ = [
    "ESFUERZO_PROFUNDO",
    "FAMILIAS_CONOCIDAS",
    "PREFIJO_MIEMBRO",
    "PRESIDENTE",
    "PROPOSITO_PRESIDENTE",
    "ROLES_CONOCIDOS",
    "ROLES_CONTRARIOS",
    "ROLES_POR_DEFECTO",
    "ROL_ABOGADO",
    "ROL_FISCAL",
    "ROL_LARGO_PLAZO",
    "ROL_LIQUIDEZ",
    "ROL_PRUDENTE",
    "Asiento",
    "CrearProveedor",
    "FabricaProveedores",
    "asignar_asientos",
    "familia_canonica",
    "familia_disponible",
    "familia_principal",
    "familias_configuradas",
    "miembro_de",
    "proposito_miembro",
    "roles_configurados",
]
