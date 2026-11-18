"""Writing the digest: the small model behind the gate, and the deterministic rendering that is always there.

* :func:`datos_para_modelo` — the ranked points as DATA for the model, inside ``<datos_repaso>``: titles (already
  neutralised when they come from mail or a tool's arguments, see ``senales.limpiar``), facts written in Spanish
  exactly as the text must copy them, the reasons code computed, the proposals' ids. Never a notice's body.
* :func:`redactar` — one call (purpose ``cerebro:repaso``, the routing table's small tier); the gate
  (:mod:`~asistente.nucleo.repaso.puerta`); on violations ONE repair with the violations listed; then
  :func:`rescatar`: the items that passed keep the model's words, the others get the deterministic text.
  A provider error (budget included) gives the deterministic rendering.
* :func:`determinista` — good Spanish built only from the points' facts and reasons (it passes the gate by
  construction; a test checks it).
* :func:`cuerpo` — the notification body: plain text with light markdown, readable in the inbox, by e-mail and in a
  webhook, listing each point's sources by their own titles (so a decision that removes a notice also removes the
  digest that cites it, ``memoria.servicio.retirar_avisos``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
import json
import logging
from typing import Any, Literal

from asistente.nucleo.agent.providers.base import ProviderError
from asistente.nucleo.repaso.priorizacion import Priorizacion, Punto, cuando, eur, fecha_es
from asistente.nucleo.repaso.prompts import REPARACION, SCHEMA, SCHEMA_NAME, prompt_repaso
from asistente.nucleo.repaso.puerta import (
    ItemRedactado,
    Permitidos,
    Redaccion,
    Veredicto,
    Violacion,
    revisar,
)
from asistente.nucleo.repaso.senales import Senal, limpiar

logger = logging.getLogger(__name__)

Frecuencia = Literal["semanal", "diaria"]
MAX_RESPUESTA_ANTERIOR = 6000


@dataclass(frozen=True)
class Periodo:
    """The period a pass covers: its id (``2026-W47`` / ``2026-11-18``) and its inbox key."""

    frecuencia: Frecuencia
    clave: str
    key: str
    inicio: date
    fin: date

    @property
    def semanal(self) -> bool:
        return self.frecuencia == "semanal"

    def etiqueta(self) -> str:
        if self.semanal:
            return f"semana del {fecha_es(self.inicio)} al {fecha_es(self.fin)}"
        return f"día {fecha_es(self.inicio)}"

    def permitidos(self) -> Permitidos:
        p = Permitidos()
        p.anadir_fecha(self.inicio)
        p.anadir_fecha(self.fin)
        return p


def periodo_de(hoy: date, frecuencia: str) -> Periodo:
    """The weekly digest's period and key (``resumen_semanal:2026-W47``, as ``bandeja.clave_semana``), or one per
    day (``resumen_semanal:2026-11-18``: the same kind, so the inbox and the decisions treat it alike)."""
    from asistente.nucleo.proactive.bandeja import clave_semana

    if frecuencia == "diaria":
        return Periodo("diaria", hoy.isoformat(), f"resumen_semanal:{hoy.isoformat()}", hoy, hoy)
    anio, semana, _ = hoy.isocalendar()
    lunes = hoy - timedelta(days=hoy.weekday())
    return Periodo("semanal", f"{anio}-W{semana:02d}", clave_semana(hoy), lunes, lunes + timedelta(days=6))


# --------------------------------------------------------------------------- #
# The data the model reads
# --------------------------------------------------------------------------- #


def _hechos(senal: Senal) -> dict[str, Any]:
    h = senal.hechos
    datos: dict[str, Any] = {}
    if h.importe is not None:
        datos["importe"] = eur(h.importe)
    if h.vence is not None:
        datos["vence"] = fecha_es(h.vence)
        if h.dias is not None:
            datos["dias_para_vencer"] = h.dias
    if h.fecha is not None:
        datos["fecha"] = fecha_es(h.fecha)
    for nombre, valor in h.numeros.items():
        datos[nombre] = int(valor) if valor == valor.to_integral_value() else str(valor).replace(".", ",")
    for nombre, dia in h.fechas.items():
        datos[nombre] = fecha_es(dia)
    return datos


def _punto_como_datos(punto: Punto, nombres: Mapping[str, str]) -> dict[str, Any]:
    lider = punto.lider
    datos: dict[str, Any] = {
        "punto_id": punto.id,
        "rango": punto.rango,
        "titulo": limpiar(punto.titulo),
        "ambitos": [nombres.get(a, a) for a in punto.ambitos],
        "urgente": punto.urgente,
        "hechos": _hechos(lider),
        "motivos": [limpiar(m, 300) for m in punto.por_que],
    }
    if lider.no_confiable:
        datos["origen_externo"] = True
    consecuencia = next((s.consecuencia for s in punto.senales if s.consecuencia), None)
    if consecuencia:
        datos["consecuencia"] = limpiar(consecuencia, 600)
    if punto.propuestas:
        datos["propuestas"] = [{"id": p.id, "que_hace": limpiar(p.resumen, 400)} for p in punto.propuestas]
    otros = [
        {"titulo": limpiar(s.titulo), "ambito": nombres.get(s.ambito, s.ambito), "hechos": _hechos(s)}
        for s in punto.senales[1:4]
    ]
    if otros:
        datos["relacionados"] = otros
    return datos


def datos_para_modelo(prio: Priorizacion, *, periodo: Periodo, hoy: date, nombres: Mapping[str, str]) -> str:
    cuerpo = {
        "periodo": periodo.etiqueta(),
        "hoy": fecha_es(hoy),
        "puntos": [_punto_como_datos(p, nombres) for p in prio.puntos],
        "avisos_que_no_caben": prio.restantes,
    }
    return (
        "<datos_repaso>\n"
        + json.dumps(cuerpo, ensure_ascii=False, indent=1)
        + "\n</datos_repaso>\nEscribe el repaso con estos datos y solo con ellos."
    )


# --------------------------------------------------------------------------- #
# The deterministic rendering
# --------------------------------------------------------------------------- #


def _frase(texto: str) -> str:
    texto = texto.strip().rstrip(".")
    return f"{texto}." if texto else ""


# What a point says when its facts give nothing to say (by the leading signal's kind).
_SIN_DATOS = {
    "aviso": "Lo tienes abierto en tu bandeja.",
    "accion": "Está esperando tu confirmación.",
    "tarea": "La tarea está a la espera de que decidas.",
    "paso": "La tarea espera a que confirmes este paso.",
    "propuesta_correo": "Lo encontré en tu correo; solo se guarda si lo confirmas.",
    "decision": "Lo tengo en cuenta para no repetirte lo que ya decidiste.",
    "novedad": "Es lo que ha cambiado desde el último repaso.",
}


def texto_determinista(punto: Punto) -> str:
    """What the point is about, from its facts only (the title is shown next to it, so it is not repeated)."""
    lider = punto.lider
    h = lider.hechos
    partes: list[str] = []
    if h.vence is not None and h.dias is not None:
        if h.dias >= 0:
            partes.append(f"Vence el {fecha_es(h.vence)} ({cuando(h.dias)}).")
        else:
            partes.append(f"Su plazo terminó el {fecha_es(h.vence)}.")
    if h.importe is not None and h.importe != 0:
        partes.append(f"Importe que indica su módulo: {eur(h.importe)}.")
    if not partes:
        partes.append(_SIN_DATOS.get(lider.tipo, _SIN_DATOS["aviso"]))
    if len(punto.senales) > 1:
        otros = len(punto.senales) - 1
        partes.append(f"Lo agrupo con {otros} aviso{'s' if otros != 1 else ''} más sobre lo mismo.")
    if punto.propuestas:
        partes.append("Si te parece bien, confírmalo con su botón: no se hará nada hasta que lo hagas.")
    texto = " ".join(partes)
    return texto if len(texto) <= 420 else texto[:419].rstrip() + "…"


def por_que_determinista(punto: Punto) -> str:
    """The point's reasons, whole (never cut in the middle of one), up to the field's length."""
    elegidos: list[str] = []
    for motivo in punto.por_que:
        candidato = "; ".join([*elegidos, motivo.rstrip(".")]) + "."
        if len(candidato) > 300:
            continue
        elegidos.append(motivo.rstrip("."))
        if len(elegidos) == 3:
            break
    if not elegidos:
        return "Lo indica su módulo."
    texto = "; ".join(elegidos) + "."
    return texto[0].upper() + texto[1:]


def titular_determinista(prio: Priorizacion, periodo: Periodo) -> str:
    cuando_txt = "esta semana" if periodo.semanal else "hoy"
    if not prio.puntos:
        return f"Nada urgente {cuando_txt}."
    primero = limpiar(prio.puntos[0].titulo, 120).rstrip(".")
    n = len(prio.puntos)
    if prio.puntos[0].urgente:
        return f"Lo más urgente {cuando_txt}: {primero}."
    if n == 1:
        return f"Una cosa que mirar {cuando_txt}: {primero}."
    return f"{n} asuntos que mirar {cuando_txt}; el primero: {primero}."


def item_determinista(punto: Punto) -> ItemRedactado:
    return ItemRedactado(
        punto_id=punto.id,
        texto=texto_determinista(punto),
        por_que=por_que_determinista(punto),
        propuesta_id=punto.propuestas[0].id if punto.propuestas else None,
        origen="determinista",
    )


def determinista(prio: Priorizacion, periodo: Periodo) -> Redaccion:
    return Redaccion(
        titular=titular_determinista(prio, periodo),
        items=[item_determinista(p) for p in prio.puntos],
        origen="determinista",
        titular_origen="determinista",
    )


# --------------------------------------------------------------------------- #
# The model, the gate, one repair, the rescue
# --------------------------------------------------------------------------- #


@dataclass
class ResultadoRedaccion:
    redaccion: Redaccion
    intentos: int = 0
    violaciones: list[Violacion] = field(default_factory=list)
    error: str | None = None


def revisar_salida(salida: Any, prio: Priorizacion, periodo: Periodo) -> Veredicto:
    return revisar(salida, prio.puntos, extra_titular=periodo.permitidos(), restantes=prio.restantes)


def componer(veredicto: Veredicto, salida: Mapping[str, Any], prio: Priorizacion) -> Redaccion:
    """A fully accepted output, in the model's (accepted) order."""
    return Redaccion(
        titular=" ".join(str(salida["titular"]).split()),
        items=[veredicto.items_ok[pid] for pid in veredicto.orden],
        origen="modelo",
        titular_origen="modelo",
    )


def rescatar(veredicto: Veredicto, salida: Mapping[str, Any] | None, prio: Priorizacion, periodo: Periodo) -> Redaccion:
    """What survives a failed gate: items that passed keep the model's words, the rest are deterministic; the
    model's order only when it was acceptable; the headline only when it passed."""
    orden = veredicto.orden if veredicto.orden_ok else [p.id for p in prio.puntos]
    por_id = {p.id: p for p in prio.puntos}
    items = [veredicto.items_ok.get(pid) or item_determinista(por_id[pid]) for pid in orden]
    titular_ok = veredicto.titular_ok and isinstance(salida, Mapping)
    modelo = sum(1 for i in items if i.origen == "modelo") + int(titular_ok)
    return Redaccion(
        titular=(
            " ".join(str(salida["titular"]).split()) if titular_ok and salida else titular_determinista(prio, periodo)
        ),
        items=items,
        origen="determinista" if modelo == 0 else "mixto",
        titular_origen="modelo" if titular_ok else "determinista",
    )


def _valor(veredicto: Veredicto) -> int:
    return len(veredicto.items_ok) * 2 + int(veredicto.titular_ok) + int(veredicto.orden_ok)


def _llamar(provider: Any, *, system: str, user: str) -> dict[str, Any]:
    datos = provider.complete_json(system=system, user=user, schema=SCHEMA, schema_name=SCHEMA_NAME)
    if not isinstance(datos, dict):
        raise ProviderError("La respuesta del modelo no es un objeto JSON.", salida_invalida=True)
    return datos


def mensaje_reparacion(veredicto: Veredicto, salida: Any) -> str:
    lista = "\n".join(f"- {v.detalle}" for v in veredicto.violaciones[:20])
    anterior = json.dumps(salida, ensure_ascii=False)[:MAX_RESPUESTA_ANTERIOR]
    return REPARACION % (lista, anterior)


def revisar_y_reparar(
    provider: Any | None,
    salida: Any,
    prio: Priorizacion,
    periodo: Periodo,
    *,
    system: str,
    user: str,
    intentos: int = 1,
) -> ResultadoRedaccion:
    """Gate ``salida``; on violations, one repair through ``provider`` (when there is one), then the rescue."""
    veredicto = revisar_salida(salida, prio, periodo)
    if veredicto.ok:
        return ResultadoRedaccion(componer(veredicto, salida, prio), intentos=intentos)
    mejor, mejor_salida = veredicto, salida
    error: str | None = None
    if provider is not None:
        try:
            reparada = _llamar(provider, system=system, user=user + "\n\n" + mensaje_reparacion(veredicto, salida))
            intentos += 1
            segundo = revisar_salida(reparada, prio, periodo)
            if segundo.ok:
                return ResultadoRedaccion(
                    componer(segundo, reparada, prio), intentos=intentos, violaciones=veredicto.violaciones
                )
            if _valor(segundo) >= _valor(veredicto):
                mejor, mejor_salida = segundo, reparada
        except ProviderError as exc:
            intentos += 1
            error = str(exc)[:300]
            logger.info("The repair of the pass failed: %s", exc)
    logger.info("Pass text rejected by the gate (%d violations); rescued", len(mejor.violaciones))
    return ResultadoRedaccion(
        rescatar(mejor, mejor_salida if isinstance(mejor_salida, Mapping) else None, prio, periodo),
        intentos=intentos,
        violaciones=mejor.violaciones,
        error=error,
    )


def redactar(
    provider: Any | None,
    prio: Priorizacion,
    *,
    periodo: Periodo,
    hoy: date,
    nombres: Mapping[str, str],
) -> ResultadoRedaccion:
    """The digest's text. Without a provider, or when it fails, the deterministic rendering."""
    if provider is None or not prio.puntos:
        return ResultadoRedaccion(determinista(prio, periodo))
    system = prompt_repaso()
    user = datos_para_modelo(prio, periodo=periodo, hoy=hoy, nombres=nombres)
    try:
        salida = _llamar(provider, system=system, user=user)
    except ProviderError as exc:
        logger.info("The pass's model call failed: %s", exc)
        return ResultadoRedaccion(determinista(prio, periodo), intentos=1, error=str(exc)[:300])
    return revisar_y_reparar(provider, salida, prio, periodo, system=system, user=user)


def peticion_lote(nativo: Any, prio: Priorizacion, *, periodo: Periodo, hoy: date, nombres: Mapping[str, str]) -> Any:
    """The provider-native request of the same call, for the batch API (``ProveedorDeLotes.peticion_json``)."""
    return nativo.peticion_json(
        system=prompt_repaso(),
        user=datos_para_modelo(prio, periodo=periodo, hoy=hoy, nombres=nombres),
        schema=SCHEMA,
        schema_name=SCHEMA_NAME,
    )


# --------------------------------------------------------------------------- #
# The notification
# --------------------------------------------------------------------------- #


def _fuente(senal: Senal, nombres: Mapping[str, str]) -> str:
    modulo = senal.fuente.modulo or senal.ambito
    texto = f"[{nombres.get(modulo, modulo)}] {senal.fuente.titulo or senal.titulo}"
    if senal.fuente.fecha is not None:
        texto += f" ({fecha_es(senal.fuente.fecha)})"
    return texto


def titulo_notificacion(prio: Priorizacion, periodo: Periodo) -> str:
    n = len(prio.puntos)
    urgentes = sum(1 for p in prio.puntos if p.urgente)
    base = "Resumen semanal" if periodo.semanal else "Resumen del día"
    titulo = f"{base}: {n} asunto{'s' if n != 1 else ''} que importa{'n' if n != 1 else ''}"
    if urgentes:
        titulo += f", {urgentes} urgente{'s' if urgentes != 1 else ''}"
    return titulo


def cuerpo(prio: Priorizacion, redaccion: Redaccion, *, nombres: Mapping[str, str]) -> str:
    por_id = {p.id: p for p in prio.puntos}
    lineas = [redaccion.titular, ""]
    for numero, item in enumerate(redaccion.items, start=1):
        punto = por_id[item.punto_id]
        lineas.append(f"{numero}. **{punto.titulo}** — {item.texto}")
        lineas.append(f"   Por qué: {item.por_que}")
        fuentes = list(dict.fromkeys(_fuente(s, nombres) for s in punto.senales))
        lineas.append("   Fuente: " + " · ".join(fuentes[:4]))
        for propuesta in punto.propuestas:
            lineas.append(f"   Propuesta: {propuesta.resumen} Confírmala o descártala con su botón en la app.")
    if prio.restantes:
        lineas += ["", f"Y {prio.restantes} aviso{'s' if prio.restantes != 1 else ''} más en tu bandeja."]
    lineas += [
        "",
        "No he hecho nada por mi cuenta: cada propuesta espera a que la confirmes. Cada importe es el que calcula "
        "su módulo para ese aviso; no se suman entre sí.",
    ]
    return "\n".join(lineas)


__all__ = [
    "Frecuencia",
    "Periodo",
    "ResultadoRedaccion",
    "cuerpo",
    "datos_para_modelo",
    "determinista",
    "item_determinista",
    "mensaje_reparacion",
    "peticion_lote",
    "periodo_de",
    "redactar",
    "rescatar",
    "revisar_salida",
    "revisar_y_reparar",
    "titular_determinista",
    "titulo_notificacion",
]
