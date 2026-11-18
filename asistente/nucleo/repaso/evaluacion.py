"""The proactive pass as the evaluation runs it (``asistente.nucleo.evaluacion.ejecutores``, requirement ``repaso``).

``ejecutar(entrada, *, proveedor)`` takes a case's ``entrada_repaso`` — ``{"hoy", "senales": [{"id", "tipo",
"titulo", "detalle", "fecha", "importe"}]}``: what is new since the last pass, as plain data — and runs the SAME
pipeline the job runs, without any database: the signals become :class:`~asistente.nucleo.repaso.senales.Senal`,
the deterministic priority ranks them (:func:`~asistente.nucleo.repaso.priorizacion.priorizar`), the model behind
the gate writes (:func:`~asistente.nucleo.repaso.redaccion.redactar`; the deterministic rendering on any provider
error, which is what the suite's replay mode gives). It returns what the evaluation reads (``SalidaRepaso``):

* ``texto`` — the digest as the user would read it (the notification body);
* ``afirmaciones`` — the headline and one claim per point, each with the ids of the signals it rests on (a point's
  signals; the headline's, the points it can be traced to — their title, amount or date — else the first one);
* ``propuestas`` — one per proposal a point carries, with the signal that carries it.

How the evaluation's signal kinds map (fixed table, :data:`TIPOS`): ``plazo`` is an official, irreversible deadline;
``caducidad``, ``luz``, ``hipoteca``, ``propuesta``… are notices of their domain whose ``fecha`` is their deadline;
``boe`` is a change in the law (its ``fecha`` is when it was published); ``evento`` and ``decision`` are context;
``correo`` without a date or an amount is classified mail (context, like the bus's ``correo.clasificado``: the mail
module writes no notice for it), with one it is a mail notice (untrusted text). ``detalle`` is part of the signal's
title (its figures are the signal's). Every signal is new (it is the input of the pass).

**Proposals.** In the product a proposal exists only when the layer that owns it produced one (a pending action, a
proposed task, a step waiting for the user, a mail proposal, a template its notice's ámbito declares). The
evaluation's signals are data with no such layer behind them, so a pass over them proposes nothing — which is exactly
what these cases check (``propuestas_solo_de``, ``max_propuestas``): the model can never add a proposal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from asistente.nucleo.repaso.priorizacion import Priorizacion, Punto, eur, fecha_es, priorizar
from asistente.nucleo.repaso.puerta import plano
from asistente.nucleo.repaso.redaccion import cuerpo, periodo_de, redactar
from asistente.nucleo.repaso.senales import Fuente, Hechos, Senal, limpiar

# Evaluation signal kind -> (domain, signal kind, the date is a deadline, official deadline, untrusted text).
TIPOS: dict[str, tuple[str, str, bool, bool, bool]] = {
    "plazo": ("fiscal", "aviso", True, True, False),
    "caducidad": ("caducidades", "aviso", True, False, False),
    "luz": ("luz", "aviso", True, False, False),
    "hipoteca": ("hipoteca", "aviso", True, False, False),
    "suscripcion": ("suscripciones", "aviso", True, False, False),
    "ayuda": ("ayudas", "aviso", True, False, False),
    "propuesta": ("hogar", "aviso", True, False, False),
    "tarea": ("tareas", "tarea", True, False, False),
    "correo": ("correo", "aviso", True, False, True),
    "boe": ("fiscal", "aviso", False, False, False),
    "evento": ("eventos", "novedad", False, False, False),
    "decision": ("memoria", "decision", True, False, False),
}
POR_DEFECTO = ("hogar", "aviso", True, False, False)
SIN_NOVEDADES = "Esta semana no hay nada que necesite tu atención."


def _importe(valor: Any) -> Decimal | None:
    if valor in (None, ""):
        return None
    try:
        numero = Decimal(str(valor).strip().replace(",", "."))
    except InvalidOperation:
        return None
    return numero if numero.is_finite() else None


def a_senal(datos: Mapping[str, Any], *, hoy: date) -> Senal:
    """One evaluation signal as a pass signal (see :data:`TIPOS`)."""
    tipo_eval = str(datos.get("tipo") or "").strip().lower()
    ambito, tipo, fecha_es_plazo, oficial, externo = TIPOS.get(tipo_eval, POR_DEFECTO)
    fecha = datos.get("fecha")
    dia = date.fromisoformat(fecha) if isinstance(fecha, str) and fecha else fecha if isinstance(fecha, date) else None
    importe = _importe(datos.get("importe"))
    if tipo_eval == "correo" and dia is None and importe is None:
        tipo = "novedad"  # classified mail: context, never a notice of its own
    titulo = str(datos.get("titulo") or "").strip()
    detalle = str(datos.get("detalle") or "").strip()
    texto = limpiar(f"{titulo} — {detalle}" if detalle else titulo, 300) or "Novedad"
    hechos = Hechos(importe=importe)
    if dia is not None and fecha_es_plazo:
        hechos.vence, hechos.dias = dia, (dia - hoy).days
    elif dia is not None:
        hechos.fecha = dia
    id_ = str(datos["id"])
    return Senal(
        id=id_,
        ambito=ambito,
        tipo=tipo,  # type: ignore[arg-type]
        titulo=texto,
        hechos=hechos,
        fuente=Fuente(tipo="aviso", id=id_, modulo=ambito, fecha=hechos.fecha, titulo=texto),
        nueva=True,
        no_confiable=externo,
        oficial=oficial,
        irreversible=oficial,
        normativa=tipo_eval == "boe",
    )


def _del_titular(titular: str, puntos: Sequence[Punto]) -> list[str]:
    """The signals the headline can be traced to: a point whose title, amount or date it states; else the first."""
    texto = plano(titular)
    trazados: list[str] = []
    for punto in puntos:
        lider = punto.lider
        marcas = [plano(punto.titulo.split(" — ")[0]).rstrip(".")[:60]]
        if lider.hechos.importe is not None:
            marcas.append(plano(eur(lider.hechos.importe)))
        if lider.hechos.vence is not None:
            marcas.append(fecha_es(lider.hechos.vence))
        if any(m and m in texto for m in marcas):
            trazados.extend(s.id for s in punto.senales)
    if not trazados and puntos:
        trazados = [s.id for s in puntos[0].senales]
    return list(dict.fromkeys(trazados))


def _salida(prio: Priorizacion, titular: str, items: Sequence[Any], texto: str) -> dict[str, Any]:
    por_id = {p.id: p for p in prio.puntos}
    afirmaciones: list[dict[str, Any]] = [{"texto": titular, "senales": _del_titular(titular, prio.puntos)}]
    propuestas: list[dict[str, Any]] = []
    for item in items:
        punto = por_id[item.punto_id]
        afirmaciones.append(
            {"texto": f"{punto.titulo}: {item.texto} {item.por_que}", "senales": [s.id for s in punto.senales]}
        )
        for propuesta in punto.propuestas:
            portadora = next((s.id for s in punto.senales if s.propuesta is not None and s.propuesta.id == propuesta.id), None)
            propuestas.append({"texto": propuesta.resumen, "senal": portadora})
    return {"texto": texto, "afirmaciones": afirmaciones, "propuestas": propuestas}


def ejecutar(entrada: Mapping[str, Any], *, proveedor: Any) -> dict[str, Any]:
    """The pass over an evaluation case's signals (see the module docstring). Never touches a database."""
    hoy_bruto = entrada["hoy"]
    hoy = date.fromisoformat(hoy_bruto) if isinstance(hoy_bruto, str) else hoy_bruto
    senales = [a_senal(s, hoy=hoy) for s in entrada.get("senales") or []]
    prio = priorizar(senales, hoy=hoy)
    if not prio.hay_algo:
        return {"texto": SIN_NOVEDADES, "afirmaciones": [], "propuestas": []}
    from asistente.nucleo.repaso.servicio import nombres_de_ambitos

    nombres = {**nombres_de_ambitos(), "hogar": "Hogar", "eventos": "Novedades"}
    periodo = periodo_de(hoy, "semanal")
    resultado = redactar(proveedor, prio, periodo=periodo, hoy=hoy, nombres=nombres)
    redaccion = resultado.redaccion
    return _salida(prio, redaccion.titular, redaccion.items, cuerpo(prio, redaccion, nombres=nombres))


__all__ = ["SIN_NOVEDADES", "TIPOS", "a_senal", "ejecutar"]
