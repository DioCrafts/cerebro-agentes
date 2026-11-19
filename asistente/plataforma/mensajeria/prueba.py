"""An in-memory channel (``nombre="prueba"``) for the gateway's tests, the adapters' tests and local trials.

It implements :class:`~asistente.plataforma.mensajeria.contrato.Canal` with no network: :meth:`CanalPrueba.recibir`
plays an incoming message into the handler (as an adapter's receive loop would), and every message sent, edited or
contact removed is recorded for the assertions. ``edicion`` and ``max_texto`` are configurable, so a test covers both
an editing channel (progress edited into the answer) and a non-editing one (two messages). Thread-safe.

    canal = CanalPrueba(edicion=True)
    pasarela = Pasarela(db_path, canales=[canal], agente=lambda: cerebro)
    canal.iniciar_en_hilo(pasarela.manejar)
    canal.recibir("contacto-1", "Hola, mi código es ABCD-…")
    pasarela.esperar()
    assert canal.textos("contacto-1")[-1] == …
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import itertools
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4

from asistente.plataforma.mensajeria.contrato import (
    AdjuntoEntrante,
    CapacidadesCanal,
    EstadoCanal,
    Manejador,
    MensajeEntrante,
    MensajeSaliente,
    NombreCanal,
)


@dataclass
class Enviado:
    """A message the channel delivered to a contact (its text as it stands now: edits included)."""

    id: str
    contacto: str
    texto: str
    responde_a: str | None = None
    ediciones: list[str] = field(default_factory=list)


class CanalPrueba:
    """The in-memory channel. ``falla_envio``: every send fails (returns None), as a disconnected adapter would."""

    def __init__(
        self,
        *,
        nombre: NombreCanal = "prueba",
        edicion: bool = True,
        max_texto: int = 4000,
        formato: str = "simplex",
        adjuntos: bool = True,
        direccion: str | None = "https://simplex.chat/contact#/?v=2-7&smp=smp%3A%2F%2Fprueba",
    ) -> None:
        self.nombre: NombreCanal = nombre
        self.capacidades = CapacidadesCanal(
            edicion=edicion,
            adjuntos_entrantes=adjuntos,
            adjuntos_salientes=False,
            max_texto=max_texto,
            cifrado_extremo_a_extremo=True,
            formato=formato,  # type: ignore[arg-type]
        )
        self.direccion = direccion
        # The private folder its attachments are received into (one random folder per file, as the SimpleX adapter
        # does); set by :meth:`adjunto`. The gateway removes a file's folder with the file, never outside this one.
        self.directorio_archivos: Path | None = None
        self.falla_envio = False
        self.desconexion_falla = False
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._entrantes = itertools.count(1)
        self._manejador: Manejador | None = None
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        self.enviados: list[Enviado] = []
        self.desconectados: list[str] = []
        self.iniciado = threading.Event()

    # -- the Canal protocol -----------------------------------------------------------------------------------

    def iniciar(self, manejador: Manejador) -> None:
        """Runs until :meth:`parar` (as a real adapter's receive loop)."""
        self._manejador = manejador
        self._parar.clear()
        self.iniciado.set()
        self._parar.wait()

    def parar(self) -> None:
        self._parar.set()

    def enviar(self, mensaje: MensajeSaliente) -> str | None:
        with self._lock:
            if self.falla_envio:
                return None
            nuevo = Enviado(
                id=f"out-{next(self._ids)}",
                contacto=mensaje.contacto,
                texto=mensaje.texto,
                responde_a=mensaje.responde_a,
            )
            self.enviados.append(nuevo)
            return nuevo.id

    def editar(self, contacto: str, mensaje_id: str, texto: str) -> bool:
        if not self.capacidades.edicion:
            return False
        with self._lock:
            for enviado in self.enviados:
                if enviado.id == mensaje_id and enviado.contacto == contacto:
                    enviado.ediciones.append(enviado.texto)
                    enviado.texto = texto
                    return True
        return False

    def estado(self) -> EstadoCanal:
        return EstadoCanal(
            canal=self.nombre,
            conectado=self.iniciado.is_set() and not self._parar.is_set(),
            detalle="Canal de prueba en memoria.",
            version="prueba",
            direccion=self.direccion,
            actualizado=datetime.now(UTC),
        )

    def desconectar_contacto(self, contacto: str) -> bool:
        with self._lock:
            if self.desconexion_falla:
                return False
            self.desconectados.append(contacto)
            return True

    # -- driving it from a test ---------------------------------------------------------------------------------

    def iniciar_en_hilo(self, manejador: Manejador) -> threading.Thread:
        self._hilo = threading.Thread(target=self.iniciar, args=(manejador,), name="canal-prueba", daemon=True)
        self._hilo.start()
        self.iniciado.wait(5)
        return self._hilo

    def mensaje(
        self,
        contacto: str,
        texto: str = "",
        *,
        mensaje_id: str | None = None,
        reenviado: bool = False,
        cita: str | None = None,
        adjuntos: list[AdjuntoEntrante] | None = None,
        adjuntos_rechazados: list[str] | None = None,
        es_voz: bool = False,
        nombre_mostrado: str | None = None,
        truncado: bool = False,
    ) -> MensajeEntrante:
        return MensajeEntrante(
            canal=self.nombre,
            contacto=contacto,
            mensaje_id=mensaje_id or f"in-{next(self._entrantes)}",
            texto=texto,
            truncado=truncado,
            reenviado=reenviado,
            cita=cita,
            adjuntos=list(adjuntos or []),
            adjuntos_rechazados=list(adjuntos_rechazados or []),
            es_voz=es_voz,
            recibido=datetime.now(UTC),
            nombre_mostrado=nombre_mostrado,
        )

    def entregar(self, mensaje: MensajeEntrante) -> None:
        """Hand an incoming message to the handler (synchronously, as an adapter's receive loop does)."""
        if self._manejador is None:
            raise RuntimeError("el canal de prueba no está iniciado")
        self._manejador(mensaje)

    def recibir(self, contacto: str, texto: str = "", **opciones: Any) -> MensajeEntrante:
        """Build an incoming message (see :meth:`mensaje`) and deliver it."""
        entrante = self.mensaje(contacto, texto, **opciones)
        self.entregar(entrante)
        return entrante

    def adjunto(self, directorio: Path, nombre: str, contenido: bytes, mime: str | None = None) -> AdjuntoEntrante:
        """A file as an adapter would have received it into its private ``directorio``: in a random folder of its
        own (the sender's name is kept as data only, never used as a path)."""
        self.directorio_archivos = Path(directorio)
        carpeta = Path(directorio) / uuid4().hex
        carpeta.mkdir(mode=0o700)
        ruta = carpeta / "adjunto"
        ruta.write_bytes(contenido)
        return AdjuntoEntrante(nombre=nombre, mime=mime, ruta_local=str(ruta), tamano=len(contenido))

    def de(self, contacto: str) -> list[Enviado]:
        with self._lock:
            return [e for e in self.enviados if e.contacto == contacto]

    def textos(self, contacto: str) -> list[str]:
        return [e.texto for e in self.de(contacto)]


__all__ = ["CanalPrueba", "Enviado"]
