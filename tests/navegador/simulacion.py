"""Local mock sites for the browser agent: Datadis, a Cl@ve-like login gateway, a sede and a tracker.

The real sites are not reachable from this machine. These mocks reproduce the documented FLOWS (a login
the user completes, a consumption form with a CUPS select and a date range that leads to a CSV download,
a multi-step sede form whose last button is «Firmar y enviar»), not the real pages: their HTML is in
``paginas/``. Each site is a separate origin (its own port on 127.0.0.1), so the allow-list is tested on
real cross-origin requests; the tracker counts every hit it gets, the sede every submission.

Run ``python -m tests.navegador.simulacion`` to serve them on fixed ports for a manual check of the UI; it
prints the ``NAVEGADOR_ORIGENES_SUSTITUTOS`` value that points the scripts at them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

PAGINAS = Path(__file__).resolve().parent / "paginas"
USUARIO_DATADIS = ("12345678Z", "contrasena-de-prueba")
USUARIO_CLAVE = ("12345678Z", "PIN123")
CUPS_SIMULADOS = ("ES0021000000000001AA", "ES0031000000000002BB")


@dataclass
class Registro:
    """What the mock sites saw: the tests assert on it."""

    variante: str = "normal"
    peticiones: list[tuple[str, str, str]] = field(default_factory=list)  # (site, method, path)
    tracker: list[str] = field(default_factory=list)
    envios_sede: list[dict[str, list[str]]] = field(default_factory=list)
    posts_datadis: list[str] = field(default_factory=list)
    descargas: int = 0
    sesiones_datadis: set[str] = field(default_factory=set)
    tickets_clave: set[str] = field(default_factory=set)
    sesiones_sede: set[str] = field(default_factory=set)
    origenes: dict[str, str] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def anota(self, sitio: str, metodo: str, ruta: str) -> None:
        with self.lock:
            self.peticiones.append((sitio, metodo, ruta))


def _pagina(sitio: str, nombre: str, reg: Registro, **extra: str) -> bytes:
    texto = (PAGINAS / sitio / nombre).read_text(encoding="utf-8")
    sustituciones = {
        "TRACKER": reg.origenes.get("tracker", ""),
        "CLAVE": reg.origenes.get("clave", ""),
        "SEDE": reg.origenes.get("sede", ""),
        "ERROR": "",
        **extra,
    }
    for clave, valor in sustituciones.items():
        texto = texto.replace("{{" + clave + "}}", valor)
    return texto.encode("utf-8")


def csv_datadis(cups: str, desde: date, hasta: date) -> str:
    """A Datadis-style hourly export (``CUPS;Fecha;Hora;Consumo_kWh;Metodo_obtencion``), decimal commas."""
    lineas = ["CUPS;Fecha;Hora;Consumo_kWh;Metodo_obtencion"]
    dia = desde
    while dia <= hasta:
        for hora in range(1, 25):
            kwh = 0.1 + ((hora * 7 + dia.day) % 10) / 20
            lineas.append(f"{cups};{dia:%Y/%m/%d};{hora:02d}:00;{kwh:.3f}".replace(".", ",") + ";Real")
        dia += timedelta(days=1)
    return "\n".join(lineas) + "\n"


class _Base(BaseHTTPRequestHandler):
    sitio = ""
    registro: Registro

    def log_message(self, format: str, *args: Any) -> None:  # silence
        return

    def _enviar(
        self,
        codigo: int,
        cuerpo: bytes = b"",
        tipo: str = "text/html; charset=utf-8",
        cabeceras: dict[str, str] | None = None,
    ) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        for k, v in (cabeceras or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(cuerpo)

    def _redirigir(self, destino: str, cookie: str | None = None) -> None:
        cabeceras = {"Location": destino}
        if cookie:
            cabeceras["Set-Cookie"] = cookie
        self._enviar(303, b"", cabeceras=cabeceras)

    def _cookie(self, nombre: str) -> str | None:
        for parte in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == nombre:
                return v
        return None

    def _formulario(self) -> dict[str, list[str]]:
        largo = int(self.headers.get("Content-Length") or 0)
        return parse_qs(self.rfile.read(largo).decode("utf-8")) if largo else {}

    def do_GET(self) -> None:
        self.registro.anota(self.sitio, "GET", self.path)
        self.get(urlsplit(self.path))

    def do_POST(self) -> None:
        self.registro.anota(self.sitio, "POST", self.path)
        self.post(urlsplit(self.path))

    def get(self, url: Any) -> None:
        self._enviar(404, b"no encontrado")

    def post(self, url: Any) -> None:
        self._enviar(404, b"no encontrado")


class _Tracker(_Base):
    sitio = "tracker"

    def do_GET(self) -> None:
        with self.registro.lock:
            self.registro.tracker.append(self.path)
        self._enviar(200, b"/* tracker */", tipo="application/javascript")

    def do_POST(self) -> None:
        self.do_GET()


class _Datadis(_Base):
    sitio = "datadis"

    def _sesion(self) -> bool:
        return self._cookie("sesion_datadis") in self.registro.sesiones_datadis

    def get(self, url: Any) -> None:
        reg = self.registro
        if url.path in ("/", "/login"):
            if self._sesion():
                return self._redirigir("/privado/inicio")
            return self._enviar(200, _pagina("datadis", "login.html", reg))
        if not url.path.startswith("/privado/"):
            return self._enviar(404, b"no encontrado")
        if not self._sesion():
            return self._redirigir("/login")
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/privado/inicio":
            return self._enviar(200, _pagina("datadis", "inicio.html", reg))
        if url.path == "/privado/consumos":
            variante = reg.variante
            id_cups = "suministro" if variante in ("selector_cambiado", "etiqueta_cambiada") else "cups"
            etiqueta = "Punto de suministro" if variante == "etiqueta_cambiada" else "Suministro (CUPS)"
            ruta_csv = "/privado/consumo.exe" if variante == "descarga_exe" else "/privado/consumo.csv"
            pagina = _pagina(
                "datadis", "consumos.html", reg, ID_CUPS=id_cups, ETIQUETA_CUPS=etiqueta, RUTA_CSV=ruta_csv
            )
            return self._enviar(200, pagina)
        if url.path == "/privado/api/consumo":
            try:
                desde, hasta = date.fromisoformat(q["desde"]), date.fromisoformat(q["hasta"])
            except KeyError, ValueError:
                return self._enviar(400, b"{}", tipo="application/json")
            total = sum(0.1 + ((h * 7 + d) % 10) / 20 for d in range(1, 2) for h in range(1, 25))
            cuerpo = json.dumps({"total": f"{total * ((hasta - desde).days + 1):.1f}"}).encode()
            return self._enviar(200, cuerpo, tipo="application/json")
        if url.path in ("/privado/consumo.csv", "/privado/consumo.exe"):
            try:
                cups, desde, hasta = q["cups"], date.fromisoformat(q["desde"]), date.fromisoformat(q["hasta"])
            except KeyError, ValueError:
                return self._enviar(400, b"parametros")
            texto = csv_datadis(cups, desde, hasta)
            if reg.variante == "descarga_grande":
                texto += "# relleno\n" * 700_000
            with reg.lock:
                reg.descargas += 1
            nombre = "consumo.exe" if url.path.endswith(".exe") else f"consumo_{cups}.csv"
            return self._enviar(
                200,
                texto.encode("utf-8"),
                tipo="text/csv; charset=utf-8",
                cabeceras={"Content-Disposition": f'attachment; filename="{nombre}"'},
            )
        return self._enviar(404, b"no encontrado")

    def post(self, url: Any) -> None:
        reg = self.registro
        datos = self._formulario()
        if url.path == "/login":
            usuario, clave = datos.get("username", [""])[0], datos.get("password", [""])[0]
            if (usuario, clave) != USUARIO_DATADIS:
                pagina = _pagina("datadis", "login.html", reg, ERROR='<p id="error">Datos incorrectos.</p>')
                return self._enviar(401, pagina)
            token = secrets.token_hex(16)
            with reg.lock:
                reg.sesiones_datadis.add(token)
            return self._redirigir("/privado/inicio", cookie=f"sesion_datadis={token}; HttpOnly; Path=/")
        with reg.lock:
            reg.posts_datadis.append(url.path)
        return self._enviar(200, b"<p>Hecho.</p>")


class _Clave(_Base):
    sitio = "clave"

    def get(self, url: Any) -> None:
        if url.path != "/login":
            return self._enviar(404, b"no encontrado")
        vuelta = parse_qs(url.query).get("vuelta", [""])[0]
        return self._enviar(200, _pagina("clave", "login.html", self.registro, VUELTA=vuelta))

    def post(self, url: Any) -> None:
        reg = self.registro
        datos = self._formulario()
        vuelta = datos.get("vuelta", [""])[0]
        if (datos.get("dni", [""])[0], datos.get("pin", [""])[0]) != USUARIO_CLAVE:
            return self._enviar(401, b"<p>Identificacion fallida.</p>")
        if not vuelta.startswith(reg.origenes["sede"] + "/"):
            return self._enviar(400, b"<p>Vuelta no permitida.</p>")
        ticket = secrets.token_hex(8)
        with reg.lock:
            reg.tickets_clave.add(ticket)
        return self._redirigir(f"{vuelta}?ticket={ticket}")


class _Sede(_Base):
    sitio = "sede"

    def get(self, url: Any) -> None:
        reg = self.registro
        if url.path == "/acceso":
            ticket = parse_qs(url.query).get("ticket", [""])[0]
            with reg.lock:
                valido = ticket in reg.tickets_clave
                reg.tickets_clave.discard(ticket)
                token = secrets.token_hex(16)
                if valido:
                    reg.sesiones_sede.add(token)
            if not valido:
                return self._enviar(403, b"<p>Ticket no valido.</p>")
            return self._redirigir("/tramite", cookie=f"sesion_sede={token}; HttpOnly; Path=/")
        if url.path == "/tramite":
            if self._cookie("sesion_sede") not in reg.sesiones_sede:
                vuelta = quote(f"{reg.origenes['sede']}/acceso", safe="")
                return self._redirigir(f"{reg.origenes['clave']}/login?vuelta={vuelta}")
            return self._enviar(200, _pagina("sede", "tramite.html", reg))
        return self._enviar(404, b"no encontrado")

    def post(self, url: Any) -> None:
        datos = self._formulario()
        with self.registro.lock:
            self.registro.envios_sede.append(datos)
        return self._enviar(200, b"<p>Solicitud registrada.</p>")


@dataclass
class Sitios:
    registro: Registro
    servidores: list[ThreadingHTTPServer]

    @property
    def origenes(self) -> dict[str, str]:
        return self.registro.origenes

    def sustitutos(self) -> dict[str, str]:
        """The value of ``NAVEGADOR_ORIGENES_SUSTITUTOS`` that points the bundled scripts at these mocks."""
        o = self.registro.origenes
        return {
            "https://datadis.es": o["datadis"],
            "https://sede.ejemplo.invalid": o["sede"],
            "https://clave.ejemplo.invalid": o["clave"],
        }

    def parar(self) -> None:
        for servidor in self.servidores:
            servidor.shutdown()
            servidor.server_close()


def arrancar(puertos: dict[str, int] | None = None) -> Sitios:
    """Start the four mock sites on 127.0.0.1 (free ports unless given)."""
    reg = Registro()
    servidores: list[ThreadingHTTPServer] = []
    for nombre, clase in (("datadis", _Datadis), ("clave", _Clave), ("sede", _Sede), ("tracker", _Tracker)):
        manejador = type(f"Manejador_{nombre}", (clase,), {"registro": reg})
        servidor = ThreadingHTTPServer(("127.0.0.1", (puertos or {}).get(nombre, 0)), manejador)
        servidor.daemon_threads = True
        reg.origenes[nombre] = f"http://127.0.0.1:{servidor.server_address[1]}"
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        servidores.append(servidor)
    return Sitios(registro=reg, servidores=servidores)


if __name__ == "__main__":  # pragma: no cover - manual check of the UI
    sitios = arrancar({"datadis": 8791, "clave": 8792, "sede": 8793, "tracker": 8794})
    print("NAVEGADOR_ORIGENES_SUSTITUTOS='" + json.dumps(sitios.sustitutos()) + "'")
    print(f"Datadis: {USUARIO_DATADIS}  Cl@ve: {USUARIO_CLAVE}")
    threading.Event().wait()
