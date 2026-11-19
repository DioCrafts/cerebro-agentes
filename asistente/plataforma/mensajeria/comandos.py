"""The chat's commands (Spanish, with a slash): everything that is not a question for the brain.

Only the user's own typed text is a command: a forwarded or quoted message never is (it is data). Commands never call
the brain; ``/consejo`` only CREATES a convocation with its estimate (exactly what the brain's ``convocar_consejo``
tool and the app's button do) and nothing runs until it is confirmed with its one-time code. An unknown command
answers with the help.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from asistente.plataforma.mensajeria import confirmaciones, formato
from asistente.plataforma.mensajeria.almacen import Vinculo

if TYPE_CHECKING:  # pragma: no cover
    from asistente.plataforma.mensajeria.pasarela import Pasarela

logger = logging.getLogger(__name__)

ALIAS: dict[str, str] = {
    "ayuda": "ayuda",
    "help": "ayuda",
    "start": "ayuda",
    "comandos": "ayuda",
    "nueva": "nueva",
    "nuevo": "nueva",
    "cifras": "cifras",
    "fuentes": "fuentes",
    "pendientes": "pendientes",
    "confirmar": "confirmar",
    "rechazar": "rechazar",
    "consejo": "consejo",
    "estado": "estado",
    "pausa": "pausa",
    "pausar": "pausa",
    "reanudar": "reanudar",
    "desemparejar": "desemparejar",
    "desvincular": "desemparejar",
}

TEXTO_AYUDA = (
    "**Lo que puedo hacer aquí**\n"
    "Escríbeme tu pregunta como en la app: consulto a los especialistas que hagan falta.\n\n"
    "/nueva — empezar una conversación nueva\n"
    "/cifras — de dónde sale cada cifra de mi última respuesta\n"
    "/fuentes — las fuentes que cité\n"
    "/pendientes — acciones y consultas al consejo que esperan tu confirmación\n"
    "/confirmar 1234 · /rechazar 1234 — decidir una de ellas con el código que te doy (nunca basta con «sí»)\n"
    "/consejo y tu pregunta — proponer una decisión difícil al consejo (te digo antes el coste)\n"
    "/estado — cómo está este chat\n"
    "/pausa · /reanudar — parar o reanudar los avisos aquí\n"
    "/desemparejar — desvincular este chat\n\n"
    "Lo que reenvíes o cites lo leo como datos, no como palabras tuyas. Aún no puedo escuchar notas de voz."
)


@dataclass(frozen=True)
class Comando:
    nombre: str  # canonical name, or "desconocido"
    argumento: str


def parsear(texto: str) -> Comando | None:
    """``/nombre argumento`` → :class:`Comando`; None when the text is not a command."""
    limpio = (texto or "").strip()
    if not limpio.startswith("/") or len(limpio) < 2:
        return None
    cabeza, _, resto = limpio[1:].partition(" ")
    nombre = cabeza.split("@", 1)[0].strip().lower()
    if not nombre or not nombre.isalpha():
        return None
    return Comando(ALIAS.get(nombre, "desconocido"), " ".join(resto.split()))


class Comandos:
    """The handlers; each returns the texts to send back (already in the channel's rich text)."""

    def __init__(self, pasarela: Pasarela) -> None:
        self._p = pasarela

    def ejecutar(self, vinculo: Vinculo, comando: Comando) -> list[str]:
        manejador = getattr(self, f"_{comando.nombre}", None)
        if manejador is None:
            return [TEXTO_AYUDA]
        try:
            respuesta: list[str] = manejador(vinculo, comando.argumento)
            return respuesta
        except Exception:
            logger.exception("messaging: command /%s failed", comando.nombre)
            return ["No he podido hacerlo por un error interno. No se ha cambiado nada; inténtalo de nuevo."]

    # -- the commands ---------------------------------------------------------------------------------------

    def _ayuda(self, vinculo: Vinculo, argumento: str) -> list[str]:
        return [TEXTO_AYUDA]

    def _nueva(self, vinculo: Vinculo, argumento: str) -> list[str]:
        self._p.nueva_conversacion(vinculo)
        return [
            "Empezamos una conversación nueva. Lo que hablamos antes no se pierde: lo resumo en tu memoria (puedes "
            "verlo y borrarlo en la app, en «Lo que sé de ti»)."
        ]

    def _cifras(self, vinculo: Vinculo, argumento: str) -> list[str]:
        ultima = self._p.ultima_respuesta(vinculo)
        if ultima is None:
            return ["Aún no tengo una respuesta reciente en este chat. Pregúntame algo y luego escribe /cifras."]
        return [formato.texto_cifras(ultima.informe)]

    def _fuentes(self, vinculo: Vinculo, argumento: str) -> list[str]:
        ultima = self._p.ultima_respuesta(vinculo)
        if ultima is None:
            return ["Aún no tengo una respuesta reciente en este chat. Pregúntame algo y luego escribe /fuentes."]
        return [formato.texto_fuentes(ultima.citas)]

    def _pendientes(self, vinculo: Vinculo, argumento: str) -> list[str]:
        entorno = self._p.entorno
        pendientes = confirmaciones.pendientes_de(entorno, vinculo)
        if not pendientes:
            return ["No tienes nada pendiente de confirmar."]
        momento = self._p.reloj()
        lineas = ["**Pendiente de tu confirmación**"]
        lineas += [confirmaciones.ofrecer(entorno, vinculo, p, momento) for p in pendientes[:10]]
        if len(pendientes) > 10:
            lineas.append(f"…y {len(pendientes) - 10} más en la app.")
        return ["\n".join(lineas)]

    def _confirmar(self, vinculo: Vinculo, argumento: str) -> list[str]:
        return [confirmaciones.decidir(self._p.entorno, vinculo, argumento, confirmar=True, momento=self._p.reloj())]

    def _rechazar(self, vinculo: Vinculo, argumento: str) -> list[str]:
        return [confirmaciones.decidir(self._p.entorno, vinculo, argumento, confirmar=False, momento=self._p.reloj())]

    def _consejo(self, vinculo: Vinculo, argumento: str) -> list[str]:
        if not argumento:
            return ["Escribe la decisión después del comando, por ejemplo: /consejo ¿Amortizo la hipoteca o invierto?"]
        return [self._p.convocar_consejo(vinculo, argumento)]

    def _estado(self, vinculo: Vinculo, argumento: str) -> list[str]:
        return [self._p.texto_estado(vinculo)]

    def _pausa(self, vinculo: Vinculo, argumento: str) -> list[str]:
        self._p.entorno.almacen.pausar(vinculo.id, True)
        return ["Avisos en pausa: no te enviaré avisos por aquí hasta que escribas /reanudar. Siguen en la app."]

    def _reanudar(self, vinculo: Vinculo, argumento: str) -> list[str]:
        self._p.entorno.almacen.pausar(vinculo.id, False)
        return ["Avisos reanudados: te enviaré aquí los urgentes, el repaso semanal y los dictámenes del consejo."]

    def _desemparejar(self, vinculo: Vinculo, argumento: str) -> list[str]:
        entorno = self._p.entorno
        momento = self._p.reloj()
        if not argumento:
            codigo = confirmaciones.emitir(entorno.almacen, vinculo, "desemparejar", vinculo.id, momento)
            return [
                "Si desvinculas este chat dejaré de responder aquí y de enviarte avisos (lo guardado en la app no "
                f"cambia). Para hacerlo escribe: /desemparejar {codigo}"
            ]
        canjeado = confirmaciones.canjear(entorno.almacen, vinculo, argumento, ("desemparejar",), momento)
        if canjeado is None or canjeado[1] != vinculo.id:
            return [confirmaciones.TEXTO_CODIGO_NO_VALE.replace("/pendientes", "/desemparejar")]
        self._p.desemparejar(vinculo, motivo="chat")
        return []  # the gateway says goodbye and removes the contact


__all__ = ["ALIAS", "TEXTO_AYUDA", "Comando", "Comandos", "parsear"]
