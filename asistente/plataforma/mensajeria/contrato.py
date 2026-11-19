"""Shared types between the messaging gateway and the channel adapters.

The gateway (``asistente.plataforma.mensajeria.pasarela``) is channel-agnostic: pairing, authorisation, the
conversation with the brain, confirmations, attachments, outgoing notices, limits, audit, export and erasure. A
channel adapter (``asistente.plataforma.mensajeria.simplex`` first) only moves messages: it turns what the channel
delivers into ``MensajeEntrante`` and sends ``MensajeSaliente`` / edits. Adapters never decide who may talk to the
brain and never call a model.

Identity: ``contacto`` is the adapter's opaque, stable id of the remote party on that channel (for SimpleX the
contact id the local CLI assigned). It is NOT a person: it becomes the user's only after pairing (a one-time code
generated in the app and sent from that contact), and the gateway stores the binding.

Everything that arrives is untrusted until paired; even after pairing, forwarded or quoted content and attachments
are data, never instructions (the brain's memory and gates already treat them so).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

NombreCanal = Literal["simplex", "signal", "telegram", "prueba"]
MAX_TEXTO_ENTRANTE = 8000  # characters accepted from one incoming message (longer: truncated and flagged)
MAX_ADJUNTO_BYTES = 15 * 1024 * 1024


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdjuntoEntrante(_Modelo):
    """A file the contact sent, already received by the adapter into a private local directory."""

    nombre: str = Field(max_length=255)  # as the sender named it (untrusted: never used as a path)
    mime: str | None = Field(default=None, max_length=120)
    ruta_local: str  # where the adapter stored it (inside its private files dir)
    tamano: int = Field(ge=0, le=MAX_ADJUNTO_BYTES)


class MensajeEntrante(_Modelo):
    canal: NombreCanal
    contacto: str = Field(min_length=1, max_length=200)
    mensaje_id: str = Field(min_length=1, max_length=200)  # the channel's id (for replies, dedupe)
    texto: str = Field(default="", max_length=MAX_TEXTO_ENTRANTE)
    truncado: bool = False
    # The message was forwarded from someone else, or quotes another message: its text is DATA, not the user's words.
    reenviado: bool = False
    cita: str | None = Field(default=None, max_length=4000)
    adjuntos: list[AdjuntoEntrante] = Field(default_factory=list, max_length=10)
    # A voice note (future: local transcription); kept as an attachment too.
    es_voz: bool = False
    recibido: datetime
    # The display name the contact chose on the channel (untrusted, for the UI only; never shown to the brain).
    nombre_mostrado: str | None = Field(default=None, max_length=200)


class MensajeSaliente(_Modelo):
    contacto: str
    texto: str = Field(min_length=1, max_length=16_000)  # the adapter splits it if the channel's limit is lower
    responde_a: str | None = None  # mensaje_id to quote/reply to, when the channel supports it


class CapacidadesCanal(_Modelo):
    edicion: bool  # can edit a sent message (progress → final answer in place)
    adjuntos_entrantes: bool
    adjuntos_salientes: bool
    max_texto: int = Field(ge=200)
    cifrado_extremo_a_extremo: bool
    # Rich text the channel renders (SimpleX: *bold*, _italic_, `code`, links); the gateway formats to it.
    formato: Literal["simplex", "markdown", "texto"]


class EstadoCanal(_Modelo):
    canal: NombreCanal
    conectado: bool
    detalle: str = ""  # Spanish, for the operator/UI («Conectado a simplex-chat 6.x en 127.0.0.1:5225»)
    version: str | None = None
    # How a user reaches the bot to pair (SimpleX: the bot's contact address link / QR content).
    direccion: str | None = None
    ultimo_error: str | None = None
    actualizado: datetime


Manejador = Callable[[MensajeEntrante], None]


@runtime_checkable
class Canal(Protocol):
    """A channel adapter. Implementations are thread-safe and never raise from ``enviar``/``editar`` (they return
    None/False and log); ``iniciar`` runs until ``parar`` and reconnects on its own with backoff."""

    nombre: NombreCanal
    capacidades: CapacidadesCanal

    def iniciar(self, manejador: Manejador) -> None: ...

    def parar(self) -> None: ...

    def enviar(self, mensaje: MensajeSaliente) -> str | None: ...  # the sent message id, None on failure

    def editar(self, contacto: str, mensaje_id: str, texto: str) -> bool: ...

    def estado(self) -> EstadoCanal: ...

    def desconectar_contacto(self, contacto: str) -> bool: ...  # remove the contact on the channel (unpairing)


__all__ = [
    "MAX_ADJUNTO_BYTES",
    "MAX_TEXTO_ENTRANTE",
    "AdjuntoEntrante",
    "Canal",
    "CapacidadesCanal",
    "EstadoCanal",
    "Manejador",
    "MensajeEntrante",
    "MensajeSaliente",
    "NombreCanal",
]
