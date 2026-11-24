import os
import socket
import tempfile

import pytest

# The format watch (asistente/conocimiento/formatos.py) records every real read under ./data by default.
# Switched off through the environment, before ``settings`` is first imported, so that it also holds
# for module- and session-scoped fixtures and survives the tests that reload ``settings``.
os.environ["FORMAT_WATCH"] = "false"
# The API's edge defaults (asistente/plataforma/api/security.py) are for real deployments: the TestClient's
# host is "testserver", one process sends thousands of requests and most tests run without authentication.
# Tests of those defaults set or remove these variables themselves.
os.environ.setdefault("API_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
os.environ.setdefault("API_RATE_LIMIT_PER_MIN", "0")
os.environ.setdefault("PERMITIR_SIN_AUTENTICACION", "1")
os.environ["FORMAT_STATE_PATH"] = os.path.join(tempfile.gettempdir(), "hacienda-tests-formatos", "_estado.json")
os.environ["FORMAT_DIR"] = os.path.join(tempfile.gettempdir(), "hacienda-tests-formatos", "formatos")
# Same for the baseline of the household modules' BOE sources (params-check).
os.environ["MODULOS_FUENTES_STATE_PATH"] = os.path.join(
    tempfile.gettempdir(), "hacienda-tests-formatos", "_modulos_fuentes.json"
)


_LOCALES = {"127.0.0.1", "::1", "localhost"}
_connect_real = socket.socket.connect
_connect_ex_real = socket.socket.connect_ex


def _destino_local(sock: socket.socket, direccion) -> bool:
    if sock.family == getattr(socket, "AF_UNIX", None):
        return True
    host = direccion[0] if isinstance(direccion, tuple) else direccion
    return str(host) in _LOCALES


def _connect_vigilado(self, direccion):
    if not _destino_local(self, direccion):
        raise RuntimeError(f"Test sin red: conexión a {direccion!r} bloqueada (márcalo con @pytest.mark.en_vivo)")
    return _connect_real(self, direccion)


def _connect_ex_vigilado(self, direccion):
    if not _destino_local(self, direccion):
        raise RuntimeError(f"Test sin red: conexión a {direccion!r} bloqueada (márcalo con @pytest.mark.en_vivo)")
    return _connect_ex_real(self, direccion)


@pytest.fixture(autouse=True)
def _sin_red(request, monkeypatch):
    """Ningún test sale de 127.0.0.1 salvo los marcados ``en_vivo``."""
    if request.node.get_closest_marker("en_vivo") is not None:
        return
    monkeypatch.setattr(socket.socket, "connect", _connect_vigilado)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex_vigilado)


@pytest.fixture(autouse=True)
def _sin_claves_ni_estado_real(monkeypatch, tmp_path):
    """Ni claves reales de proveedores ni la base de casos de ./data: cada test usa la suya en tmp_path."""
    from asistente.plataforma import settings

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ruta = str(tmp_path / "decision_state.sqlite3")
    monkeypatch.setenv("DECISION_STATE_DB_PATH", ruta)
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", ruta, raising=False)


@pytest.fixture(autouse=True)
def _formatos_sin_rastro(monkeypatch, tmp_path):
    """The format watch writes state and samples on every real read: keep tests from touching ./data."""
    from asistente.plataforma import settings

    monkeypatch.setattr(settings, "FORMAT_WATCH", False, raising=False)
    monkeypatch.setattr(settings, "FORMAT_STATE_PATH", str(tmp_path / "_formatos_state.json"), raising=False)
    monkeypatch.setattr(settings, "FORMAT_DIR", str(tmp_path / "formatos"), raising=False)


@pytest.fixture(autouse=True)
def _registro_de_modulos_sin_rastro():
    """No test leaves its module registry behind: a fake one (``reiniciar(nuevo)``), or a real one built while
    the test had patched settings (``MODULOS_DESACTIVADOS``…), is forgotten after the test, so the next test
    discovers the real modules again. Tests that leave the registry untouched pay nothing."""
    from asistente.nucleo.modulos import registro

    antes = list(registro._cache)
    yield
    despues = list(registro._cache)
    if len(antes) != len(despues) or any(a is not d for a, d in zip(antes, despues, strict=False)):
        registro.reiniciar()


@pytest.fixture(autouse=True)
def _sin_red_por_el_cliente_comun(monkeypatch):
    """No test reaches the network through the shared HTTP client: any request it makes fails the test,
    unless the test installs answers with ``red_falsa``."""
    import httpx

    from asistente.plataforma import http

    def _prohibido(request):
        pytest.fail(f"network access in a test: {request.method} {request.url}")

    monkeypatch.setattr(
        http, "_por_defecto", [http.Cliente(transport=httpx.MockTransport(_prohibido), dormir=lambda _s: None)]
    )


@pytest.fixture(autouse=True)
def hindsight():
    """Every test's conversation memory talks to an in-memory Hindsight (``tests/recuerdos/falso.py``), never to the
    embedded daemon. Tests that check what was retained or recalled take this fixture and inspect it."""
    from asistente.nucleo.recuerdos import servicio
    from tests.recuerdos.falso import HindsightFalso

    falso = HindsightFalso()
    servicio.configurar_hindsight(falso.cliente, listo=lambda: True)
    yield falso
    servicio.configurar_hindsight(None)


@pytest.fixture
def red_falsa(monkeypatch):
    """``red_falsa(handler)`` answers every request of the shared HTTP client with ``handler(request)``
    (an ``httpx.Response``, or an exception to raise) and returns the list of requests seen."""
    import httpx

    from asistente.plataforma import http

    def instalar(handler, **opciones):
        vistas = []

        def _responder(request):
            vistas.append(request)
            salida = handler(request)
            if isinstance(salida, BaseException):
                raise salida
            return salida

        cliente = http.Cliente(transport=httpx.MockTransport(_responder), dormir=lambda _s: None, **opciones)
        monkeypatch.setattr(http, "_por_defecto", [cliente])
        return vistas

    return instalar
