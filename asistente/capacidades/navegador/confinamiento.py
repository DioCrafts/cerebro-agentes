"""Confinement of the Chromium process, below the browser's own policy (``seguridad.py``).

Route interception decides every request the pages make, but it lives inside the browser. This module adds what
does not depend on the browser behaving:

* **Name resolution.** ``--host-resolver-rules`` maps every host to «not found» except the run's own: whatever
  bypasses interception (a prefetch, a service the browser starts on its own) cannot reach another host by name.
* **No background traffic.** Component updates, sync, metrics, first-run and default-browser checks are off.
* **The file system (macOS).** The process runs under ``sandbox-exec`` with a profile that denies reading and
  writing the user's home (their real browser profiles, keychains, ``~/.ssh``, documents) except the browser's own
  files, and so a page pointed at ``file:///Users/…`` gets «access denied».

What it does NOT cover, so nobody assumes it: connections to an IP address written in a page (interception
blocks those, the resolver rule does not); other processes of the same user through IPC, which the profile's
``(allow default)`` leaves open; Linux, where no equivalent is bundled (the deployment's container and
Chromium's own sandbox are the boundary there). The profile denies the home and allows the rest, so writing
outside the home — ``/tmp``, another volume — and reading ``/etc`` or ``/Library/Keychains`` stay allowed.

The three modes of ``NAVEGADOR_CONFINAMIENTO``, since only one of them fails closed:

* ``auto`` (the default) wraps the process where it can and runs it unwrapped where it cannot, which is every
  platform but macOS and a macOS without ``sandbox-exec``. The run is not refused; it reports
  ``confinado="ninguno"`` and ``ejecutor.py`` tells the person so in the task's log.
* ``sandbox-exec`` refuses to run when it cannot wrap the process (:class:`ConfinamientoError`). This is the
  mode to set when an unconfined browser is not acceptable; Apple has deprecated ``sandbox-exec`` but still
  ships it, and the day it goes the tasks stop instead of going unconfined.
* ``ninguno`` never wraps it, and says nothing, because the operator chose it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shutil
import stat
import sys
from typing import Literal
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

Modo = Literal["auto", "sandbox-exec", "ninguno"]
SANDBOX_EXEC = "/usr/bin/sandbox-exec"

# Flags that stop the browser from talking to anyone on its own.
ARGUMENTOS_FIJOS = (
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-default-apps",
    "--disable-domain-reliability",
    "--disable-breakpad",
    "--no-first-run",
    "--no-default-browser-check",
    "--no-pings",
    "--metrics-recording-only",
    # WebRTC could otherwise open UDP flows that no route interception sees.
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
)


class ConfinamientoError(RuntimeError):
    """The confinement the operator requires cannot be applied (message in Spanish)."""


@dataclass(frozen=True)
class Lanzamiento:
    """How to launch Chromium for one run."""

    ejecutable: str | None  # None: Playwright's own browser, unwrapped
    argumentos: tuple[str, ...]
    descargas: str
    # "sandbox-exec" when the process is wrapped; "ninguno" otherwise.
    confinado: str


def modo_de(valor: object) -> Modo:
    """``NAVEGADOR_CONFINAMIENTO`` as a mode; anything unknown is ``auto``."""
    texto = str(valor or "auto").strip().lower()
    if texto == "sandbox-exec":
        return "sandbox-exec"
    return "ninguno" if texto == "ninguno" else "auto"


def hosts_de(origenes: Iterable[str]) -> list[str]:
    """Host names of the allowed origins (no ports, IPv6 without brackets), sorted and unique."""
    hosts = {(urlsplit(origen).hostname or "").lower() for origen in origenes}
    return sorted(h for h in hosts if h)


def argumentos_de_red(origenes: Iterable[str]) -> tuple[str, ...]:
    """Chromium flags that restrict name resolution to the run's hosts and silence background traffic."""
    excluidos = "".join(f", EXCLUDE {host}" for host in hosts_de(origenes))
    return (f"--host-resolver-rules=MAP * ~NOTFOUND{excluidos}", *ARGUMENTOS_FIJOS)


def _sb(ruta: str) -> str:
    """A path as an SBPL string literal."""
    return '"' + ruta.replace("\\", "\\\\").replace('"', '\\"') + '"'


def perfil_macos(*, hogar: str, navegador: str, temporal: str) -> str:
    """The ``sandbox-exec`` profile: everything Chromium needs, minus the user's home.

    Later rules win in SBPL: the home is denied as a whole, then the browser's own directory is allowed again
    (it usually lives under ``~/Library/Caches/ms-playwright``) and so is the run's temporary directory when it
    happens to be under the home. Metadata (``stat``) stays readable: the browser walks its own path.
    """
    reglas = [
        "(version 1)",
        "(allow default)",
        f"(deny file-read* (subpath {_sb(hogar)}))",
        f"(deny file-write* (subpath {_sb(hogar)}))",
        f"(allow file-read-metadata (subpath {_sb(hogar)}))",
        f"(allow file-read* (subpath {_sb(navegador)}))",
        f"(allow file-read* file-write* (subpath {_sb(temporal)}))",
    ]
    return "\n".join(reglas) + "\n"


def _raiz_del_navegador(ejecutable: str) -> str:
    """The directory to keep readable: the ``.app`` bundle's parent on macOS, else the executable's directory."""
    ruta = Path(ejecutable).resolve()
    for padre in ruta.parents:
        if padre.suffix == ".app":
            return str(padre.parent)
    return str(ruta.parent)


def preparar(
    temporal: Path,
    *,
    origenes: Iterable[str],
    ejecutable: str | None,
    ejecutable_por_defecto: str | None = None,
    plataforma: str | None = None,
    modo: object = "auto",
) -> Lanzamiento:
    """Everything a run's launch needs, written under ``temporal`` (deleted with it by the caller).

    ``ejecutable`` is the operator's Chromium (``NAVEGADOR_CHROMIUM``) or None; ``ejecutable_por_defecto`` is
    Playwright's own, needed only to wrap it. Raises :class:`ConfinamientoError` when ``sandbox-exec`` is
    required and cannot be used.
    """
    plataforma = plataforma or sys.platform
    descargas = temporal / "descargas"
    descargas.mkdir(parents=True, exist_ok=True)
    argumentos = argumentos_de_red(origenes)
    pedido = modo_de(modo)
    real = ejecutable or ejecutable_por_defecto
    disponible = (
        plataforma == "darwin" and os.access(SANDBOX_EXEC, os.X_OK) and real is not None and Path(real).exists()
    )
    if pedido == "ninguno" or (pedido == "auto" and not disponible):
        if pedido == "auto" and plataforma == "darwin":
            logger.warning("sandbox-exec is not available: the browser runs without file-system confinement")
        return Lanzamiento(ejecutable=ejecutable, argumentos=argumentos, descargas=str(descargas), confinado="ninguno")
    if not disponible:
        raise ConfinamientoError(
            "NAVEGADOR_CONFINAMIENTO=sandbox-exec, pero este equipo no lo tiene (solo existe en macOS). "
            "La tarea no se ejecuta."
        )
    assert real is not None
    perfil = temporal / "chromium.sb"
    perfil.write_text(
        perfil_macos(hogar=str(Path.home()), navegador=_raiz_del_navegador(real), temporal=str(temporal.resolve())),
        encoding="utf-8",
    )
    lanzador = temporal / "chromium-confinado"
    # ``exec`` keeps Playwright's pipes and signals pointed at Chromium itself.
    lanzador.write_text(f'#!/bin/sh\nexec {SANDBOX_EXEC} -f {_sh(str(perfil))} {_sh(real)} "$@"\n', encoding="utf-8")
    lanzador.chmod(lanzador.stat().st_mode | stat.S_IXUSR)
    return Lanzamiento(
        ejecutable=str(lanzador), argumentos=argumentos, descargas=str(descargas), confinado="sandbox-exec"
    )


def _sh(texto: str) -> str:
    return "'" + texto.replace("'", "'\\''") + "'"


def disponible(modo: object = "auto") -> bool:
    """Whether this machine confines the browser's file access under ``modo`` (``/navegador/estado``)."""
    if modo_de(modo) == "ninguno" or sys.platform != "darwin":
        return False
    return shutil.which("sandbox-exec", path="/usr/bin") is not None


__all__ = [
    "ARGUMENTOS_FIJOS",
    "ConfinamientoError",
    "Lanzamiento",
    "argumentos_de_red",
    "disponible",
    "hosts_de",
    "modo_de",
    "perfil_macos",
    "preparar",
]
