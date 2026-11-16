"""Data the mail PROPOSES to other modules. Nothing is written until the owner confirms it in the UI.

One proposal per message, built deterministically from the derived item (never from a model):

* purchase receipt -> ``caducidades`` (its document tool, ``caducidades_registrar_compra``): description,
  date, shop, amount, distance sale when the message is an order;
* electricity bill -> ``luz``: the contracted power (``luz_potencia_p1_kw``) when the bill states it; the
  total and the billing period are shown as a note, because the ``luz`` module has no fact for bills yet;
* bank charge notice -> ``suscripciones`` (note: that module learns charges from bank statements);
* administration notice -> the area it belongs to (``fiscal``, ``ayudas``…) as a note.

Applying a confirmed proposal (:func:`aplicar`) runs the destination module's own tool or validator, as if
the user had typed the data in that module: its rules decide. A tool that would act outside the app is
refused. Suspected phishing never produces a proposal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from hacienda_gpt.decision.schemas import CaseState
from hacienda_gpt.modulos.contrato import ContextoModulo, Modulo, ResultadoHerramienta
from hacienda_gpt.modulos.correo.almacen import CampoGuardado, Elemento, OrigenPropuesta, Propuesta

HERRAMIENTA_COMPRAS = "caducidades_registrar_compra"
HECHO_POTENCIA = "luz_potencia_p1_kw"
NOMBRES_DESTINO = {
    "caducidades": "Caducidades y garantías",
    "luz": "Luz",
    "suscripciones": "Suscripciones y recibos",
    "fiscal": "Impuestos",
    "ayudas": "Ayudas públicas",
}


def eur(texto: str) -> str:
    valor = Decimal(texto)
    entero = f"{abs(valor):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{entero} €"


def fecha_es(texto: str | date) -> str:
    dia = date.fromisoformat(texto) if isinstance(texto, str) else texto
    return dia.strftime("%d/%m/%Y")


def _evidencias(elemento: Elemento, *nombres: str) -> list[CampoGuardado]:
    return [c for n in nombres if (c := elemento.campo(n)) is not None]


def _origen(elemento: Elemento) -> OrigenPropuesta:
    return OrigenPropuesta(dominio=elemento.dominio, asunto=elemento.asunto, fecha=elemento.fecha)


def generar(case_id: str, elemento: Elemento) -> Propuesta | None:
    """The proposal a derived item gives rise to, or None."""
    if elemento.sospechoso or elemento.categoria == "phishing":
        return None
    comun = {"case_id": case_id, "elemento_id": elemento.id, "origen": _origen(elemento)}
    importe = elemento.campo("importe")
    if elemento.categoria == "compra" and (importe is not None or elemento.campo("fecha_compra") is not None):
        fecha = elemento.campo("fecha_compra")
        argumentos: dict[str, Any] = {
            "descripcion": (f"Compra en {elemento.comercio}" if elemento.comercio else "Compra")[:120],
            "fecha_compra": fecha.valor if fecha else elemento.fecha.isoformat(),
        }
        if elemento.comercio:
            argumentos["establecimiento"] = elemento.comercio[:80]
        if importe is not None:
            argumentos["importe"] = importe.valor
        if (entrega := elemento.campo("fecha_entrega")) is not None and entrega.valor >= argumentos["fecha_compra"]:
            argumentos["fecha_entrega"] = entrega.valor
        if elemento.campo("modalidad") is not None:
            argumentos["modalidad"] = "distancia"
        partes = [f"Del correo de {elemento.dominio} del {fecha_es(elemento.fecha)}."]
        if fecha is None:
            partes.append("La fecha de compra es la del correo (el mensaje no la dice); corrígela si no coincide.")
        if "modalidad" in argumentos:
            partes.append("Es un pedido, así que se registra como compra a distancia (14 días para devolverla).")
        partes.append("Caducidades calculará los plazos de devolución y garantía y te avisará antes de que acaben.")
        return Propuesta(
            **comun,
            destino="caducidades",
            tipo="herramienta",
            herramienta=HERRAMIENTA_COMPRAS,
            argumentos=argumentos,
            titulo=f"Guardar la compra{f' en {elemento.comercio}' if elemento.comercio else ''}"
            + (f" ({eur(importe.valor)})" if importe else ""),
            explicacion=" ".join(partes),
            evidencias=_evidencias(elemento, "importe", "fecha_compra", "fecha_entrega", "modalidad"),
        )
    if elemento.categoria == "factura_luz":
        inicio, fin = elemento.campo("periodo_inicio"), elemento.campo("periodo_fin")
        detalle = []
        if importe is not None:
            detalle.append(f"total {eur(importe.valor)}")
        if inicio and fin:
            detalle.append(f"del {fecha_es(inicio.valor)} al {fecha_es(fin.valor)}")
        potencia = elemento.campo("potencia_kw")
        resumen = f"Factura de la luz{': ' + ', '.join(detalle) if detalle else ''}"
        if potencia is not None:
            return Propuesta(
                **comun,
                destino="luz",
                tipo="hechos",
                hechos={HECHO_POTENCIA: float(Decimal(potencia.valor))},
                titulo=f"Potencia contratada: {potencia.valor.replace('.', ',')} kW",
                explicacion=(
                    f"{resumen}. La factura dice que tienes {potencia.valor.replace('.', ',')} kW de potencia "
                    "contratada; si lo confirmas, Luz lo usará para calcular tu factura y comparar tarifas. Si tu "
                    "contrato tiene dos potencias distintas (punta y valle), revisa también la del periodo valle en Luz."
                ),
                evidencias=_evidencias(elemento, "potencia_kw", "importe", "periodo_inicio", "periodo_fin"),
            )
        if not detalle:
            return None
        return Propuesta(
            **comun,
            destino="luz",
            tipo="nota",
            titulo=resumen,
            explicacion=(
                f"{resumen}. El módulo Luz todavía no guarda facturas: si lo confirmas, queda anotada aquí, en Correo, "
                "para tus búsquedas. Para comparar tarifas con tu consumo real, importa el consumo en Luz."
            ),
            evidencias=_evidencias(elemento, "importe", "periodo_inicio", "periodo_fin", "fecha_emision"),
        )
    if elemento.categoria == "cargo" and importe is not None:
        return Propuesta(
            **comun,
            destino="suscripciones",
            tipo="nota",
            titulo=f"Cargo de {eur(importe.valor)}" + (f" ({elemento.organismo})" if elemento.organismo else ""),
            explicacion=(
                "Suscripciones detecta los cargos que se repiten a partir de los extractos del banco. Si lo confirmas, "
                "el cargo queda anotado en Correo; para ver si es recurrente, importa el extracto en Suscripciones."
            ),
            evidencias=_evidencias(elemento, "importe", "fecha_cargo"),
        )
    if elemento.categoria == "administracion" and elemento.area in NOMBRES_DESTINO:
        quien = elemento.organismo or "la Administración"
        return Propuesta(
            **comun,
            destino=elemento.area,
            tipo="nota",
            titulo=f"Aviso de {quien}" + (f": ábrelo antes del {fecha_es(elemento.plazo)}" if elemento.plazo else ""),
            explicacion=(
                f"Anotar este aviso de {quien} con lo de {NOMBRES_DESTINO[elemento.area]}. Ábrelo en la sede "
                "electrónica escribiendo tú la dirección, no desde el enlace del correo."
            ),
            evidencias=_evidencias(elemento, "plazo"),
        )
    return None


@dataclass(frozen=True)
class Aplicacion:
    texto: str
    es_error: bool
    case: CaseState | None  # the updated case when something was written


Registrador = Callable[[CaseState, Modulo, Mapping[str, Any]], CaseState]


def aplicar(
    propuesta: Propuesta,
    case: CaseState,
    destino: Modulo | None,
    *,
    perfil: Mapping[str, Any],
    hoy: date,
    registrar_hechos: Registrador,
) -> Aplicacion:
    """Apply a CONFIRMED proposal to its destination module (``destino`` must be active for the owner)."""
    if propuesta.tipo == "nota":
        nombre = NOMBRES_DESTINO.get(propuesta.destino, propuesta.destino)
        return Aplicacion(f"Anotado en Correo. No se ha cambiado nada en {nombre}.", False, None)
    if destino is None:
        nombre = NOMBRES_DESTINO.get(propuesta.destino, propuesta.destino)
        return Aplicacion(f"Activa primero el módulo «{nombre}» para guardar este dato.", True, None)
    if propuesta.tipo == "hechos":
        try:
            nuevo = registrar_hechos(case, destino, propuesta.hechos)
        except ValueError as exc:
            return Aplicacion(f"No se pudo guardar: {exc}", True, None)
        return Aplicacion(f"Guardado en {destino.nombre}.", False, nuevo)
    herramienta = next((h for h in destino.herramientas if h.spec.name == propuesta.herramienta), None)
    if herramienta is None or herramienta.efecto == "actua":
        return Aplicacion(f"{destino.nombre} ya no ofrece esa herramienta. No se ha hecho nada.", True, None)
    escrito: dict[str, Any] = {"case": case, "cambiado": False}

    def registrar(valores: Mapping[str, Any]) -> None:
        escrito["case"] = registrar_hechos(escrito["case"], destino, valores)
        escrito["cambiado"] = True

    try:
        resultado = herramienta.ejecutar(
            dict(propuesta.argumentos), ContextoModulo(perfil=perfil, hoy=hoy, case_id=case.case_id, registrar=registrar)
        )
    except ValueError as exc:
        return Aplicacion(f"No se pudo guardar: {exc}", True, None)
    if not isinstance(resultado, ResultadoHerramienta):
        return Aplicacion("La herramienta del módulo no devolvió un resultado válido.", True, None)
    if resultado.es_error:
        return Aplicacion(resultado.contenido, True, None)
    return Aplicacion(resultado.contenido, False, escrito["case"] if escrito["cambiado"] else None)


__all__ = ["HECHO_POTENCIA", "HERRAMIENTA_COMPRAS", "NOMBRES_DESTINO", "Aplicacion", "aplicar", "eur", "fecha_es", "generar"]
