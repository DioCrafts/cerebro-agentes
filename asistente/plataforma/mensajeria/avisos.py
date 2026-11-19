"""Outgoing notices: the paired user's new inbox notices that matter, delivered to their chat once.

Which: the urgent ones (``severity == "urgente"``), the weekly digest / the brain's ``repaso`` (``resumen_semanal``)
and the council's rulings (``modulo:consejo``: «Dictamen del consejo listo», partial or failed). Everything else stays
in the app's inbox — a chat that buzzes for every notice gets muted.

Gates, in order:

* only notices created after the chat was paired, not read yet in the app, and younger than
  ``MENSAJERIA_AVISOS_MAX_HORAS`` (a worker that was down does not flood the chat with stale notices);
* ``/pausa``: skipped and recorded as such (resuming does not replay them: they are in the app);
* quiet hours (``MENSAJERIA_HORAS_SILENCIO``, Spanish time): held, and sent when the quiet hours end;
* once per chat and notice (``mensajeria_entregas``, the same idea as the push deliveries): a delivery is claimed
  before it is sent; a failed send is retried a few times, a sent one never again.

The text is the minimum: the title, one line of the body, and «ábrelo en la app» — the details stay behind the
user's session (the chat is end-to-end encrypted, but a lock screen is not).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time, timedelta
import logging
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from asistente.plataforma import settings
from asistente.plataforma.mensajeria import formato
from asistente.plataforma.mensajeria.contrato import Canal, MensajeSaliente

if TYPE_CHECKING:  # pragma: no cover
    from asistente.nucleo.proactive.store import Notification
    from asistente.plataforma.mensajeria.pasarela import Entorno

logger = logging.getLogger(__name__)

ZONA = ZoneInfo("Europe/Madrid")
KIND_CONSEJO = "modulo:consejo"
MAX_INTENTOS = 3
PIE = "Ábrelo en la app («Avisos») o pregúntame por ello aquí."


def merece_chat(notificacion: Notification) -> bool:
    """Urgent notices, the weekly digest (the brain's repaso) and the council's rulings."""
    from asistente.nucleo.proactive.store import KIND_RESUMEN

    return notificacion.severity == "urgente" or notificacion.kind == KIND_RESUMEN or notificacion.kind == KIND_CONSEJO


def _hora(texto: str) -> time | None:
    try:
        horas, minutos = texto.strip().split(":")
        return time(int(horas), int(minutos))
    except ValueError:
        return None


def en_silencio(momento: datetime, franja: str | None = None) -> bool:
    """Whether ``momento`` falls in the quiet hours «HH:MM-HH:MM» (Spanish time; across midnight when the start is
    later than the end). An empty or malformed setting means no quiet hours."""
    texto = franja if franja is not None else str(getattr(settings, "MENSAJERIA_HORAS_SILENCIO", "") or "")
    if "-" not in texto:
        return False
    inicio_txt, _, fin_txt = texto.partition("-")
    inicio, fin = _hora(inicio_txt), _hora(fin_txt)
    if inicio is None or fin is None or inicio == fin:
        return False
    ahora_local = momento.astimezone(ZONA).time()
    if inicio < fin:
        return inicio <= ahora_local < fin
    return ahora_local >= inicio or ahora_local < fin


def texto_aviso(notificacion: Notification, formato_canal: formato.Formato) -> str:
    """Title, one line, and where to read the rest."""
    titulo = " ".join(notificacion.title.split())[:160]
    linea = ""
    for candidata in formato.a_canal(notificacion.body, "texto").splitlines():
        candidata = " ".join(candidata.split()).lstrip("•› ").strip()
        if candidata:
            linea = candidata if len(candidata) <= 200 else candidata[:199].rstrip() + "…"
            break
    texto = "\n".join(p for p in (f"**Aviso: {titulo}**", linea, PIE) if p)
    return formato.a_canal(texto, formato_canal)


def _max_horas() -> int:
    return max(1, int(getattr(settings, "MENSAJERIA_AVISOS_MAX_HORAS", 48)))


def repartir(entorno: Entorno, canales: Mapping[str, Canal], momento: datetime) -> dict[str, int]:
    """One pass over every paired chat. Never raises; returns counts."""
    resumen = {"entregados": 0, "omitidos": 0, "retenidos": 0, "fallidos": 0}
    silencio = en_silencio(momento)
    antiguedad = momento - timedelta(hours=_max_horas())
    try:
        vinculos = entorno.almacen.vinculos()
    except Exception:
        logger.exception("messaging notices: bindings unavailable")
        return resumen
    almacen = entorno.almacen
    for vinculo in vinculos:
        canal = canales.get(vinculo.canal)
        if canal is None:
            continue
        try:
            avisos = entorno.notificaciones().list(vinculo.case_id, unread_only=True)
        except Exception:
            logger.exception("messaging notices: inbox unavailable")
            continue
        ya = almacen.entregas_de(vinculo.id)
        for aviso in sorted(avisos, key=lambda n: n.created_at):
            if not merece_chat(aviso) or aviso.created_at < vinculo.creado:
                continue
            # A notice already claimed (``ya``) was delivered or skipped — never again — or failed: the claim below
            # retries it a few times.
            if aviso.created_at < antiguedad or vinculo.pausado:
                if aviso.id not in ya:
                    almacen.terminar_entrega(vinculo.id, aviso.id, "omitida", momento)
                    resumen["omitidos"] += 1
                continue
            if silencio:
                resumen["retenidos"] += 1
                continue
            if not almacen.reclamar_entrega(vinculo.id, aviso.id, momento, max_intentos=MAX_INTENTOS):
                continue
            try:
                enviado = canal.enviar(
                    MensajeSaliente(contacto=vinculo.contacto, texto=texto_aviso(aviso, canal.capacidades.formato))
                )
            except Exception:  # adapters do not raise; a broken one must not stop the pass
                logger.exception("messaging notices: the channel raised while sending")
                enviado = None
            if enviado is not None:
                almacen.terminar_entrega(vinculo.id, aviso.id, "entregada", momento)
                resumen["entregados"] += 1
            else:
                almacen.terminar_entrega(vinculo.id, aviso.id, "fallida", momento)
                resumen["fallidos"] += 1
    return resumen


__all__ = ["KIND_CONSEJO", "PIE", "en_silencio", "merece_chat", "repartir", "texto_aviso"]
