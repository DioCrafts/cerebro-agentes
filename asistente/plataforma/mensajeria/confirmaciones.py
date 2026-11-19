"""Confirming from the chat what acts outside the app or spends: a one-time code per item, never a plain «sí».

WHY a code and not «sí»: in a chat, «sí» answers whatever the reader thinks it answers — the last question, an older
one, a forwarded message that says «sí, confírmalo». An action that sends, contracts or cancels (a module tool with
efecto ``actua``, already a pending action) or a council session that spends money must be confirmed for THAT item,
by the paired chat, knowingly. So the gateway shows, per item, «Para confirmar escribe: /confirmar 4821»:

* the code is bound to the item AND the chat (``mensajeria_confirmaciones``: binding, kind, item id, SHA-256 of the
  code), expires after ``MENSAJERIA_CONFIRMACION_MINUTOS`` and is spent by its first use — one conditional ``UPDATE``;
* a wrong, expired, reused or another chat's code does nothing at all (and says so);
* a new code for the same item voids the previous one; a forwarded or quoted message is never a command;
* ``/rechazar <código>`` takes the same code (rejecting is harmless, but it is still that item and nothing else);
* ``MENSAJERIA_CONFIRMAR_EN_CHAT`` decides which kinds the chat may confirm at all (``consejo``, ``actua``); the rest
  are shown with «confírmalo en la app».

What a code confirms runs through the SAME gate as the app's button: a pending action through the
``POST /acciones/{id}/confirmar`` endpoint function itself (module still on, the tool still an action, the transition
``pendiente → ejecutando`` once, the audit and the decisions memory), a council session through
``ServicioConsejo.confirmar`` (queued once). The chat adds a gate; it never removes one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import secrets
from typing import TYPE_CHECKING, Any, Literal

from asistente.plataforma import settings
from asistente.plataforma.mensajeria.almacen import AlmacenMensajeria, TipoConfirmacion, Vinculo, huella

if TYPE_CHECKING:  # pragma: no cover
    from asistente.plataforma.mensajeria.pasarela import Entorno

logger = logging.getLogger(__name__)

ClaseConfirmable = Literal["consejo", "actua"]
_MAX_SORTEOS = 30

TEXTO_CODIGO_NO_VALE = (
    "Ese código no vale: puede que haya caducado, que ya se usara o que no sea de este chat. No he hecho nada. "
    "Escribe /pendientes para ver los códigos vigentes."
)
TEXTO_SIN_CODIGO = "Escribe el código que te di, por ejemplo: /confirmar 4821. Escribe /pendientes para verlos."


def confirmable_en_chat(clase: ClaseConfirmable) -> bool:
    """Whether ``clase`` may be confirmed from the chat (``MENSAJERIA_CONFIRMAR_EN_CHAT``)."""
    valor = str(getattr(settings, "MENSAJERIA_CONFIRMAR_EN_CHAT", "consejo,actua") or "")
    return clase in {p.strip().lower() for p in valor.split(",") if p.strip()}


def _minutos() -> int:
    return max(1, int(getattr(settings, "MENSAJERIA_CONFIRMACION_MINUTOS", 30)))


def huella_confirmacion(vinculo_id: str, codigo: str) -> str:
    return huella("confirmacion", vinculo_id, codigo.strip())


def emitir(
    almacen: AlmacenMensajeria, vinculo: Vinculo, tipo: TipoConfirmacion, objeto_id: str, momento: datetime
) -> str:
    """A fresh 4-digit code for one item of this chat (stored hashed); the item's earlier code stops working."""
    caduca = momento + timedelta(minutes=_minutos())
    for _ in range(_MAX_SORTEOS):
        codigo = f"{1000 + secrets.randbelow(9000)}"
        if almacen.crear_confirmacion(
            vinculo_id=vinculo.id,
            tipo=tipo,
            objeto_id=objeto_id,
            huella_codigo=huella_confirmacion(vinculo.id, codigo),
            caduca=caduca,
            momento=momento,
        ):
            return codigo
    raise RuntimeError("no free confirmation code for this chat")  # pragma: no cover - 9000 codes, a few live


def canjear(
    almacen: AlmacenMensajeria,
    vinculo: Vinculo,
    codigo: str,
    tipos: tuple[TipoConfirmacion, ...],
    momento: datetime,
) -> tuple[TipoConfirmacion, str] | None:
    """Spend ``codigo`` of this chat: (kind, item id), or None — and then nothing may happen."""
    limpio = codigo.strip()
    if not (limpio.isdigit() and len(limpio) == 4):
        return None
    return almacen.canjear_confirmacion(
        vinculo_id=vinculo.id, huella_codigo=huella_confirmacion(vinculo.id, limpio), tipos=tipos, momento=momento
    )


# --------------------------------------------------------------------------------------------------------------
# What is pending, and the line that offers it
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pendiente:
    """An item waiting for the user: a module's action, or a council convocation."""

    tipo: Literal["accion", "consejo"]
    objeto_id: str
    descripcion: str  # Spanish, one line

    @property
    def clase(self) -> ClaseConfirmable:
        return "consejo" if self.tipo == "consejo" else "actua"


def _recorte(texto: str, n: int) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= n else texto[: n - 1].rstrip() + "…"


def describir_consejo(registro: Any) -> str:
    from asistente.nucleo.consejo.estimacion import formatear_usd

    minutos = max(1, int(getattr(registro.extra, "minutos_estimados", 0) or 5))
    return (
        f"Consejo del hogar sobre «{_recorte(registro.sesion.pregunta_usuario, 160)}»: coste estimado "
        f"{formatear_usd(registro.sesion.estimacion_usd)}, unos {minutos} minuto{'s' if minutos != 1 else ''}."
    )


def pendientes_de(entorno: Entorno, vinculo: Vinculo, *, desde: datetime | None = None) -> list[Pendiente]:
    """The binding's case: pending actions and council convocations waiting for confirmation (``desde``: only those
    created since then — what a turn just proposed)."""
    salida: list[Pendiente] = []
    try:
        for accion in entorno.acciones().list(vinculo.case_id, estado="pendiente"):
            if desde is None or accion.created_at >= desde:
                salida.append(Pendiente("accion", accion.id, _recorte(accion.resumen, 300)))
    except Exception:
        logger.exception("messaging: pending actions unavailable")
    try:
        from asistente.nucleo.consejo.almacen import tabla_existe
        from asistente.nucleo.consejo.servicio import servicio_para

        if tabla_existe(entorno.db_path):
            for registro in servicio_para(entorno.db_path).listar(vinculo.case_id):
                sesion = registro.sesion
                if sesion.estado != "pendiente_confirmacion":
                    continue
                if desde is not None and sesion.creada < desde:
                    continue
                salida.append(Pendiente("consejo", sesion.id, describir_consejo(registro)))
    except Exception:
        logger.exception("messaging: council convocations unavailable")
    return salida


def ofrecer(entorno: Entorno, vinculo: Vinculo, pendiente: Pendiente, momento: datetime) -> str:
    """The item's line: with its one-time code when the chat may confirm it, else «en la app»."""
    if not confirmable_en_chat(pendiente.clase):
        donde = "«Consejo»" if pendiente.tipo == "consejo" else "«Módulos»"
        return f"• {pendiente.descripcion}\n  Confírmalo o recházalo en la app ({donde})."
    codigo = emitir(entorno.almacen, vinculo, pendiente.tipo, pendiente.objeto_id, momento)
    return (
        f"• {pendiente.descripcion}\n  Para confirmar escribe: /confirmar {codigo} · para rechazar: /rechazar {codigo}"
    )


# --------------------------------------------------------------------------------------------------------------
# Deciding (through the app's own gates)
# --------------------------------------------------------------------------------------------------------------


def _endpoint(metodo: str, ruta: str) -> Callable[..., Any]:
    """The API's own endpoint function for ``metodo ruta`` (the gate the app's button goes through)."""
    from asistente.plataforma.api.api import app

    for route in app.routes:
        if getattr(route, "path", None) == ruta and metodo in (getattr(route, "methods", None) or ()):
            return route.endpoint  # type: ignore[attr-defined, no-any-return]
    raise LookupError(f"no {metodo} {ruta} in the API")


def _detalle(exc: Exception) -> str:
    detalle = getattr(exc, "detail", None)
    if isinstance(detalle, str) and detalle and detalle not in ("action not found", "case not found"):
        return detalle
    return "Esa acción ya no existe. No he hecho nada."


def decidir_accion(entorno: Entorno, vinculo: Vinculo, accion_id: str, *, confirmar: bool) -> str:
    """Confirm or reject a pending action of the binding's case, exactly as the app's buttons do."""
    from fastapi import HTTPException

    accion = entorno.acciones().get(accion_id)
    if accion is None or accion.case_id != vinculo.case_id:
        return "Esa acción ya no existe. No he hecho nada."
    ruta = "/acciones/{accion_id}/confirmar" if confirmar else "/acciones/{accion_id}/rechazar"
    try:
        acceso = entorno.acceso(vinculo)
        final = _endpoint("POST", ruta)(
            accion_id=accion_id, cases=entorno.casos(), acciones=entorno.acciones(), access=acceso
        )
    except HTTPException as exc:
        return _detalle(exc)
    except Exception:
        logger.exception("messaging: deciding action %s failed", accion_id)
        return "No he podido completarlo por un error interno. No se ha hecho nada; inténtalo desde la app."
    if not confirmar:
        return f"Rechazada: {_recorte(accion.resumen, 200)} No se hará."
    resultado = _recorte(str(getattr(final, "resultado", "") or ""), 400)
    if getattr(final, "estado", "") == "ejecutada":
        return f"Hecho: {_recorte(accion.resumen, 200)}" + (f"\nResultado: {resultado}" if resultado else "")
    return f"No se pudo completar: {resultado or 'la acción falló.'}"


def decidir_consejo(entorno: Entorno, vinculo: Vinculo, sesion_id: str, *, confirmar: bool) -> str:
    """Confirm (queue once) or reject a council convocation of the binding's case."""
    from asistente.nucleo.consejo.servicio import ConsejoError, servicio_para

    servicio = servicio_para(entorno.db_path)
    try:
        if confirmar:
            resultado = servicio.confirmar(sesion_id, case_id=vinculo.case_id, user_id=vinculo.user_id)
            registro = servicio.obtener(sesion_id, case_id=vinculo.case_id, user_id=vinculo.user_id)
            minutos = max(1, int(registro.extra.minutos_estimados or 5))
            if not resultado.confirmada_ahora:
                return "Esa sesión del consejo ya estaba confirmada."
            return (
                "Confirmado: el consejo empieza a estudiarlo (unos "
                f"{minutos} minuto{'s' if minutos != 1 else ''}). Te aviso aquí cuando el dictamen esté listo."
            )
        servicio.rechazar(sesion_id, case_id=vinculo.case_id, user_id=vinculo.user_id)
        return "Rechazado: el consejo no estudiará esa decisión."
    except ConsejoError as exc:
        return exc.mensaje
    except Exception:
        logger.exception("messaging: deciding council session %s failed", sesion_id)
        return "No he podido completarlo por un error interno. No se ha hecho nada; inténtalo desde la app."


def decidir(entorno: Entorno, vinculo: Vinculo, codigo: str, *, confirmar: bool, momento: datetime) -> str:
    """``/confirmar <código>`` and ``/rechazar <código>``: the item the code is bound to, and nothing else."""
    if not codigo.strip():
        return TEXTO_SIN_CODIGO
    canjeado = canjear(entorno.almacen, vinculo, codigo, ("accion", "consejo"), momento)
    if canjeado is None:
        return TEXTO_CODIGO_NO_VALE
    tipo, objeto_id = canjeado
    clase: ClaseConfirmable = "consejo" if tipo == "consejo" else "actua"
    if confirmar and not confirmable_en_chat(clase):  # the setting changed after the code was shown
        return "Esto solo se puede confirmar en la app. No he hecho nada."
    entorno.auditar(vinculo, "confirmacion" if confirmar else "rechazo", tipo=tipo, objeto_id=objeto_id)
    if tipo == "accion":
        return decidir_accion(entorno, vinculo, objeto_id, confirmar=confirmar)
    return decidir_consejo(entorno, vinculo, objeto_id, confirmar=confirmar)


__all__ = [
    "TEXTO_CODIGO_NO_VALE",
    "TEXTO_SIN_CODIGO",
    "ClaseConfirmable",
    "Pendiente",
    "canjear",
    "confirmable_en_chat",
    "decidir",
    "decidir_accion",
    "decidir_consejo",
    "emitir",
    "huella_confirmacion",
    "ofrecer",
    "pendientes_de",
]
