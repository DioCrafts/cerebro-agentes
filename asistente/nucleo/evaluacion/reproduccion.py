"""A provider that replays a case's reference script (``guion``): the suite's model-free mode.

It drives the REAL central agent, tools, specialists, evidence ledger and pending-action logic — only the
model's decisions come from the script. That proves, on every pull request and without any key, that each
case is satisfiable (its expected figures can appear with a citation), that the expectations are checked
correctly, and that routing to a specialist, the calculators and the ``actua`` guarantee still work.
It says nothing about how a real model behaves: that is what ``run`` with a provider measures.

The script has one list of turns per conversation: ``asesor`` (the central agent) and
``especialista:<clave>`` (each module specialist, consumed in order if it is consulted more than once).
Final texts may use ``{cifra:<nombre>}`` (the calculator's figure for that expectation) and
``{fragmento:<herramienta>}`` (the fragment id the latest call to that tool produced). The verifier is
answered with every cited sentence marked as supported: this mode does not test the verifier.
"""

from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Any

from hacienda_gpt.agent.providers.base import ModelTurn, ProviderError, TextCallback, ToolCall, ToolResult, ToolSpec
from hacienda_gpt.evaluacion.modelos import CasoEvaluacion, PasoGuion

_FRAGMENTO_RE = re.compile(r"\[(F\d+)\]")
_MARCADOR_RE = re.compile(r"\{(cifra|fragmento):([A-Za-z0-9_]+)\}")
_FRASE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
_USO = {"input_tokens": 0, "output_tokens": 0}


class _Conversacion:
    def __init__(self, proveedor: ProveedorReproduccion, clave: str) -> None:
        self._proveedor = proveedor
        self._clave = clave
        self._nombres: dict[str, str] = {}

    def step(self, *, allow_tools: bool = True, on_text: TextCallback | None = None) -> ModelTurn:
        cola = self._proveedor.colas.get(self._clave, [])
        if not cola:
            texto = "No tengo más pasos en el guion."
            return ModelTurn(text=texto, tool_calls=[], stop_reason="end", usage=dict(self._proveedor.uso))
        paso = cola.pop(0)
        if paso.herramientas:
            llamadas = []
            for indice, llamada in enumerate(paso.herramientas):
                self._proveedor.contador += 1
                identificador = f"guion_{self._proveedor.contador}_{indice}"
                self._nombres[identificador] = llamada.nombre
                llamadas.append(ToolCall(id=identificador, name=llamada.nombre, arguments=dict(llamada.argumentos)))
            return ModelTurn(text="", tool_calls=llamadas, stop_reason="tool_use", usage=dict(self._proveedor.uso))
        texto = self._proveedor.resolver(paso.texto or "")
        if self._clave == "asesor":
            self._proveedor.ultima_respuesta = texto
        if on_text is not None:
            on_text(texto)
        return ModelTurn(text=texto, tool_calls=[], stop_reason="end", usage=dict(self._proveedor.uso))

    def add_tool_results(self, results: list[ToolResult]) -> None:
        for resultado in results:
            nombre = self._nombres.get(resultado.call_id)
            ids = _FRAGMENTO_RE.findall(resultado.content)
            if nombre is not None and ids and not resultado.is_error:
                self._proveedor.fragmentos[nombre] = ids[0]

    def add_user_message(self, text: str) -> None:
        self._proveedor.mensajes.append(text)


class ProveedorReproduccion:
    """Replays ``caso.guion``. Satisfies the :class:`LLMProvider` protocol."""

    name = "reproduccion"
    model = "guion"

    def __init__(
        self,
        caso: CasoEvaluacion,
        cifras: dict[str, str],
        *,
        modelo: str | None = None,
        uso: dict[str, int] | None = None,
    ) -> None:
        # ``modelo``/``uso`` let a test meter a replay as if it were a priced model.
        if modelo is not None:
            self.model = modelo
        self.uso = dict(uso or _USO)
        self._caso = caso
        self._cifras = cifras
        self.colas: dict[str, list[PasoGuion]] = {clave: list(pasos) for clave, pasos in (caso.guion or {}).items()}
        self.fragmentos: dict[str, str] = {}
        self.mensajes: list[str] = []
        self.ultima_respuesta = ""
        self.contador = 0

    def resolver(self, texto: str) -> str:
        def sustituir(encontrado: re.Match[str]) -> str:
            tipo, nombre = encontrado.group(1), encontrado.group(2)
            if tipo == "cifra":
                return self._cifras.get(nombre, encontrado.group(0))
            fragmento = self.fragmentos.get(nombre)
            return f"[{fragmento}]" if fragmento else encontrado.group(0)

        return _MARCADOR_RE.sub(sustituir, texto)

    def _clave(self, tools: Sequence[ToolSpec]) -> str:
        nombres = [t.name for t in tools]
        if "leer_expediente" not in nombres:
            return "asesor"
        claves = sorted(self._caso.modulos, key=len, reverse=True)
        for nombre in nombres:
            for clave in claves:
                if nombre.startswith(f"{clave}_"):
                    return f"especialista:{clave}"
        return "especialista:desconocido"

    def start(
        self, *, system: str, tools: list[ToolSpec], history: list[dict[str, Any]], user_message: str
    ) -> _Conversacion:
        return _Conversacion(self, self._clave(tools))

    def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], schema_name: str, attachments: Any = None
    ) -> dict[str, Any]:
        propiedades = schema.get("properties", {})
        if "afirmaciones" not in propiedades:
            raise ProviderError(f"El modo reproducción no responde a «{schema_name}».")
        afirmaciones = []
        for frase in _FRASE_RE.findall(self.ultima_respuesta):
            ids = _FRAGMENTO_RE.findall(frase)
            if ids:
                afirmaciones.append(
                    {"texto": frase.strip(), "fragmentos": ids, "veredicto": "respaldada", "motivo": ""}
                )
        return {"tipo_respuesta": "respuesta", "declara_falta_de_criterio": False, "afirmaciones": afirmaciones}


__all__ = ["ProveedorReproduccion"]
