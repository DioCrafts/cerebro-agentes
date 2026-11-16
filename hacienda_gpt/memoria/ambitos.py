"""Which ámbito a proposal is about: notices of the inbox, levers of the tax optimiser, pending actions.

The same names are used by every capture path (the agent tool, the buttons, the settings screen) and every
use (the inbox filter, the optimiser adapter, the agent's context), so a «no» said in the chat holds back
the notice, and a «No me interesa» pressed on a notice holds back the same lever in the optimiser.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from hacienda_gpt.memoria.modelo import ambito_valido

# Known ámbitos, with a Spanish label for the user (and the agent tool's description). Narrower ones
# («…:conyuge», «…:<oferta>») are accepted too; a whole module («luz») covers all its proposals.
CATALOGO: dict[str, str] = {
    "fiscal": "Cualquier propuesta fiscal",
    "fiscal:plan_pensiones": "Aportar a planes de pensiones",
    "fiscal:plan_pensiones:contribuyente": "Aportar a tu plan de pensiones",
    "fiscal:plan_pensiones:conyuge": "Aportar al plan de pensiones de tu cónyuge",
    "fiscal:donativo": "Hacer donativos para deducir",
    "fiscal:propuesta": "Cifras que proponen los módulos para la declaración",
    "fiscal:propuesta:hipoteca": "Usar los pagos de la hipoteca para la deducción por vivienda",
    "fiscal:perdidas": "Vender inversiones con pérdidas para compensar",
    "fiscal:perdidas:contribuyente": "Vender tus inversiones con pérdidas",
    "fiscal:perdidas:conyuge": "Vender inversiones con pérdidas de tu cónyuge",
    "fiscal:tributacion:conjunta": "Declaración conjunta",
    "fiscal:tributacion:individual": "Declaraciones individuales",
    "fiscal:plan_fin_de_ejercicio": "El plan de fin de ejercicio",
    "fiscal:plazo": "Recordatorios de plazos fiscales",
    "fiscal:cambio_normativo": "Avisos de cambios normativos",
    "luz": "Cualquier propuesta sobre la luz",
    "luz:cambio_tarifa": "Cambiar de tarifa de la luz",
    "luz:fin_precio": "Avisos de fin del precio garantizado de la luz",
    "luz:fin_permanencia": "Avisos de fin de permanencia de la luz",
    "suscripciones": "Cualquier propuesta sobre suscripciones",
    "suscripciones:renovacion": "Avisos de renovación de suscripciones",
    "suscripciones:subida": "Avisos de subidas de precio de suscripciones",
    "suscripciones:duplicado": "Avisos de suscripciones duplicadas",
    "suscripciones:prueba": "Avisos de fin de periodos de prueba",
    "hipoteca": "Cualquier propuesta sobre la hipoteca",
    "hipoteca:amortizar": "Amortizar anticipadamente la hipoteca",
    "hipoteca:amortizar:plazo": "Amortizar la hipoteca reduciendo el plazo",
    "hipoteca:amortizar:cuota": "Amortizar la hipoteca reduciendo la cuota",
    "hipoteca:revision": "Avisos de revisión del tipo de la hipoteca",
    "hipoteca:comision": "Avisos sobre comisiones de amortización",
    "hipoteca:indice": "Avisos de nuevos valores del índice",
    "ayudas": "Cualquier propuesta de ayudas públicas",
    "ayudas:prestacion": "Prestaciones que podrían corresponderte",
    "ayudas:convocatoria": "Convocatorias de ayudas",
    "caducidades": "Avisos de caducidades y garantías",
}

# The lever a module's notice type proposes, when its name in the notice key is not the lever's.
_RENOMBRES: dict[tuple[str, str], str] = {("luz", "ahorro"): "cambio_tarifa"}
# Proposals the buttons answer at the level of the lever, not of the one offer: «no quiero cambiar de
# tarifa» means any tariff (the offer is kept in the decision's ``propuesta``; a much better one may still
# come back once). Every other notice is answered for its own subject (one subscription, one deadline).
_RESPUESTA_A_LA_PALANCA = ("luz:cambio_tarifa",)

_FECHA = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$|^\d{4}-W\d{2}$")
_SEGMENTO = re.compile(r"[^A-Za-z0-9_.\-]+")

# The optimiser's lever ids (``PalancaElegida.id``) and the ámbito each one is about.
PALANCAS_OPTIMIZADOR: dict[str, str] = {
    "plan_pensiones_contribuyente": "fiscal:plan_pensiones:contribuyente",
    "plan_pensiones_conyuge": "fiscal:plan_pensiones:conyuge",
    "perdidas_contribuyente": "fiscal:perdidas:contribuyente",
    "perdidas_conyuge": "fiscal:perdidas:conyuge",
    "donativo": "fiscal:donativo",
}


@dataclass(frozen=True)
class AmbitoAviso:
    # The notice's own subject (what a decision must cover to hold it back).
    propuesta: str
    # What a button on it records (the lever for a few proposal types, the subject otherwise).
    respuesta: str


def _segmento(texto: str) -> str:
    return _SEGMENTO.sub("-", texto).strip("-")[:80] or "x"


def etiqueta(ambito: str) -> str:
    """A Spanish label for an ámbito (the closest known one, plus the detail)."""
    if ambito in CATALOGO:
        return CATALOGO[ambito]
    partes = ambito.split(":")
    for n in range(len(partes) - 1, 0, -1):
        base = ":".join(partes[:n])
        if base in CATALOGO:
            return f"{CATALOGO[base]} ({':'.join(partes[n:])})"
    return ambito


def ambito_de_palanca(palanca_id: str, opcion: str | None = None) -> str:
    """The ámbito of a lever of the optimiser's plan."""
    if palanca_id == "tributacion":
        return "fiscal:tributacion:" + ("conjunta" if opcion and opcion.startswith("conjunta") else "individual")
    return PALANCAS_OPTIMIZADOR.get(palanca_id, f"fiscal:{_segmento(palanca_id)}")


def ambito_de_aviso(kind: str, key: str) -> AmbitoAviso | None:
    """The ámbito of an inbox notice; None for the weekly digest (never held back by a decision)."""
    if kind == "resumen_semanal":
        return None
    if kind.startswith("modulo:"):
        clave = kind.removeprefix("modulo:")
        resto = key.removeprefix(f"modulo:{clave}:").removeprefix(f"{clave}:")
        segmentos = [_segmento(s) for s in resto.split(":") if s and not _FECHA.match(s)]
        tipo = segmentos[0] if segmentos else "aviso"
        tipo = _RENOMBRES.get((clave, tipo), tipo)
        propuesta = ":".join([clave, tipo, *segmentos[1:2]])
        palanca = f"{clave}:{tipo}"
        respuesta = palanca if palanca in _RESPUESTA_A_LA_PALANCA else propuesta
    elif kind == "plazo":
        propuesta = respuesta = "fiscal:plazo:" + ":".join(_segmento(s) for s in key.removeprefix("plazo:").split(":"))
    elif kind == "fin_de_ejercicio":
        propuesta = respuesta = "fiscal:plan_fin_de_ejercicio"
    elif kind == "cambio_normativo":
        resto = key.split(":", 1)[1] if ":" in key else key
        propuesta = respuesta = "fiscal:cambio_normativo:" + _segmento(resto.split(":", 1)[0])
    else:
        propuesta = respuesta = f"fiscal:{_segmento(kind)}"
    propuesta = ":".join(propuesta.split(":")[:5])
    respuesta = ":".join(respuesta.split(":")[:5])
    if not ambito_valido(propuesta) or not ambito_valido(respuesta):
        return None
    return AmbitoAviso(propuesta=propuesta, respuesta=respuesta)


def ambito_de_accion(modulo: str, herramienta: str) -> str:
    """The ámbito of a module's action (a tool that acts outside the app)."""
    nombre = herramienta.removeprefix(f"{modulo}_")
    return f"{_segmento(modulo).lower()}:accion:{_segmento(nombre)}"


__all__ = [
    "CATALOGO",
    "PALANCAS_OPTIMIZADOR",
    "AmbitoAviso",
    "ambito_de_accion",
    "ambito_de_aviso",
    "ambito_de_palanca",
    "etiqueta",
]
