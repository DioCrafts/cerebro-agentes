"""Pairing a chat with its owner: a one-time code generated in the app, typed in the chat.

WHY a code typed in the chat, and not «whoever finds the bot's address»: the bot's address is public by nature (it is a
QR anyone can scan), so reaching the bot proves nothing. What proves the chat is the owner's is a secret only the
signed-in owner can see in the app, sent from that chat. The gates, all deterministic:

* **High entropy, short-lived, single use.** 12 symbols of Crockford's base32 (60 bits), with at least two digits and
  two letters so that ordinary words are never mistaken for a code; valid ``MENSAJERIA_CODIGO_MINUTOS`` (10); spent
  by the first chat that sends it (one conditional ``UPDATE`` in the same transaction that creates the binding); a
  new code from the app voids the previous one.
* **Stored hashed.** Only the SHA-256 of the normalised code is in the database.
* **Brute force is pointless.** Failed attempts are limited per contact (``MENSAJERIA_INTENTOS_CONTACTO_HORA``) and
  for every contact together (``MENSAJERIA_INTENTOS_GLOBAL_HORA``); past either limit codes are not even checked.
  With 2^60 codes, 10 minutes of life and a handful of attempts per hour, guessing is not a strategy.
* **Typed, not forwarded.** A code inside a forwarded or quoted message is data, never a pairing.
* **Nothing for strangers.** A contact that is not paired gets ONE fixed sentence a day at most, and nothing else:
  no brain call, no model cost, no data about anyone (:data:`TEXTO_PRIVADO`).

The binding is (user, case) of the code's owner; ``MENSAJERIA_CONTACTOS_POR_USUARIO`` chats per user and channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
import secrets
from typing import Literal

from asistente.plataforma import settings
from asistente.plataforma.mensajeria.almacen import AlmacenMensajeria, Vinculo, huella
from asistente.plataforma.mensajeria.contrato import MensajeEntrante

ALFABETO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford's base32: no I, L, O, U
LONGITUD = 12
_DIGITOS = frozenset("0123456789")
# Crockford's decoding: letters people confuse with digits are read as the digit.
_EQUIVALENCIAS = str.maketrans({"O": "0", "I": "1", "L": "1"})
# Every code-shaped run of 12 symbols (in groups of four, optionally separated), not glued to a word (``\w`` is
# Unicode: «código XXXX…» does not lend «digo»). A lookahead, so overlapping runs are all seen: «ABCD 7K3M-9QXP-2D0R»
# yields both «ABCD7K3M9QXP» and the real code.
_CANDIDATO = re.compile(r"(?<!\w)(?=([0-9A-Za-z]{4}[-\s.]?[0-9A-Za-z]{4}[-\s.]?[0-9A-Za-z]{4})(?!\w))")
MAX_CANDIDATOS = 5

TEXTO_PRIVADO = "Este asistente es privado. Si eres su dueño, empareja este chat desde la app."
TEXTO_BIENVENIDA = (
    "¡Hecho! Este chat está vinculado a **tu hogar**. Pregúntame lo que quieras, como en la app: consulto a los "
    "especialistas que hagan falta y te digo de dónde sale cada cifra.\n\n"
    "Escribe /ayuda para ver lo que puedo hacer, o /desemparejar si quieres desvincular este chat (también desde la "
    "app, en «Mensajería»)."
)
TEXTO_CODIGO_INVALIDO = (
    "Ese código no es válido o ha caducado. Genera uno nuevo en la app («Mensajería») y escríbemelo aquí."
)
TEXTO_LIMITE = (
    "Ya tienes el máximo de chats vinculados en esta aplicación de mensajería. Desvincula uno desde la app "
    "(«Mensajería») y vuelve a enviarme el código."
)

ResultadoChat = Literal["emparejado", "invalido", "limitado", "limite", "sin_codigo", "silencio"]


def generar_codigo() -> str:
    """A fresh code as the app shows it: ``XXXX-XXXX-XXXX`` (at least two digits and two letters)."""
    while True:
        simbolos = "".join(secrets.choice(ALFABETO) for _ in range(LONGITUD))
        digitos = sum(1 for c in simbolos if c in _DIGITOS)
        if 2 <= digitos <= LONGITUD - 2:
            return f"{simbolos[:4]}-{simbolos[4:8]}-{simbolos[8:]}"


def normalizar(texto: str) -> str | None:
    """The canonical form of a typed code (upper case, no separators, Crockford's equivalences), or None."""
    crudo = re.sub(r"[-\s.]", "", texto).upper()
    limpio = crudo.translate(_EQUIVALENCIAS)
    if len(limpio) != LONGITUD or any(c not in ALFABETO for c in limpio):
        return None
    # Counted as typed (before the look-alikes are read as digits): a word is not a code.
    digitos = sum(1 for c in crudo if c in _DIGITOS)
    if digitos < 2 or digitos > LONGITUD - 2:
        return None
    return limpio


def codigos_en(texto: str) -> list[str]:
    """Code-shaped tokens of a message, normalised (at most :data:`MAX_CANDIDATOS`)."""
    salida: list[str] = []
    for coincidencia in _CANDIDATO.finditer(texto or ""):
        normal = normalizar(coincidencia.group(1))
        if normal is not None and normal not in salida:
            salida.append(normal)
        if len(salida) >= MAX_CANDIDATOS:
            break
    return salida


def huella_codigo(normalizado: str) -> str:
    return huella("codigo", normalizado)


def huella_contacto(canal: str, contacto: str) -> str:
    return huella("contacto", canal, contacto)


def minutos_codigo() -> int:
    return max(1, int(getattr(settings, "MENSAJERIA_CODIGO_MINUTOS", 10)))


@dataclass(frozen=True)
class CodigoNuevo:
    codigo: str  # shown once, in the app
    caduca: datetime


def nuevo_codigo(
    almacen: AlmacenMensajeria, *, canal: str, user_id: str | None, case_id: str, momento: datetime
) -> CodigoNuevo:
    """Generate and store (hashed) a pairing code for (user, case) on ``canal``."""
    codigo = generar_codigo()
    normal = normalizar(codigo)
    assert normal is not None
    caduca = momento + timedelta(minutes=minutos_codigo())
    almacen.crear_codigo(
        huella_codigo=huella_codigo(normal),
        canal=canal,
        user_id=user_id,
        case_id=case_id,
        caduca=caduca,
        momento=momento,
    )
    return CodigoNuevo(codigo=codigo, caduca=caduca)


@dataclass(frozen=True)
class AtencionDesconocido:
    """What the gateway does with a message from a contact that is not paired."""

    resultado: ResultadoChat
    respuesta: str | None = None  # the one sentence to send back (None: stay silent)
    vinculo: Vinculo | None = None


def _limitado(almacen: AlmacenMensajeria, huella_c: str, momento: datetime) -> bool:
    hora = momento - timedelta(hours=1)
    por_contacto = max(1, int(getattr(settings, "MENSAJERIA_INTENTOS_CONTACTO_HORA", 5)))
    global_ = max(1, int(getattr(settings, "MENSAJERIA_INTENTOS_GLOBAL_HORA", 30)))
    return (
        almacen.contar_intentos("codigo_fallido", hora, huella_contacto=huella_c) >= por_contacto
        or almacen.contar_intentos("codigo_fallido", hora) >= global_
    )


def _privado_una_vez_al_dia(almacen: AlmacenMensajeria, huella_c: str, momento: datetime) -> AtencionDesconocido:
    """The fixed sentence, at most once a day per contact; silence otherwise."""
    if almacen.contar_intentos("respuesta_desconocido", momento - timedelta(days=1), huella_contacto=huella_c):
        return AtencionDesconocido("silencio")
    almacen.anotar_intento("respuesta_desconocido", huella_c, momento)
    return AtencionDesconocido("sin_codigo", TEXTO_PRIVADO)


def atender_desconocido(almacen: AlmacenMensajeria, mensaje: MensajeEntrante, momento: datetime) -> AtencionDesconocido:
    """A message from an unpaired contact: pair it if it carries a live code, else the fixed sentence once a day.

    Never calls a model and never says anything about anybody: the answers are fixed sentences."""
    huella_c = huella_contacto(mensaje.canal, mensaje.contacto)
    candidatos = [] if mensaje.reenviado else codigos_en(mensaje.texto)
    if not candidatos:
        return _privado_una_vez_al_dia(almacen, huella_c, momento)
    if _limitado(almacen, huella_c, momento):
        # Past the limits nothing is checked; the contact hears at most the daily sentence.
        atencion = _privado_una_vez_al_dia(almacen, huella_c, momento)
        return AtencionDesconocido("limitado", atencion.respuesta)
    maximo = max(1, int(getattr(settings, "MENSAJERIA_CONTACTOS_POR_USUARIO", 1)))
    for candidato in candidatos:
        resultado = almacen.emparejar(
            huella_codigo=huella_codigo(candidato),
            canal=mensaje.canal,
            contacto=mensaje.contacto,
            nombre_mostrado=mensaje.nombre_mostrado,
            maximo_por_usuario=maximo,
            momento=momento,
        )
        if resultado.resultado == "ok":
            return AtencionDesconocido("emparejado", TEXTO_BIENVENIDA, resultado.vinculo)
        if resultado.resultado == "limite":
            return AtencionDesconocido("limite", TEXTO_LIMITE)
        if resultado.resultado == "ya_vinculado":  # a message racing this one paired the contact first
            return AtencionDesconocido("silencio")
    almacen.anotar_intento("codigo_fallido", huella_c, momento)
    return AtencionDesconocido("invalido", TEXTO_CODIGO_INVALIDO)


__all__ = [
    "ALFABETO",
    "LONGITUD",
    "TEXTO_BIENVENIDA",
    "TEXTO_CODIGO_INVALIDO",
    "TEXTO_LIMITE",
    "TEXTO_PRIVADO",
    "AtencionDesconocido",
    "CodigoNuevo",
    "atender_desconocido",
    "codigos_en",
    "generar_codigo",
    "huella_codigo",
    "huella_contacto",
    "minutos_codigo",
    "normalizar",
    "nuevo_codigo",
]
