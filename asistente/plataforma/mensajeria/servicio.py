"""The worker's service ``mensajeria``: the enabled channels, connected to the gateway, plus the gateway's chores.

``ejecutar(contexto)`` is the platform ``Servicio`` (registered in ``asistente/plataforma/trabajos.py``, on by
default only with ``MENSAJERIA_ACTIVA``). It:

1. builds the channels of ``MENSAJERIA_CANALES`` (``simplex`` → the SimpleX adapter's ``CanalSimplex``; ``prueba`` →
   the in-memory channel) — a channel that cannot be built is logged and skipped, the others run;
2. starts each one (``Canal.iniciar`` runs until ``parar`` and reconnects on its own) with the gateway's
   :meth:`~asistente.plataforma.mensajeria.pasarela.Pasarela.manejar` as the handler, each in its own thread (one
   that dies is started again after a pause);
3. every few seconds: publishes each channel's state to the database (the API runs in another process and shows the
   bot's address from there) and removes on the channels the contacts unpaired from the app or erased; every
   ``MENSAJERIA_AVISOS_INTERVALO_S`` delivers the new notices; once a day purges the deduplication records;
4. stops cleanly: the channels first (no new messages), then the gateway lets the queued turns finish.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from asistente.plataforma import settings
from asistente.plataforma.mensajeria.contrato import Canal, EstadoCanal
from asistente.plataforma.mensajeria.pasarela import Pasarela

if TYPE_CHECKING:  # pragma: no cover
    from asistente.plataforma.procesos import ContextoServicio

logger = logging.getLogger(__name__)

# Where each channel's adapter lives (``module:class``).
ADAPTADORES: dict[str, str] = {
    "simplex": "asistente.plataforma.mensajeria.simplex:CanalSimplex",
    "prueba": "asistente.plataforma.mensajeria.prueba:CanalPrueba",
}
_TICK_S = 5.0
_ESTADO_CADA_S = 15.0
_PURGA_CADA_S = 24 * 3600.0
_PAUSA_REINICIO_S = 10.0


def activa() -> bool:
    return bool(getattr(settings, "MENSAJERIA_ACTIVA", False))


def canales_configurados() -> list[str]:
    """The channel names of ``MENSAJERIA_CANALES`` that have an adapter (unknown ones are logged and skipped)."""
    nombres: list[str] = []
    for nombre in str(getattr(settings, "MENSAJERIA_CANALES", "") or "").split(","):
        nombre = nombre.strip().lower()
        if not nombre or nombre in nombres:
            continue
        if nombre not in ADAPTADORES:
            logger.warning("MENSAJERIA_CANALES names an unknown channel: %s", nombre)
            continue
        nombres.append(nombre)
    return nombres


def construir_canal(nombre: str) -> Canal:
    """The adapter of channel ``nombre`` (its ``desde_settings()`` when it has one, else its constructor)."""
    import importlib

    modulo, _, clase_nombre = ADAPTADORES[nombre].partition(":")
    clase: Any = getattr(importlib.import_module(modulo), clase_nombre)
    fabrica = getattr(clase, "desde_settings", None)
    canal = fabrica() if callable(fabrica) else clase()
    if not isinstance(canal, Canal):
        raise TypeError(f"{ADAPTADORES[nombre]} does not implement contrato.Canal")
    return canal


def construir_canales(nombres: Sequence[str] | None = None) -> list[Canal]:
    canales: list[Canal] = []
    for nombre in nombres if nombres is not None else canales_configurados():
        try:
            canales.append(construir_canal(nombre))
        except Exception:
            logger.exception("messaging: channel %s could not be built; the others run", nombre)
    return canales


class ServicioMensajeria:
    """The channels, the gateway and the chores of one database."""

    def __init__(
        self,
        db_path: str,
        *,
        canales: Sequence[Canal] | None = None,
        pasarela: Pasarela | None = None,
        intervalo_avisos: float | None = None,
        tick: float = _TICK_S,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self.canales = list(canales) if canales is not None else construir_canales()
        self.pasarela = pasarela or Pasarela(db_path, canales=self.canales)
        for canal in self.canales:
            self.pasarela.registrar_canal(canal)
        self._intervalo_avisos = (
            intervalo_avisos
            if intervalo_avisos is not None
            else max(5.0, float(getattr(settings, "MENSAJERIA_AVISOS_INTERVALO_S", 60)))
        )
        self._tick = max(0.01, tick)
        self._reloj = reloj
        self._hilos: dict[str, threading.Thread] = {}
        self._caidas: dict[str, float] = {}
        self._publicados: dict[str, tuple[Any, ...]] = {}
        self.vueltas = 0

    # -- the channels' threads ---------------------------------------------------------------------------------

    def _correr_canal(self, canal: Canal) -> None:
        try:
            canal.iniciar(self.pasarela.manejar)
        except Exception:
            logger.exception("messaging: channel %s stopped with an error", canal.nombre)

    def _asegurar_canales(self, parar: threading.Event) -> None:
        """Start every channel whose thread is not running (after a pause when it died)."""
        if parar.is_set():
            return
        for canal in self.canales:
            hilo = self._hilos.get(canal.nombre)
            if hilo is not None and hilo.is_alive():
                continue
            ahora = self._reloj()
            if hilo is not None:  # it died: wait a little before starting it again
                caida = self._caidas.setdefault(canal.nombre, ahora)
                if ahora - caida < _PAUSA_REINICIO_S:
                    continue
                logger.warning("messaging: restarting channel %s", canal.nombre)
            self._caidas.pop(canal.nombre, None)
            nuevo = threading.Thread(
                target=self._correr_canal, args=(canal,), name=f"mensajeria-{canal.nombre}", daemon=True
            )
            self._hilos[canal.nombre] = nuevo
            nuevo.start()

    def publicar_estados(self, *, solo_cambios: bool = False) -> None:
        """Write each channel's state to the database (``solo_cambios``: only the ones that changed since the last
        write, so the app learns at once that the channel connected or its address changed)."""
        for canal in self.canales:
            try:
                estado = canal.estado()
            except Exception:
                logger.exception("messaging: channel %s did not report its state", canal.nombre)
                estado = EstadoCanal(
                    canal=canal.nombre,
                    conectado=False,
                    detalle="El canal no responde.",
                    actualizado=datetime.now(UTC),
                )
            huella = (estado.conectado, estado.direccion, estado.detalle, estado.ultimo_error, estado.version)
            if solo_cambios and self._publicados.get(canal.nombre) == huella:
                continue
            try:
                self.pasarela.entorno.almacen.publicar_estado(estado, datetime.now(UTC))
                self._publicados[canal.nombre] = huella
            except Exception:
                logger.exception("messaging: channel state not published")

    # -- the loop -----------------------------------------------------------------------------------------------------

    def paso(self, ahora: float, marcas: dict[str, float]) -> None:
        """One round of chores (``marcas``: when each chore last ran)."""
        if ahora - marcas.get("estado", -1e18) >= _ESTADO_CADA_S:
            marcas["estado"] = ahora
            self.publicar_estados()
        else:
            self.publicar_estados(solo_cambios=True)
        try:
            self.pasarela.procesar_desconexiones()
        except Exception:
            logger.exception("messaging: disconnections failed")
        if ahora - marcas.get("avisos", -1e18) >= self._intervalo_avisos:
            marcas["avisos"] = ahora
            try:
                resumen = self.pasarela.repartir_avisos()
                if resumen.get("entregados") or resumen.get("fallidos"):
                    logger.info("messaging notices: %s", resumen)
            except Exception:
                logger.exception("messaging: notices failed")
        if ahora - marcas.get("purga", -1e18) >= _PURGA_CADA_S:
            marcas["purga"] = ahora
            try:
                dias = max(1, int(getattr(settings, "MENSAJERIA_RETENCION_DIAS", 7)))
                self.pasarela.entorno.almacen.purgar(datetime.now(UTC), dias=dias)
            except Exception:
                logger.exception("messaging: purge failed")

    def arrancar(self, parar: threading.Event) -> None:
        """Run until ``parar`` is set; always stops the channels and the gateway on the way out."""
        if not self.canales:
            logger.warning("messaging: no channel to run (MENSAJERIA_CANALES)")
            return
        marcas: dict[str, float] = {}
        logger.info("messaging: gateway started (%s)", ", ".join(c.nombre for c in self.canales))
        try:
            while not parar.is_set():
                self._asegurar_canales(parar)
                self.paso(self._reloj(), marcas)
                self.vueltas += 1
                parar.wait(self._tick)
        finally:
            self.parar()

    def parar(self, timeout: float = 10.0) -> None:
        for canal in self.canales:
            try:
                canal.parar()
            except Exception:
                logger.exception("messaging: channel %s did not stop cleanly", canal.nombre)
        for hilo in self._hilos.values():
            hilo.join(timeout)
        self.pasarela.cerrar(timeout)
        logger.info("messaging: gateway stopped")


def ejecutar(contexto: ContextoServicio) -> None:
    """The platform service (``Servicio("mensajeria", …).arrancar``)."""
    if not activa():
        logger.info("messaging: MENSAJERIA_ACTIVA is off; the service does nothing")
        return
    ServicioMensajeria(contexto.db_path).arrancar(contexto.parar)


__all__ = [
    "ADAPTADORES",
    "ServicioMensajeria",
    "activa",
    "canales_configurados",
    "construir_canal",
    "construir_canales",
    "ejecutar",
]
