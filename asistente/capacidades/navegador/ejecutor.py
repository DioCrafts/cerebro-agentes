"""Run one declarative script in a real Chromium (Playwright), under the policy of ``seguridad.py``.

How a run goes:

1. **Browser.** A fresh Chromium with a fresh, empty context (no profile, no stored cookies, service workers
   blocked, downloads accepted only to be checked). Every request of the context goes through
   :meth:`PoliticaRed.decidir` (route interception): other origins are aborted by the browser itself.
2. **Login, by the user.** The agent opens the script's login page and does nothing else until the
   "logged in" marker (``login.hecho_cuando``) appears:

   * ``modo="ventana"``: Chromium opens HEADED on the machine that runs the service (a personal deployment
     on the user's own computer). The user types their credentials, uses their certificate or Cl@ve in that
     window; nothing they type passes through the app, and the agent takes no screenshot and reads nothing
     of the page while it waits.
   * ``modo="sesion"``: the user hands over, for this run only, a session they opened themselves elsewhere
     (Playwright ``storage_state``: cookies / localStorage). It is filtered to the script's origins, loaded
     into the fresh context, never written to disk and dropped with the context.

3. **The agent's steps.** Only now does the agent act, step by step, as the script says; the network
   policy switches to "read only" (GET), so no form can be submitted even by accident.
4. **The end.** A download is checked (type, size, text) and returned in memory; a procedure stops at the
   «Firmar y enviar» button WITHOUT clicking it, with a screenshot and the summary of what would be sent.
   The context and the browser are closed (in ``ventana`` mode the window is first left to the user, who may
   submit the procedure themselves; the agent runs nothing more). Temporary files are deleted.

Nothing here talks to a model except the optional selector repair (``reparacion.py``), and nothing here
stores anything: the caller (``tareas.py``) decides what is kept.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Any, Literal, Protocol

from asistente.capacidades.navegador.guion import (
    Guion,
    Paso,
    PasoDescargar,
    PasoDetener,
    PasoEsperar,
    PasoIr,
    PasoPulsar,
    PasoRellenar,
    PasoSeleccionar,
    renderizar,
)
from asistente.capacidades.navegador.reparacion import Reparador, puerta
from asistente.capacidades.navegador.seguridad import (
    JS_ELEMENTO,
    JS_ESQUELETO,
    PoliticaRed,
    comprobar_descarga,
    contiene_literal,
    origen_de,
    problemas_campo,
    problemas_pulsable,
    redactar_esqueleto,
)
from asistente.plataforma import settings

logger = logging.getLogger(__name__)

Modo = Literal["ventana", "sesion"]
EstadoFinal = Literal["descargado", "detenido_antes_de_enviar", "fallido", "cancelado", "login_no_completado"]
EstadoPaso = Literal["ok", "reparado", "no_encontrado", "no_coincide", "rechazado", "error", "cancelado"]
# Step outcomes that mean "the page is not the one the script describes": reported to the format watch.
FALLOS_DE_FORMATO = frozenset({"no_encontrado", "no_coincide"})
MAX_FILAS_RESUMEN = 60
MAX_SESION_BYTES = 256_000
ESPERA_SESION_S = 15


@dataclass(frozen=True)
class Evento:
    tipo: str
    texto: str  # Spanish, never a value the user typed
    paso: str | None = None
    momento: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class PasoEjecutado:
    id: str
    accion: str
    estado: EstadoPaso
    detalle: str = ""


@dataclass
class ResultadoEjecucion:
    estado: EstadoFinal
    mensaje: str
    pasos: list[PasoEjecutado] = field(default_factory=list)
    descarga: bytes | None = None
    nombre_descarga: str | None = None
    resumen_envio: list[tuple[str, str]] = field(default_factory=list)
    captura: bytes | None = None
    bloqueadas: list[str] = field(default_factory=list)
    reparaciones: dict[str, str] = field(default_factory=dict)
    esqueleto_fallo: list[dict[str, Any]] | None = None
    # True once the user's login was detected and the agent started its own steps.
    llego_a_agente: bool = False
    url_final_origen: str | None = None


class Observador(Protocol):
    def evento(self, evento: Evento) -> None: ...

    def captura(self, png: bytes) -> None: ...


class _Nulo:
    def evento(self, evento: Evento) -> None:
        return None

    def captura(self, png: bytes) -> None:
        return None


class _CanceladoError(Exception):
    pass


class _FalloPasoError(Exception):
    def __init__(self, estado: EstadoPaso, detalle: str) -> None:
        super().__init__(detalle)
        self.estado = estado
        self.detalle = detalle


def ejecutable_chromium() -> str | None:
    """``NAVEGADOR_CHROMIUM`` or None (Playwright's own browser)."""
    return getattr(settings, "NAVEGADOR_CHROMIUM", "") or None


def filtrar_sesion(sesion: Any, origenes: set[str]) -> tuple[dict[str, Any], int]:
    """A handed-over ``storage_state`` reduced to the run's origins: (filtered state, items dropped)."""
    import json

    if not isinstance(sesion, dict):
        raise ValueError("La sesión debe ser un objeto con «cookies» y, opcionalmente, «origins».")
    if len(json.dumps(sesion, default=str)) > MAX_SESION_BYTES:
        raise ValueError("La sesión es demasiado grande.")
    hosts = {origen.split("://", 1)[1].split(":")[0].strip("[]") for origen in origenes}
    cookies, origins, descartados = [], [], 0
    for cookie in sesion.get("cookies") or []:
        dominio = str(cookie.get("domain") or "").lstrip(".").lower() if isinstance(cookie, dict) else ""
        if dominio and any(h == dominio or h.endswith("." + dominio) for h in hosts):
            cookies.append(cookie)
        else:
            descartados += 1
    for entrada in sesion.get("origins") or []:
        if isinstance(entrada, dict) and origen_de(str(entrada.get("origin") or "")) in origenes:
            origins.append(entrada)
        else:
            descartados += 1
    return {"cookies": cookies, "origins": origins}, descartados


class Ejecutor:
    def __init__(
        self,
        guion: Guion,
        parametros: dict[str, Any],
        *,
        origenes: dict[str, str],
        modo: Modo,
        sesion: dict[str, Any] | None = None,
        observador: Observador | None = None,
        cancelar: threading.Event | None = None,
        cerrar: threading.Event | None = None,
        reparador: Reparador | None = None,
        headless: bool | None = None,
        ejecutable: str | None = None,
        espera_login_s: float | None = None,
        timeout_paso_ms: int = 15_000,
        pausa_ms: int | None = None,
        entrega_max_s: float = 600,
        activo: Callable[[], bool] | None = None,
        usuario_simulado: Callable[[Any], None] | None = None,
    ) -> None:
        if modo == "sesion" and not sesion:
            raise ValueError("El modo «sesión» necesita la sesión que entregas para esta tarea.")
        self.guion = guion
        self.parametros = parametros
        self.origenes = origenes
        self.modo = modo
        self._sesion = sesion
        self.observador: Observador = observador or _Nulo()
        self.cancelar = cancelar or threading.Event()
        self.cerrar = cerrar or threading.Event()
        self.reparador = reparador
        self.headless = (modo != "ventana") if headless is None else headless
        self.ejecutable = ejecutable if ejecutable is not None else ejecutable_chromium()
        self.espera_login_s = espera_login_s if espera_login_s is not None else guion.login.espera_max_s
        self.timeout_paso_ms = timeout_paso_ms
        self.pausa_ms = guion.pausa_ms if pausa_ms is None else pausa_ms
        self.entrega_max_s = entrega_max_s
        self.activo = activo or (lambda: True)
        # TESTS ONLY: stands for the person typing their own credentials in the window during the login
        # phase. Not reachable from the API.
        self.usuario_simulado = usuario_simulado
        self.politica = PoliticaRed.para(guion, origenes)
        self._avisados: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------ #

    def _evento(self, tipo: str, texto: str, paso: str | None = None) -> None:
        try:
            self.observador.evento(Evento(tipo=tipo, texto=texto, paso=paso))
        except Exception:  # an observer must never break a run
            logger.warning("browser run observer failed", exc_info=True)

    def _comprobar_parada(self) -> None:
        if self.cancelar.is_set():
            raise _CanceladoError("detenida por el usuario")
        if not self.activo():
            raise _CanceladoError("el agente con navegador se ha desactivado o has retirado tu consentimiento")

    def _ruta(self, route: Any, request: Any) -> None:
        permitido, motivo = self.politica.decidir(request.url, request.method)
        if permitido:
            route.continue_()
            return
        origen = self.politica.anotar_bloqueo(request.url)
        if (origen, motivo) not in self._avisados:
            self._avisados.add((origen, motivo))
            self._evento("bloqueo", f"Bloqueada una petición a {origen}: {motivo}.")
        route.abort("blockedbyclient")

    def _ruta_ws(self, ws: Any) -> None:
        url = str(ws.url)
        http = "https" + url[3:] if url.startswith("wss:") else "http" + url[2:] if url.startswith("ws:") else url
        permitido, motivo = self.politica.decidir(http, "GET")
        if permitido and self.politica.fase != "agente":
            ws.connect_to_server()
            return
        self.politica.anotar_bloqueo(http)
        ws.close(code=1008, reason="bloqueado")

    # ------------------------------------------------------------------ #

    def ejecutar(self) -> ResultadoEjecucion:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        resultado = ResultadoEjecucion(estado="fallido", mensaje="")
        tmp = Path(tempfile.mkdtemp(prefix="navegador-"))
        try:
            with sync_playwright() as pw:
                try:
                    navegador = pw.chromium.launch(headless=self.headless, executable_path=self.ejecutable)
                except PlaywrightError as exc:
                    resultado.mensaje = (
                        "No se ha podido abrir el navegador"
                        + (" en una ventana (¿hay pantalla en este equipo?)" if not self.headless else "")
                        + f": {str(exc).splitlines()[0][:200]}"
                    )
                    return resultado
                try:
                    self._ejecutar_en(navegador, resultado, tmp)
                finally:
                    self.politica.fase = "cerrada"
                    try:
                        navegador.close()
                    except PlaywrightError:
                        pass
        except _CanceladoError as exc:  # raised outside a step (e.g. while handing over)
            resultado.estado, resultado.mensaje = "cancelado", f"Tarea detenida: {exc}."
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            self._sesion = None
        resultado.bloqueadas = sorted(set(self.politica.bloqueadas))
        return resultado

    def _ejecutar_en(self, navegador: Any, resultado: ResultadoEjecucion, tmp: Path) -> None:
        from playwright.sync_api import Error as PlaywrightError

        opciones: dict[str, Any] = {
            "accept_downloads": True,
            "service_workers": "block",
            "locale": "es-ES",
            "timezone_id": "Europe/Madrid",
            "viewport": {"width": 1280, "height": 900},
        }
        if self.modo == "sesion":
            estado, descartados = filtrar_sesion(self._sesion, set(self.origenes.values()))
            opciones["storage_state"] = estado
            if descartados:
                self._evento("sesion", f"Se han descartado {descartados} cookies o datos de otros sitios de la sesión.")
        self._sesion = None
        contexto = navegador.new_context(**opciones)
        try:
            contexto.route("**/*", self._ruta)
            try:
                contexto.route_web_socket("**/*", self._ruta_ws)
            except AttributeError, PlaywrightError:
                # Without the filter a page could open a websocket to any host and carry data out of the
                # allowed origins: the task does not run (Playwright >= 1.48 is required).
                resultado.estado = "fallido"
                resultado.mensaje = (
                    "No se ha podido activar el filtro de conexiones del navegador (websockets); "
                    "la tarea no se ejecuta. Actualiza Playwright."
                )
                return

            def nueva_pagina(pagina: Any) -> None:
                # The agent drives one tab; any other one opened while it works is closed at once.
                if self.politica.fase == "agente":
                    try:
                        pagina.close()
                    except PlaywrightError:
                        pass

            contexto.on("page", nueva_pagina)
            pagina = contexto.new_page()
            pagina.set_default_timeout(self.timeout_paso_ms)
            try:
                if not self._login(pagina):
                    resultado.estado = "login_no_completado"
                    resultado.mensaje = (
                        "No se ha detectado el inicio de sesión a tiempo. No se ha hecho nada en el sitio."
                        if self.modo == "ventana"
                        else "La sesión entregada no es válida o ha caducado. No se ha hecho nada en el sitio."
                    )
                    return
                resultado.llego_a_agente = True
                self.politica.fase = "agente"
                self._evento("agente", "Sesión iniciada. A partir de aquí el agente solo consulta: no envía datos.")
                self._pasos(pagina, resultado, tmp)
            except _CanceladoError as exc:
                resultado.estado, resultado.mensaje = "cancelado", f"Tarea detenida: {exc}. No se ha enviado nada."
                return
            resultado.url_final_origen = origen_de(pagina.url) if not pagina.is_closed() else None
            if resultado.estado == "detenido_antes_de_enviar" and self.modo == "ventana" and not self.headless:
                self._entregar(pagina)
        finally:
            self.politica.fase = "cerrada"
            try:
                contexto.close()
            except PlaywrightError:
                pass

    # ------------------------------------------------------------------ #

    def _login(self, pagina: Any) -> bool:
        login = self.guion.login
        url = self.origenes[login.origen] + login.ruta
        if self.modo == "ventana":
            self._evento(
                "esperando_login",
                "Inicia sesión tú en la ventana del navegador que se ha abierto. El agente no ve lo que escribes "
                "y no hará nada hasta que hayas entrado.",
            )
        else:
            self._evento("esperando_login", "Comprobando la sesión que has entregado.")
        pagina.goto(url, wait_until="domcontentloaded")
        if self.usuario_simulado is not None:
            self.usuario_simulado(pagina)
        espera = self.espera_login_s if self.modo == "ventana" else min(self.espera_login_s, ESPERA_SESION_S)
        limite = time.monotonic() + espera
        marcador = pagina.locator(login.hecho_cuando)
        while time.monotonic() < limite:
            self._comprobar_parada()
            try:
                if not pagina.is_closed() and marcador.first.is_visible():
                    return True
            except Exception:  # navigation in progress
                pass
            if pagina.is_closed():
                return False
            pagina.wait_for_timeout(400)
        return False

    def _entregar(self, pagina: Any) -> None:
        """Leave the window to the user (headed mode only); the agent runs nothing more."""
        self.politica.fase = "usuario"
        self._evento(
            "entregado",
            "El agente ha terminado. La ventana es tuya: revisa el formulario y, si quieres, fírmalo y envíalo tú. "
            "HaciendaGPT no lo hará. Ciérrala cuando acabes.",
        )
        limite = time.monotonic() + self.entrega_max_s
        while time.monotonic() < limite and not self.cerrar.is_set() and not self.cancelar.is_set():
            if pagina.is_closed():
                return
            pagina.wait_for_timeout(500)

    # ------------------------------------------------------------------ #

    def _pasos(self, pagina: Any, resultado: ResultadoEjecucion, tmp: Path) -> None:
        for paso in self.guion.pasos:
            self._comprobar_parada()
            self._evento("paso", paso.descripcion, paso.id)
            try:
                estado = self._paso(pagina, paso, resultado, tmp)
            except _FalloPasoError as fallo:
                resultado.pasos.append(PasoEjecutado(paso.id, paso.accion, fallo.estado, fallo.detalle))
                resultado.estado = "fallido"
                resultado.mensaje = (
                    f"No se ha podido completar «{paso.descripcion}»: {fallo.detalle}. No se ha enviado nada."
                )
                if fallo.estado in FALLOS_DE_FORMATO:
                    resultado.esqueleto_fallo = self._esqueleto(pagina)
                self._evento("fallo", resultado.mensaje, paso.id)
                self._captura(pagina)
                return
            except _CanceladoError:
                resultado.pasos.append(PasoEjecutado(paso.id, paso.accion, "cancelado"))
                raise
            except Exception as exc:  # timeouts, navigation errors, a closed window…
                detalle = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}" if str(exc) else type(exc).__name__
                resultado.pasos.append(PasoEjecutado(paso.id, paso.accion, "error", detalle))
                resultado.estado = "fallido"
                resultado.mensaje = f"Error en «{paso.descripcion}» ({detalle}). No se ha enviado nada."
                self._evento("fallo", resultado.mensaje, paso.id)
                return
            resultado.pasos.append(PasoEjecutado(paso.id, paso.accion, estado))
            if isinstance(paso, PasoDescargar | PasoDetener):
                return
            self._captura(pagina)
            if self.pausa_ms:
                pagina.wait_for_timeout(self.pausa_ms)

    def _captura(self, pagina: Any, *, completa: bool = False, enmascarar: bool = True) -> bytes | None:
        try:
            mascara = [pagina.locator(s) for s in self.guion.enmascarar] if enmascarar else []
            png: bytes = pagina.screenshot(full_page=completa, mask=mascara, timeout=10_000)
        except Exception:
            return None
        try:
            self.observador.captura(png)
        except Exception:
            logger.warning("browser run observer failed", exc_info=True)
        return png

    def _esqueleto(self, pagina: Any) -> list[dict[str, Any]] | None:
        try:
            return redactar_esqueleto(pagina.evaluate(JS_ESQUELETO), enmascarar_ids=self.guion.enmascarar)
        except Exception:
            return None

    def _localizar(self, pagina: Any, selector: str) -> tuple[Any, int, dict[str, Any] | None]:
        localizador = pagina.locator(selector)
        try:
            localizador.first.wait_for(state="attached", timeout=self.timeout_paso_ms)
        except Exception:
            return localizador, 0, None
        cuantos = localizador.count()
        info = localizador.first.evaluate(JS_ELEMENTO) if cuantos == 1 else None
        return localizador, cuantos, info

    def _elemento(
        self, pagina: Any, paso: PasoEsperar | PasoRellenar | PasoPulsar, resultado: ResultadoEjecucion
    ) -> tuple[Any, dict[str, Any], bool]:
        """(locator, element facts, repaired?) of a step's element, or :class:`_FalloPasoError`."""
        localizador, cuantos, info = self._localizar(pagina, paso.selector)
        if cuantos == 1 and info is not None:
            return localizador, info, False
        if cuantos > 1:
            raise _FalloPasoError("no_coincide", f"el selector encuentra {cuantos} elementos, no uno")
        if self.reparador is None:
            raise _FalloPasoError("no_encontrado", "la página ha cambiado: no encuentro el elemento")
        esqueleto = self._esqueleto(pagina) or []
        self._evento("reparacion", "La página ha cambiado; se pide una propuesta de selector y se comprueba.", paso.id)
        propuesto = self.reparador.proponer(paso, esqueleto)
        if not propuesto:
            raise _FalloPasoError("no_encontrado", "la página ha cambiado y no hay un selector alternativo seguro")
        try:
            localizador, cuantos, info = self._localizar(pagina, propuesto)
        except Exception:
            cuantos, info = 0, None
        problemas = puerta(paso, propuesto, cuantos, info, self.origenes.values())
        if problemas:
            raise _FalloPasoError(
                "no_encontrado",
                "la página ha cambiado y el selector propuesto no supera la comprobación ("
                + "; ".join(problemas)
                + ")",
            )
        assert info is not None
        resultado.reparaciones[paso.id] = propuesto
        self._evento("reparacion", "Selector alternativo comprobado (mismo campo, misma etiqueta).", paso.id)
        return localizador, info, True

    def _paso(self, pagina: Any, paso: Paso, resultado: ResultadoEjecucion, tmp: Path) -> EstadoPaso:
        if isinstance(paso, PasoIr):
            url = self.origenes[paso.origen] + paso.ruta
            if not self.politica.decidir(url, "GET")[0]:
                raise _FalloPasoError("rechazado", "dirección fuera de los sitios permitidos")
            respuesta = pagina.goto(url, wait_until="domcontentloaded")
            if origen_de(pagina.url) not in self.politica.origenes:
                raise _FalloPasoError("rechazado", "la página ha redirigido fuera de los sitios permitidos")
            if respuesta is not None and respuesta.status >= 400:
                raise _FalloPasoError("no_encontrado", f"el sitio responde {respuesta.status}")
            return "ok"

        localizador, info, reparado = self._elemento(pagina, paso, resultado)
        estado: EstadoPaso = "reparado" if reparado else "ok"
        elemento = localizador.first

        if isinstance(paso, PasoEsperar):
            elemento.wait_for(state="visible")
            if paso.texto and not contiene_literal(paso.texto, str(info.get("texto") or "")):
                raise _FalloPasoError("no_coincide", f"no muestra el texto «{paso.texto}»")
            return estado

        if isinstance(paso, PasoRellenar):
            seleccion = isinstance(paso, PasoSeleccionar)
            problemas = problemas_campo(info, campo=paso.campo, etiqueta=paso.etiqueta, seleccion=seleccion)
            if problemas:
                raise _FalloPasoError("no_coincide", "; ".join(problemas))
            valor = renderizar(paso.valor, self.parametros, paso.formato_fecha)
            if seleccion:
                self._seleccionar(elemento, valor)
            else:
                elemento.fill(valor)
                if elemento.input_value() != valor:
                    raise _FalloPasoError("no_coincide", "el campo no admite el valor (formato distinto)")
            return estado

        if isinstance(paso, PasoPulsar):
            parada = isinstance(paso, PasoDetener)
            if not contiene_literal(paso.texto, str(info.get("texto") or "")):
                raise _FalloPasoError(
                    "no_coincide", f"su texto es «{str(info.get('texto') or '')[:80]}», no «{paso.texto}»"
                )
            problemas = problemas_pulsable(info, texto=paso.texto, origenes=self.politica.origenes, es_parada=parada)
            if problemas:
                raise _FalloPasoError("no_coincide" if parada else "rechazado", "; ".join(problemas))
            if isinstance(paso, PasoDetener):
                self._detener(pagina, elemento, paso, resultado)
                return estado
            if isinstance(paso, PasoDescargar):
                self._descargar(pagina, elemento, resultado, tmp)
                return estado
            elemento.click()
            try:
                pagina.wait_for_load_state("domcontentloaded")
            except Exception:
                pass
            return estado
        raise _FalloPasoError("rechazado", f"acción desconocida {paso.accion}")

    def _seleccionar(self, elemento: Any, valor: str) -> None:
        if not valor:
            opciones = [v for v in elemento.evaluate("el => Array.from(el.options).map(o => o.value)") if v]
            if len(opciones) != 1:
                raise _FalloPasoError(
                    "rechazado", f"hay {len(opciones)} opciones y no se ha indicado cuál elegir (indica el dato)"
                )
            valor = opciones[0]
        valores = elemento.evaluate("el => Array.from(el.options).map(o => o.value)")
        if valor not in valores:
            raise _FalloPasoError("rechazado", "la opción indicada no está en la lista del sitio")
        elemento.select_option(value=valor)

    def _descargar(self, pagina: Any, elemento: Any, resultado: ResultadoEjecucion, tmp: Path) -> None:
        with pagina.expect_download(timeout=max(self.timeout_paso_ms, 30_000)) as espera:
            elemento.click()
        descarga = espera.value
        if origen_de(descarga.url) not in self.politica.origenes:
            descarga.cancel()
            raise _FalloPasoError("rechazado", "la descarga viene de fuera de los sitios permitidos")
        destino = tmp / "descarga.bin"
        descarga.save_as(destino)
        try:
            tamano = destino.stat().st_size
            problema = comprobar_descarga(descarga.suggested_filename, tamano, self.guion.descarga)
            if problema:
                raise _FalloPasoError("rechazado", f"descarga no válida: {problema}")
            resultado.descarga = destino.read_bytes()
            resultado.nombre_descarga = descarga.suggested_filename
        finally:
            destino.unlink(missing_ok=True)
            try:
                descarga.delete()
            except Exception:
                pass
        resultado.estado = "descargado"
        resultado.mensaje = f"Fichero descargado ({len(resultado.descarga)} bytes)."
        self._evento("descarga", resultado.mensaje)

    def _detener(self, pagina: Any, elemento: Any, paso: PasoDetener, resultado: ResultadoEjecucion) -> None:
        """Locate the submit/sign button, summarise and STOP. There is no code path that clicks it."""
        if paso.resumen is not None:
            filas = pagina.locator(paso.resumen.filas)
            for i in range(min(filas.count(), MAX_FILAS_RESUMEN)):
                fila = filas.nth(i)
                try:
                    etiqueta = fila.locator(paso.resumen.etiqueta).first.inner_text(timeout=2_000)
                    valor = fila.locator(paso.resumen.valor).first.inner_text(timeout=2_000)
                except Exception:
                    continue
                resultado.resumen_envio.append((" ".join(etiqueta.split())[:200], " ".join(valor.split())[:300]))
        try:
            elemento.scroll_into_view_if_needed(timeout=3_000)
            elemento.evaluate("el => { el.style.outline = '4px solid #c0392b'; el.style.outlineOffset = '3px'; }")
        except Exception:
            pass
        resultado.captura = self._captura(pagina, completa=True, enmascarar=False)
        resultado.estado = "detenido_antes_de_enviar"
        resultado.mensaje = (
            f"Formulario preparado. El agente se ha detenido ANTES de «{paso.texto}»: no se ha firmado ni enviado nada."
        )
        self._evento("detenido", resultado.mensaje, paso.id)


__all__ = [
    "FALLOS_DE_FORMATO",
    "Ejecutor",
    "EstadoFinal",
    "Evento",
    "Modo",
    "Observador",
    "PasoEjecutado",
    "ResultadoEjecucion",
    "ejecutable_chromium",
    "filtrar_sesion",
]
