"""Deterministic classification of one message into a CLOSED set of categories, with its extracted fields.

Rules first, always. The optional LLM adapter (``clasificador_llm.py``) is consulted only for what the
rules leave as ``otro``, and its answer passes the same gate (a label of this closed set; every value must
appear literally in the message).

Priority when several match: phishing (it overrides; the apparent category is kept) > administración >
factura de luz > compra > cargo > viaje > acción con plazo > promoción > otro. «Requiere acción» is a flag
any category can carry when a deadline phrase announces a future date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import re
import unicodedata

from asistente.capacidades.correo import extraccion
from asistente.capacidades.correo.dominios import (
    Marca,
    Remitentes,
    es_correo_gratuito,
    marca_nombrada,
    marca_oficial_de,
)
from asistente.capacidades.correo.extraccion import Extraido
from asistente.capacidades.correo.mime import Mensaje
from asistente.capacidades.correo.normativa import cargar as cargar_normativa
from asistente.capacidades.correo.seguridad import UMBRAL_SOSPECHA, Senal, autenticacion, puntuacion, senales

CATEGORIAS: tuple[str, ...] = (
    "administracion",
    "factura_luz",
    "compra",
    "cargo",
    "viaje",
    "accion",
    "promocion",
    "phishing",
    "otro",
)
ETIQUETAS: dict[str, str] = {
    "administracion": "Aviso de la Administración",
    "factura_luz": "Factura de la luz",
    "compra": "Compra o recibo",
    "cargo": "Cargo o aviso del banco",
    "viaje": "Viaje",
    "accion": "Requiere una acción",
    "promocion": "Promoción o boletín",
    "phishing": "Posible fraude (phishing)",
    "otro": "Otro",
}

_ADMIN_NOTIFICACION = re.compile(
    r"tiene(s)? (una |\d+ )?(nueva(s)? )?notificaci|notificacion(es)? pendiente|nueva notificacion|"
    r"puesta a (su |tu )?disposicion|direccion electronica habilitada|\bdehu\b|notific@|"
    r"comparecencia en (la )?sede"
)
_ADMIN_GENERAL = re.compile(
    r"sede electronica|agencia tributaria|seguridad social|direccion general de trafico|requerimiento|"
    r"carta de pago|liquidacion provisional|notificacion"
)
_FACTURA = re.compile(r"\bfactura\b")
_LUZ = re.compile(
    r"\bkwh\b|electric|energia electrica|suministro electrico|potencia contratada|\bfactura de (la )?luz\b"
)
_COMPRA = re.compile(
    r"confirmacion de (tu |su )?pedido|tu pedido|su pedido|\bpedido\b|gracias por tu compra|gracias por su compra|"
    r"recibo de (tu |su )?compra|\bticket\b|factura simplificada|resumen de (tu |su )?compra"
)
_PEDIDO = re.compile(r"\bpedido\b", re.IGNORECASE)
_ENVIO = re.compile(r"\benvi(o|ado|aremos)\b|\bentreg|\breparto\b|\bseguimiento\b")
_CARGO = re.compile(
    r"\bcargo\b|\badeudo\b|se ha cargado|hemos cargado|compra con (tu |su )?tarjeta|recibo domiciliado|"
    r"domiciliacion|movimiento en (tu |su )?cuenta|pago con tarjeta"
)
_VIAJE = re.compile(
    r"\bvuelo\b|\btren\b|tarjeta de embarque|\bembarque\b|\bhotel\b|\blocalizador\b|check-?in|\bbillete"
)
_VIAJE_RESERVA = re.compile(r"\breserva\b|tarjeta de embarque|billete|check-?in|retras|cancelad")
_INCIDENCIA = re.compile(r"retrasad[oa]|retraso|cancelad[oa]|cancelaci[oó]n", re.IGNORECASE)
_ACCION = re.compile(
    r"\bdebe(s|ra|ran)?\b|tienes que|tiene que|es necesario|necesitamos|pendiente de|recuerda|recuerde|"
    r"renovar|renueve|renueva|pagar|presentar|confirmar|firmar|aportar|completar|responder"
)
_PROMO_ETIQUETAS = frozenset({"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"})
_PROMO = re.compile(r"newsletter|boletin|ofertas?\b|descuento|rebajas|promocion|darte de baja|darse de baja|cupon")


@dataclass
class Clasificacion:
    categoria: str
    categoria_aparente: str
    sospechoso: bool
    senales: list[Senal] = field(default_factory=list)
    campos: list[Extraido] = field(default_factory=list)
    plazo: date | None = None
    requiere_accion: bool = False
    # Brand/administration the message comes from (verified domain), or names (unverified).
    organismo: str | None = None
    organismo_verificado: bool = False
    area: str = ""
    comercio: str | None = None
    origen: str = "reglas"  # "reglas" | "llm"

    def campo(self, nombre: str) -> Extraido | None:
        return next((c for c in self.campos if c.campo == nombre), None)


def _plano(texto: str) -> str:
    sin = "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))
    return " ".join(sin.split())


def _comercio(mensaje: Mensaje, remitentes: Remitentes) -> str | None:
    """Who sold: the sender's display name of a business domain (never a person on a free mail service)."""
    if not mensaje.remitente_dominio or es_correo_gratuito(mensaje.remitente_dominio, remitentes):
        return None
    nombre = re.sub(r"[\"<>@]", " ", mensaje.remitente_nombre or "").strip()
    return " ".join(nombre.split())[:60] or mensaje.remitente_dominio.split(".")[-2][:60]


def _mas(campos: list[Extraido], *nuevos: Extraido | None) -> None:
    for nuevo in nuevos:
        if nuevo is not None and all(c.campo != nuevo.campo for c in campos):
            campos.append(nuevo)


def clasificar(mensaje: Mensaje, remitentes: Remitentes) -> Clasificacion:
    """Classify ``mensaje`` and extract its fields (all values literal in its visible text)."""
    texto = mensaje.texto
    plano = _plano(f"{mensaje.asunto}\n{texto}")
    asunto = _plano(mensaje.asunto)
    referencia = mensaje.fecha
    lista = senales(mensaje, remitentes)
    sospechoso = puntuacion(lista) >= UMBRAL_SOSPECHA
    oficial = marca_oficial_de(mensaje.remitente_dominio, remitentes) if mensaje.remitente_dominio else None
    auth = autenticacion(mensaje)
    nombrada: Marca | None = marca_nombrada(f"{mensaje.remitente_nombre} {mensaje.asunto}", remitentes)

    campos: list[Extraido] = []
    plazo_leido = extraccion.plazo(f"{mensaje.asunto}\n{texto}", referencia) if not mensaje.solo_metadatos else None
    plazo: date | None = date.fromisoformat(plazo_leido.valor) if plazo_leido is not None else None
    requiere_accion = plazo is not None and bool(_ACCION.search(plano))
    categoria = "otro"
    organismo: str | None = None
    verificado = False
    area = ""

    es_admin_remitente = oficial is not None and oficial.es_administracion and auth.dmarc != "fail"
    if (
        es_admin_remitente
        or _ADMIN_NOTIFICACION.search(plano)
        or (nombrada is not None and nombrada.es_administracion and _ADMIN_GENERAL.search(plano))
    ):
        categoria = "administracion"
        marca = oficial if es_admin_remitente else nombrada
        organismo = marca.nombre if marca is not None else None
        verificado = es_admin_remitente
        area = marca.area if marca is not None else "fiscal"
        if _ADMIN_NOTIFICACION.search(plano):
            # Deadline of Ley 39/2015 art. 43.2, counted from the notice's date (an estimate: see the rule's note).
            dias = cargar_normativa().entero("plazo_rechazo_notificacion", "dias_naturales")
            plazo_ley = referencia + timedelta(days=dias)
            plazo = min(plazo, plazo_ley) if plazo is not None else plazo_ley
            requiere_accion = True
    elif _FACTURA.search(plano) and (
        _LUZ.search(plano) or (any(c in plano for c in remitentes.comercializadoras_luz) and "factura" in asunto)
    ):
        categoria = "factura_luz"
        area = "luz"
        periodo = extraccion.periodo(texto, referencia)
        _mas(
            campos,
            extraccion.importe_total(texto),
            extraccion.fecha_tras(texto, referencia, r"fecha de (emisi[oó]n|(la )?factura)", "fecha_emision"),
            *(periodo or ()),
            extraccion.potencia_kw(texto),
            extraccion.fecha_tras(
                texto, referencia, r"fecha de cargo|se (te |le )?cargar[aá] el|cargo el", "fecha_cargo"
            ),
        )
    elif _COMPRA.search(plano) and not (oficial is not None and oficial.tipo == "banco"):
        categoria = "compra"
        area = "caducidades"
        _mas(
            campos,
            extraccion.importe_total(texto),
            extraccion.fecha_tras(texto, referencia, r"fecha (del |de )?(pedido|compra)", "fecha_compra"),
            extraccion.fecha_tras(texto, referencia, r"(entregado|entrega|llegar[aá]) el", "fecha_entrega"),
        )
        pedido = _PEDIDO.search(texto)
        if pedido is not None and _ENVIO.search(plano):
            # An order shipped to the buyer: a distance sale (the receipt's own word is the evidence).
            fragmento = extraccion.fragmento(texto, pedido.start(), pedido.end())
            _mas(campos, Extraido("modalidad", "distancia", pedido.group(0), fragmento))
    elif _CARGO.search(plano):
        categoria = "cargo"
        area = "suscripciones"
        _mas(
            campos,
            extraccion.importe_total(texto),
            extraccion.fecha_tras(
                texto, referencia, r"fecha( del cargo| de cargo| de la operaci[oó]n)?:", "fecha_cargo"
            ),
        )
        if oficial is not None:
            organismo, verificado = oficial.nombre, auth.dmarc != "fail"
    elif _VIAJE.search(plano) and _VIAJE_RESERVA.search(plano):
        categoria = "viaje"
        incidencia = _INCIDENCIA.search(texto)
        if incidencia is not None:
            fragmento = extraccion.fragmento(texto, incidencia.start(), incidencia.end())
            _mas(campos, Extraido("incidencia", incidencia.group(0).lower(), incidencia.group(0), fragmento))
        _mas(
            campos,
            extraccion.fecha_tras(
                texto, referencia, r"salida|fecha( del (vuelo|viaje))?:|ida:|llegada|entrada:?", "fecha_viaje"
            ),
        )
    elif requiere_accion:
        categoria = "accion"
    elif mensaje.etiquetas & _PROMO_ETIQUETAS or mensaje.cabecera("list-unsubscribe") or _PROMO.search(plano):
        categoria = "promocion"

    if plazo_leido is not None:
        _mas(campos, plazo_leido)
    aparente = categoria
    if sospechoso:
        categoria = "phishing"
    return Clasificacion(
        categoria=categoria,
        categoria_aparente=aparente,
        sospechoso=sospechoso,
        senales=lista,
        campos=[
            c
            for c in campos
            if extraccion.aparece(c.fragmento, f"{mensaje.asunto}\n{texto}")
            and extraccion.aparece(c.literal, c.fragmento)
        ],
        plazo=plazo if (requiere_accion or categoria == "administracion") else None,
        requiere_accion=requiere_accion and not sospechoso,
        organismo=organismo,
        organismo_verificado=verificado,
        area=area,
        comercio=_comercio(mensaje, remitentes) if categoria in ("compra", "factura_luz", "cargo") else None,
    )


__all__ = ["CATEGORIAS", "ETIQUETAS", "Clasificacion", "clasificar"]
