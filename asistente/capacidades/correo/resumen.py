"""The summary and the search over DERIVED items, for the agent tools (as quoted untrusted data) and the UI.

No message body exists anywhere to summarise: only what ``almacen.Elemento`` keeps. Text that came from a
message (the subject, the literal snippets) is always passed through ``seguridad.citar`` for the model.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from asistente.capacidades.correo.almacen import Elemento, Propuesta
from asistente.capacidades.correo.clasificacion import ETIQUETAS
from asistente.capacidades.correo.decisiones import suprimido
from asistente.capacidades.correo.propuestas import NOMBRES_DESTINO, eur, fecha_es
from asistente.capacidades.correo.seguridad import citar, envolver
from asistente.nucleo.memoria.modelo import Decision

Periodo = Literal["hoy", "semana"]
SECCIONES: tuple[tuple[str, str], ...] = (
    ("acciones", "Requieren una acción"),
    ("administracion", "Avisos de la Administración"),
    ("phishing", "Posible fraude: no abras sus enlaces"),
    ("facturas", "Facturas y compras"),
    ("cargos", "Cargos y avisos del banco"),
    ("viajes", "Viajes"),
)


@dataclass
class Resumen:
    periodo: Periodo
    desde: date
    hasta: date
    secciones: dict[str, list[Elemento]] = field(default_factory=dict)
    otros: dict[str, int] = field(default_factory=dict)
    silenciados: int = 0

    @property
    def vacio(self) -> bool:
        return not any(self.secciones.values())


def construir(
    elementos: Iterable[Elemento], *, hoy: date, periodo: Periodo, decisiones: Sequence[Decision] = ()
) -> Resumen:
    desde = hoy if periodo == "hoy" else hoy - timedelta(days=6)
    resumen = Resumen(periodo=periodo, desde=desde, hasta=hoy, secciones={clave: [] for clave, _ in SECCIONES})
    for e in elementos:
        recientes = e.fecha >= desde
        plazo_vivo = e.requiere_accion and e.plazo is not None and e.plazo >= hoy
        if not (recientes or plazo_vivo):
            continue
        if suprimido(e, decisiones):
            resumen.silenciados += 1
            continue
        if plazo_vivo and e.categoria != "phishing":
            resumen.secciones["acciones"].append(e)
        if not recientes:
            continue
        seccion = {
            "administracion": "administracion",
            "phishing": "phishing",
            "factura_luz": "facturas",
            "compra": "facturas",
            "cargo": "cargos",
            "viaje": "viajes",
        }.get(e.categoria)
        if seccion is not None and not (seccion == "administracion" and plazo_vivo):
            resumen.secciones[seccion].append(e)
        elif seccion is None and not plazo_vivo:
            resumen.otros[e.categoria] = resumen.otros.get(e.categoria, 0) + 1
    resumen.secciones["acciones"].sort(key=lambda e: (e.plazo or hoy, e.id))
    return resumen


def linea(e: Elemento, *, para_modelo: bool = True) -> str:
    """One item in one line. Message text only as short «quotes» (sanitised for the model)."""
    cita = citar if para_modelo else (lambda t, m=200: f"«{t[:m]}»")
    partes = [
        f"[{e.id}] {fecha_es(e.fecha)}",
        f"de {e.dominio or 'remitente desconocido'}",
        ETIQUETAS.get(e.categoria, e.categoria),
    ]
    if e.organismo:
        partes.append(e.organismo + ("" if e.organismo_verificado else " (remitente no verificado)"))
    if e.asunto:
        partes.append(f"asunto {cita(e.asunto, 120)}")
    if e.plazo is not None:
        partes.append(f"plazo: {fecha_es(e.plazo)}")
    for c in e.campos:
        if c.campo == "plazo":
            continue
        valor = (
            eur(c.valor)
            if c.campo == "importe"
            else fecha_es(c.valor) if c.campo.startswith(("fecha", "periodo")) else c.valor
        )
        partes.append(f"{c.campo.replace('_', ' ')}: {valor} (leído en {cita(c.fragmento)})")
    if e.sospechoso and e.motivos:
        partes.append("motivos de la sospecha: " + " ".join(e.motivos))
    return "- " + " · ".join(partes)


def texto(resumen: Resumen) -> str:
    cabecera = (
        f"Correo de hoy ({fecha_es(resumen.hasta)})"
        if resumen.periodo == "hoy"
        else f"Correo de esta semana ({fecha_es(resumen.desde)}–{fecha_es(resumen.hasta)})"
    )
    lineas: list[str] = []
    for clave, titulo in SECCIONES:
        items = resumen.secciones.get(clave) or []
        if items:
            lineas.append(f"{titulo}:")
            lineas.extend(linea(e) for e in items)
    pie = []
    if resumen.otros:
        pie.append(
            "Sin importancia: "
            + ", ".join(f"{n} {ETIQUETAS.get(c, c).lower()}" for c, n in sorted(resumen.otros.items()))
            + "."
        )
    if resumen.silenciados:
        pie.append(f"{resumen.silenciados} mensaje(s) no se muestran porque dijiste que no te interesan.")
    if resumen.vacio:
        return "\n".join([cabecera + ": nada importante.", *pie])
    return "\n".join(
        [
            cabecera + ".",
            envolver(lineas),
            *pie,
            "Solo lectura: el asistente no envía, borra ni mueve correos, y nada de un correo se guarda en tu "
            "expediente sin que lo confirmes en el panel de Correo.",
        ]
    )


def linea_propuesta(p: Propuesta) -> str:
    destino = NOMBRES_DESTINO.get(p.destino, p.destino)
    origen = f"correo de {p.origen.dominio} del {fecha_es(p.origen.fecha)}"
    if p.origen.asunto:
        origen += f", asunto {citar(p.origen.asunto, 120)}"
    evidencias = "; ".join(f"{c.campo.replace('_', ' ')} leído en {citar(c.fragmento)}" for c in p.evidencias)
    return f"- [{p.id}] Para {destino}: {p.titulo} ({origen}){' · ' + evidencias if evidencias else ''} · estado: {p.estado}"


def a_dict(e: Elemento) -> dict[str, Any]:
    datos = e.model_dump(mode="json")
    datos["etiqueta"] = ETIQUETAS.get(e.categoria, e.categoria)
    return datos


def resumen_dict(resumen: Resumen) -> dict[str, Any]:
    return {
        "periodo": resumen.periodo,
        "desde": resumen.desde.isoformat(),
        "hasta": resumen.hasta.isoformat(),
        "secciones": [
            {"clave": clave, "titulo": titulo, "elementos": [a_dict(e) for e in resumen.secciones.get(clave) or []]}
            for clave, titulo in SECCIONES
        ],
        "otros": resumen.otros,
        "silenciados": resumen.silenciados,
    }


__all__ = [
    "SECCIONES",
    "Periodo",
    "Resumen",
    "a_dict",
    "construir",
    "linea",
    "linea_propuesta",
    "resumen_dict",
    "texto",
]
