"""Proactive notices from the derived items (deterministic, idempotent: one stable key per message).

* ``administracion:<dominio>:<id>`` (urgente) — an administration notice received in the last 10 days:
  who, when, the 10 calendar days of art. 43.2 Ley 39/2015 (when it is a notification made available),
  that the e-mail is merely informative (art. 41.6), and to open it by typing the e-office address;
* ``accion:<dominio>:<id>`` — a message announcing a deadline in the next 30 days (urgente within 3 days);
* ``phishing:<dominio>:<id>`` — suspected fraud received in the last 7 days, with the reasons.

Items the user muted («No me interesa», ``decisiones.py``) give no notice; the inbox filter of the core
also applies to these keys (``memoria.ambitos.ambito_de_aviso`` maps them to ``correo:<categoria>:<dominio>``).
"""

from __future__ import annotations

from datetime import date, timedelta

from hacienda_gpt.capacidades.correo import almacen, decisiones
from hacienda_gpt.capacidades.correo.almacen import Elemento
from hacienda_gpt.capacidades.correo.normativa import cargar as cargar_normativa
from hacienda_gpt.capacidades.correo.propuestas import fecha_es
from hacienda_gpt.capacidades.correo.seguridad import citar
from hacienda_gpt.modulos.contrato import AvisoModulo, ContextoModulo

DIAS_ADMINISTRACION = 10
DIAS_PHISHING = 7
HORIZONTE_ACCION = 30


def _dominio(e: Elemento) -> str:
    return e.dominio or "desconocido"


def _asunto(e: Elemento) -> str:
    return f" (asunto {citar(e.asunto, 120)})" if e.asunto else ""


def aviso_administracion(e: Elemento, hoy: date) -> AvisoModulo:
    normativa = cargar_normativa()
    quien = e.organismo or "la Administración"
    partes = [
        f"El {fecha_es(e.fecha)} recibiste un correo de {_dominio(e)} que avisa de una notificación o un trámite de {quien}{_asunto(e)}."
    ]
    if not e.organismo_verificado:
        partes.append("No he podido comprobar que venga de verdad de ese organismo: no uses sus enlaces.")
    if e.plazo is not None:
        dias = normativa.entero("plazo_rechazo_notificacion", "dias_naturales")
        partes.append(
            f"Si es una notificación electrónica obligatoria para ti (o la elegiste), se entiende rechazada a los {dias} "
            f"días naturales de ponerse a tu disposición sin abrirla (art. 43.2 de la Ley 39/2015): ábrela antes del "
            f"{fecha_es(e.plazo)} (contando desde el día del correo; la fecha exacta es la de puesta a disposición que "
            "verás en la sede)."
        )
    partes.append(
        "El correo es solo un aviso: la notificación vale aunque el aviso no llegue (art. 41.6 de la Ley 39/2015). "
        "Entra en la sede electrónica o en la DEHú escribiendo tú la dirección (las sedes estatales terminan en "
        "«.gob.es»), no desde el enlace del correo."
    )
    return AvisoModulo(
        clave=f"administracion:{_dominio(e)}:{e.id}",
        titulo=f"Notificación de {quien}" + (f": ábrela antes del {fecha_es(e.plazo)}" if e.plazo else ""),
        cuerpo=" ".join(partes),
        gravedad="urgente",
        acciones=("Abrir la notificación en la sede electrónica o en la DEHú",),
        fuentes=normativa.citas("plazo_rechazo_notificacion") + normativa.citas("aviso_solo_informativo"),
        vence=e.plazo,
    )


def aviso_accion(e: Elemento, hoy: date) -> AvisoModulo:
    assert e.plazo is not None
    dias = (e.plazo - hoy).days
    return AvisoModulo(
        clave=f"accion:{_dominio(e)}:{e.id}",
        titulo=f"Tienes hasta el {fecha_es(e.plazo)}: correo de {_dominio(e)}",
        cuerpo=(
            f"Un correo de {_dominio(e)} del {fecha_es(e.fecha)}{_asunto(e)} pide hacer algo antes del "
            f"{fecha_es(e.plazo)} ({'hoy' if dias == 0 else f'quedan {dias} días'}). Compruébalo en el propio correo; "
            "el asistente no ha hecho nada."
        ),
        gravedad="urgente" if dias <= 3 else "aviso",
        vence=e.plazo,
    )


def aviso_phishing(e: Elemento) -> AvisoModulo:
    return AvisoModulo(
        clave=f"phishing:{_dominio(e)}:{e.id}",
        titulo=f"Posible fraude: correo de {_dominio(e)}",
        cuerpo=(
            f"El correo del {fecha_es(e.fecha)} de {_dominio(e)}{_asunto(e)} tiene señales de suplantación: "
            + " ".join(e.motivos)
            + " No abras sus enlaces ni adjuntos ni respondas con datos. Si dudas, entra tú en la web oficial. "
            "No se ha borrado ni movido nada de tu buzón."
        ),
        gravedad="aviso",
    )


def generar_avisos(contexto: ContextoModulo, *, db_path: str | None = None) -> list[AvisoModulo]:
    if contexto.case_id is None:
        return []
    hoy = contexto.hoy
    ruta = db_path or almacen.ruta_db()
    items = almacen.elementos(contexto.case_id, desde=hoy - timedelta(days=60), db_path=ruta)
    if not items:
        return []
    vigentes = decisiones.vigentes(ruta, contexto.case_id, hoy)
    salida: list[AvisoModulo] = []
    for e in items:
        if decisiones.suprimido(e, vigentes):
            continue
        if e.categoria == "phishing":
            if e.fecha >= hoy - timedelta(days=DIAS_PHISHING):
                salida.append(aviso_phishing(e))
        elif e.categoria == "administracion":
            if e.fecha >= hoy - timedelta(days=DIAS_ADMINISTRACION):
                salida.append(aviso_administracion(e, hoy))
        elif e.requiere_accion and e.plazo is not None and hoy <= e.plazo <= hoy + timedelta(days=HORIZONTE_ACCION):
            salida.append(aviso_accion(e, hoy))
    return salida


__all__ = ["aviso_accion", "aviso_administracion", "aviso_phishing", "generar_avisos"]
