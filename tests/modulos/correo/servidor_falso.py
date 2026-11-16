"""A local fake of Google's OAuth consent/token/revoke endpoints and the Gmail API, over HTTP on 127.0.0.1.

For the browser check and for the test of the real HTTP transport. NOT Google: it serves ``GoogleFalso``
(built from the public documentation) with the synthetic corpus. Run it with

    uv run python -m tests.modulos.correo.servidor_falso --puerto 8146

and start the API with ``CORREO_GOOGLE_AUTH_URL=http://127.0.0.1:8146/o/oauth2/v2/auth``,
``CORREO_GOOGLE_TOKEN_URL=http://127.0.0.1:8146/token``, ``CORREO_GOOGLE_REVOKE_URL=http://127.0.0.1:8146/revoke``
and ``CORREO_GMAIL_API_URL=http://127.0.0.1:8146/gmail/v1`` (it prints them).
"""

from __future__ import annotations

import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.parse import parse_qs, urlencode, urlsplit

from tests.modulos.correo import corpus
from tests.modulos.correo.falso import GoogleFalso


def construir(google: GoogleFalso, puerto: int = 0) -> ThreadingHTTPServer:
    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # quiet
            return

        def _responder(self, metodo: str) -> None:
            partes = urlsplit(self.path)
            consulta = {k: v if len(v) > 1 else v[0] for k, v in parse_qs(partes.query).items()}
            if partes.path == "/o/oauth2/v2/auth":
                self._consentimiento(consulta)
                return
            longitud = int(self.headers.get("Content-Length") or 0)
            cuerpo = self.rfile.read(longitud).decode() if longitud else ""
            datos = {k: v[0] for k, v in parse_qs(cuerpo).items()}
            cabeceras = {"Authorization": self.headers.get("Authorization", "")}
            respuesta = google(
                metodo, f"http://127.0.0.1{partes.path}", params=consulta, datos=datos, cabeceras=cabeceras
            )
            self.send_response(respuesta.estado)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(respuesta.cuerpo)))
            self.end_headers()
            self.wfile.write(respuesta.cuerpo)

        def _consentimiento(self, consulta: dict) -> None:  # type: ignore[type-arg]
            """The consent screen: «Permitir» registers the PKCE challenge and returns a code, like Google."""
            codigo = f"codigo-{len(google.codigos) + 1}"
            google.codigos[codigo] = str(consulta.get("code_challenge", ""))
            destino = str(consulta.get("redirect_uri", ""))
            permitir = f"{destino}?{urlencode({'state': consulta.get('state', ''), 'code': codigo, 'scope': consulta.get('scope', '')})}"
            cancelar = f"{destino}?{urlencode({'state': consulta.get('state', ''), 'error': 'access_denied'})}"
            pagina = (
                "<!doctype html><html lang='es'><meta charset='utf-8'><title>Google (simulado)</title>"
                "<body style='font-family:sans-serif;max-width:420px;margin:40px auto'>"
                "<h1>Google (servidor de pruebas)</h1>"
                f"<p>La aplicación pide: <code>{html.escape(str(consulta.get('scope', '')))}</code></p>"
                f"<p><a id='permitir' href='{html.escape(permitir)}'>Permitir</a> · "
                f"<a id='cancelar' href='{html.escape(cancelar)}'>Cancelar</a></p></body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(pagina)))
            self.end_headers()
            self.wfile.write(pagina)

        def do_GET(self) -> None:  # noqa: N802
            self._responder("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._responder("POST")

    return ThreadingHTTPServer(("127.0.0.1", puerto), Manejador)


def en_hilo(google: GoogleFalso) -> tuple[ThreadingHTTPServer, str]:
    servidor = construir(google)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor, f"http://127.0.0.1:{servidor.server_address[1]}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--puerto", type=int, default=8146)
    args = parser.parse_args()
    google = GoogleFalso(mensajes=corpus.todos())
    servidor = construir(google, args.puerto)
    base = f"http://127.0.0.1:{args.puerto}"
    print(f"CORREO_GOOGLE_AUTH_URL={base}/o/oauth2/v2/auth")
    print(f"CORREO_GOOGLE_TOKEN_URL={base}/token")
    print(f"CORREO_GOOGLE_REVOKE_URL={base}/revoke")
    print(f"CORREO_GMAIL_API_URL={base}/gmail/v1", flush=True)
    servidor.serve_forever()


if __name__ == "__main__":
    main()
