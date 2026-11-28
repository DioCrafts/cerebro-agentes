"""Connections to the MCP servers: one long-lived session per server, driven from synchronous code.

The project is synchronous (threads); the MCP SDK is asynchronous and a session must be opened and closed by the
same task. So:

* one daemon thread runs an asyncio loop for every connector of the process;
* each server has one task (:meth:`_Sesion._servir`) that opens the ``mcp.Client``, lists the tools and then
  serves requests from a queue, one at a time, until it is closed or the connection breaks;
* callers (:meth:`GestorConexiones.listar`, :meth:`GestorConexiones.llamar`) block on a future with a deadline.

What the client refuses to be for a server: it declares no ``sampling``, ``elicitation`` or ``roots`` handler, so
a server can neither ask this app's model for a completion nor ask the user anything; a tool that answers
«input required» simply fails. A call that runs out of time closes the session (its state is unknown) and the
next call opens a fresh one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from concurrent.futures import Future as FuturoHilo
from concurrent.futures import TimeoutError as TiempoAgotado
from dataclasses import dataclass
import json
import logging
import threading
from typing import Any

from asistente.capacidades.conectores import ajustes
from asistente.capacidades.conectores.config import SecretoAusenteError, ServidorMCP, resolver
from asistente.capacidades.conectores.definicion import HerramientaRemota, remota

logger = logging.getLogger(__name__)

NOMBRE_CLIENTE = "cerebro-agentes"
MAX_PAGINAS = 20
# Extra seconds the caller waits beyond the call's own deadline (queueing, the round trip to the loop thread).
MARGEN_S = 5.0


class ConexionError(RuntimeError):
    """The server cannot be reached or broke the protocol (Spanish message, no secret, no argument)."""


class TiempoAgotadoError(ConexionError):
    """The server did not answer in time."""


class DefinicionCambiadaError(ConexionError):
    """The live tool is not the one the operator approved."""


@dataclass(frozen=True)
class ResultadoRemoto:
    texto: str
    estructurado: Any = None
    es_error: bool = False
    # Content blocks that are not text (images, audio, embedded resources): counted, never forwarded.
    omitidos: int = 0


# What ``mcp.Client`` accepts as its ``server``: a URL, stdio parameters, a transport or an in-process server.
Destino = Any
Fabrica = Callable[[str, ServidorMCP], Destino]


def destino_real(clave: str, servidor: ServidorMCP) -> Destino:
    """The transport of a declared server: a subprocess (stdio) or a Streamable HTTP endpoint."""
    del clave
    if servidor.transporte == "stdio":
        from mcp import StdioServerParameters

        assert servidor.comando is not None
        return StdioServerParameters(
            command=servidor.comando,
            args=list(servidor.argumentos),
            # Merged by the SDK over its own short list of safe variables: the service's environment (its API
            # keys, its database path) is NOT inherited by the server process.
            env=resolver(servidor.entorno) or None,
            cwd=servidor.directorio,
        )
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    assert servidor.url is not None
    return streamable_http_client(
        servidor.url, http_client=create_mcp_http_client(headers=resolver(servidor.cabeceras))
    )


def _texto_de(resultado: Any) -> ResultadoRemoto:
    partes: list[str] = []
    omitidos = 0
    for bloque in getattr(resultado, "content", None) or []:
        texto = getattr(bloque, "text", None)
        if getattr(bloque, "type", None) == "text" and isinstance(texto, str):
            partes.append(texto)
        else:
            omitidos += 1
    estructurado = getattr(resultado, "structured_content", None)
    if estructurado is not None:
        try:
            json.dumps(estructurado, default=str)
        except TypeError, ValueError:
            estructurado = None
    return ResultadoRemoto(
        texto="\n".join(partes),
        estructurado=estructurado,
        es_error=bool(getattr(resultado, "is_error", False)),
        omitidos=omitidos,
    )


_Trabajo = tuple[Callable[[Any], Coroutine[Any, Any, Any]], "asyncio.Future[Any]"]


class _Sesion:
    """One server's connection: created and used only inside the manager's loop."""

    def __init__(self, clave: str, servidor: ServidorMCP, fabrica: Fabrica) -> None:
        self.clave = clave
        self.servidor = servidor
        self._fabrica = fabrica
        self._cola: asyncio.Queue[_Trabajo | None] = asyncio.Queue()
        self._lista: asyncio.Future[list[HerramientaRemota]] = asyncio.get_running_loop().create_future()
        self._tarea = asyncio.get_running_loop().create_task(self._servir(), name=f"mcp-{clave}")
        self.cerrada = False

    async def _servir(self) -> None:
        from mcp import Client, Implementation

        error: BaseException | None = None
        try:
            destino = self._fabrica(self.clave, self.servidor)
            cliente = Client(
                destino,
                read_timeout_seconds=self.servidor.tiempo_llamada(),
                client_info=Implementation(name=NOMBRE_CLIENTE, version="1"),
            )
            async with cliente:
                self._lista.set_result(await self._listar(cliente))
                while True:
                    trabajo = await self._cola.get()
                    if trabajo is None:
                        return
                    accion, futuro = trabajo
                    if futuro.cancelled():
                        continue
                    try:
                        valor = await accion(cliente)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # the call failed; the session may still be usable
                        if not futuro.done():
                            futuro.set_exception(exc)
                    else:
                        if not futuro.done():
                            futuro.set_result(valor)
        except asyncio.CancelledError:
            error = ConexionError("La conexión se ha cerrado.")
            raise
        except BaseException as exc:  # the SDK raises exception groups on a broken transport
            error = exc
            logger.warning("MCP server %s: connection ended (%s)", self.clave, type(exc).__name__)
        finally:
            self.cerrada = True
            fallo = error or ConexionError("La conexión se ha cerrado.")
            if not self._lista.done():
                self._lista.set_exception(fallo)
            # Read the exception so asyncio does not log «never retrieved» for a list nobody awaited.
            if self._lista.done() and not self._lista.cancelled():
                self._lista.exception()
            while not self._cola.empty():
                pendiente = self._cola.get_nowait()
                if pendiente is not None and not pendiente[1].done():
                    pendiente[1].set_exception(fallo)

    @staticmethod
    async def _listar(cliente: Any) -> list[HerramientaRemota]:
        herramientas: list[HerramientaRemota] = []
        cursor: str | None = None
        for _pagina in range(MAX_PAGINAS):
            pagina = await cliente.list_tools(cursor=cursor)
            for tool in pagina.tools:
                herramientas.append(remota(tool.name, tool.description, tool.input_schema))
            cursor = pagina.next_cursor
            if not cursor:
                break
        return herramientas

    async def herramientas(self) -> list[HerramientaRemota]:
        return list(await asyncio.shield(self._lista))

    async def pedir(self, accion: Callable[[Any], Coroutine[Any, Any, Any]]) -> Any:
        await asyncio.shield(self._lista)  # raises the connection error when the session never came up
        if self.cerrada:
            raise ConexionError("La conexión se ha cerrado.")
        futuro: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await self._cola.put((accion, futuro))
        return await futuro

    async def cerrar(self) -> None:
        if self._tarea.done():
            return
        self._tarea.cancel()
        try:
            await self._tarea
        except asyncio.CancelledError, Exception:  # closing must never raise
            pass


class GestorConexiones:
    """The MCP sessions of this process. Thread-safe; every method blocks until done or out of time."""

    def __init__(self, fabrica: Fabrica | None = None) -> None:
        self._fabrica: Fabrica = fabrica or destino_real
        self._sesiones: dict[str, _Sesion] = {}
        # Background closes of timed-out sessions (kept so the tasks are not garbage-collected mid-flight).
        self._cierres: set[asyncio.Task[None]] = set()
        self._bucle: asyncio.AbstractEventLoop | None = None
        self._hilo: threading.Thread | None = None
        self._lock = threading.Lock()

    # --- the loop thread -----------------------------------------------------

    def _loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._bucle is None or not self._bucle.is_running():
                listo = threading.Event()
                bucle = asyncio.new_event_loop()

                def correr() -> None:
                    asyncio.set_event_loop(bucle)
                    bucle.call_soon(listo.set)
                    bucle.run_forever()

                self._hilo = threading.Thread(target=correr, name="mcp-conexiones", daemon=True)
                self._hilo.start()
                listo.wait(5)
                self._bucle = bucle
            return self._bucle

    def _ejecutar(self, corrutina: Coroutine[Any, Any, Any], tiempo: float) -> Any:
        futuro: FuturoHilo[Any] = asyncio.run_coroutine_threadsafe(corrutina, self._loop())
        try:
            return futuro.result(tiempo + MARGEN_S)
        except TiempoAgotado as exc:
            futuro.cancel()
            raise TiempoAgotadoError("El servicio externo no ha respondido a tiempo.") from exc

    async def _sesion(self, clave: str, servidor: ServidorMCP) -> _Sesion:
        actual = self._sesiones.get(clave)
        if actual is not None and not actual.cerrada and actual.servidor == servidor:
            return actual
        if actual is not None:
            await actual.cerrar()
        nueva = _Sesion(clave, servidor, self._fabrica)
        self._sesiones[clave] = nueva
        return nueva

    async def _descartar(self, clave: str, *, esperar: bool = True) -> None:
        """Forget a session and close it. ``esperar=False``: close it in the background, because a session
        whose call never answered may take as long to close as that call would have taken."""
        sesion = self._sesiones.pop(clave, None)
        if sesion is None:
            return
        if esperar:
            await sesion.cerrar()
            return
        cierre = asyncio.get_running_loop().create_task(sesion.cerrar(), name=f"mcp-cierre-{clave}")
        self._cierres.add(cierre)
        cierre.add_done_callback(self._cierres.discard)

    # --- what callers use ----------------------------------------------------

    def listar(self, clave: str, servidor: ServidorMCP, *, tiempo: float | None = None) -> list[HerramientaRemota]:
        """The server's tools as it lists them now (connecting if needed). Raises :class:`ConexionError`."""
        limite = float(tiempo) if tiempo is not None else ajustes.tiempo_conexion()

        async def listar() -> list[HerramientaRemota]:
            sesion = await self._sesion(clave, servidor)
            try:
                return await asyncio.wait_for(sesion.herramientas(), limite)
            except TimeoutError:
                await self._descartar(clave, esperar=False)
                raise TiempoAgotadoError("El servicio externo no ha respondido a tiempo.") from None

        return self._traducir(clave, lambda: self._ejecutar(listar(), limite))

    def llamar(
        self,
        clave: str,
        servidor: ServidorMCP,
        nombre: str,
        argumentos: dict[str, Any],
        *,
        huella: str | None = None,
    ) -> ResultadoRemoto:
        """Call tool ``nombre``. With ``huella`` the live definition must still be the approved one.

        Raises :class:`DefinicionCambiadaError` (the server changed or removed the tool) or
        :class:`ConexionError`.
        """
        limite = servidor.tiempo_llamada()
        conexion = ajustes.tiempo_conexion()

        async def llamar() -> ResultadoRemoto:
            sesion = await self._sesion(clave, servidor)
            try:
                vivas = {h.nombre: h for h in await asyncio.wait_for(sesion.herramientas(), conexion)}
                if huella is not None:
                    viva = vivas.get(nombre)
                    if viva is None:
                        raise DefinicionCambiadaError("El servicio externo ya no ofrece esa herramienta.")
                    if viva.huella != huella:
                        raise DefinicionCambiadaError(
                            "El servicio externo ha cambiado la definición de esa herramienta desde que se aprobó."
                        )
                crudo = await asyncio.wait_for(
                    sesion.pedir(lambda cliente: cliente.call_tool(nombre, argumentos, read_timeout_seconds=limite)),
                    limite,
                )
            except TimeoutError:
                await self._descartar(clave, esperar=False)
                raise TiempoAgotadoError("El servicio externo no ha respondido a tiempo.") from None
            return _texto_de(crudo)

        return self._traducir(clave, lambda: self._ejecutar(llamar(), limite + conexion))

    def _traducir(self, clave: str, accion: Callable[[], Any]) -> Any:
        try:
            return accion()
        except ConexionError, SecretoAusenteError:
            raise
        except Exception as exc:
            # The SDK's own errors can quote server text: only their type goes to the caller.
            logger.warning("MCP server %s failed: %s", clave, type(exc).__name__, exc_info=True)
            raise ConexionError(f"El servicio externo ha fallado ({type(exc).__name__}).") from exc

    def cerrar(self, clave: str | None = None) -> None:
        """Close one session (or all of them, stopping the loop thread)."""
        bucle = self._bucle
        if bucle is None or not bucle.is_running():
            return

        async def cerrar() -> None:
            for nombre in [clave] if clave is not None else list(self._sesiones):
                await self._descartar(nombre)

        try:
            asyncio.run_coroutine_threadsafe(cerrar(), bucle).result(10)
        except Exception:
            logger.warning("could not close the MCP sessions cleanly", exc_info=True)
        if clave is None:
            with self._lock:
                bucle.call_soon_threadsafe(bucle.stop)
                self._bucle = None


_gestor: list[GestorConexiones] = []
_gestor_lock = threading.Lock()


def gestor() -> GestorConexiones:
    """The process-wide connection manager (``reiniciar`` replaces it in tests)."""
    with _gestor_lock:
        if not _gestor:
            _gestor.append(GestorConexiones())
        return _gestor[0]


def reiniciar(nuevo: GestorConexiones | None = None) -> None:
    with _gestor_lock:
        anteriores = list(_gestor)
        _gestor.clear()
        if nuevo is not None:
            _gestor.append(nuevo)
    for anterior in anteriores:
        if anterior is not nuevo:
            anterior.cerrar()


__all__ = [
    "ConexionError",
    "DefinicionCambiadaError",
    "GestorConexiones",
    "ResultadoRemoto",
    "TiempoAgotadoError",
    "destino_real",
    "gestor",
    "reiniciar",
]
