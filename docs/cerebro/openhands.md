# El cerebro sobre el SDK de OpenHands (histórico)

> Retirado por el [ADR 0006](../adr/0006-un-solo-runtime-propio.md): el adaptador, `CEREBRO_RUNTIME` y la
> dependencia ya no existen. Este documento queda como registro de la auditoría que llevó a esa decisión.

Este documento recoge la auditoría de `asistente/nucleo/agent/`: qué parte es fontanería, que puede delegarse en el
SDK de OpenHands, y qué parte es lo propio del producto, que no se delega. También describe el adaptador que ya está
en marcha y enumera lo que todavía no se ha migrado, con el motivo de cada caso.

- **SDK:** `openhands-sdk==1.60.2`. Es la versión más reciente que admite el resto de dependencias: a partir de 1.61
  se exige `pillow>=13`, y `streamlit==1.57.0` necesita `pillow<13`. Fijar esta versión obligó a subir `pydantic` de
  2.12.3 a 2.13.5.
- **Flag:** `CEREBRO_RUNTIME=openhands` (el valor por defecto) o `CEREBRO_RUNTIME=propio`. Está en
  `asistente/plataforma/settings.py`.
- **Adaptador:** `asistente/nucleo/agent/openhands_runtime.py`.
- **Tests:** `tests/agent/test_openhands_runtime.py`. Cada escenario se ejecuta con los dos runtimes y se exige que
  den exactamente el mismo resultado.

## 1. Auditoría

### Fontanería (delegable en el SDK)

| Pieza | Dónde está | Qué hace | Estado |
|---|---|---|---|
| Bucle acotado de subagente | `team.run_subagent` | Repite turnos del modelo, ejecuta las herramientas y devuelve el último texto como memo. Lo usan los especialistas del hogar (`especialistas.py`, `cerebro/colaboracion/modulos.py`), los investigadores del modo equipo y el crítico. | **Migrado** a OpenHands (`Agent` + `Conversation`). |
| Despacho de llamadas a herramientas | `Toolbox.execute`, `ModuleToolbox.execute` | Hace corresponder cada `ToolCall` con su función. | El SDK enruta la llamada. La ejecución sigue en el toolbox, que es donde viven las garantías. |
| Paralelismo de herramientas | (en el bucle propio, secuencial) | Ejecuta las llamadas de un mismo turno. | Lo hace el SDK con `tool_concurrency_limit=1`, para conservar el orden y que los ids del ledger no cambien. |
| Bucle del asesor | `TaxAgent.run` | Igual que el de los subagentes, pero intercalando el crítico, el verificador y las revisiones. | **No migrado** (ver §3). |
| Bucle de síntesis del cerebro | `cerebro.py` | Igual, con la puerta de cifras entre borradores. | **No migrado** (ver §3). |
| Paralelismo entre encargos (DAG) | `cerebro/colaboracion/despacho.py`, `team.investigar` | Usa `ThreadPoolExecutor` y respeta el orden de dependencias. | **No migrado** (ver §3). Cada encargo ya es una conversación OpenHands propia. |
| Proveedores, reintentos, lotes, streaming | `providers/` | Clientes de Anthropic y OpenAI, reintentos, API de lotes y medición. | **Se mantienen.** El SDK los usa a través de `LLMPuente` (ver §2). |

### Diferencial propio (no se delega)

- **Enrutado por intención** (`enrutado.py`, `cerebro/intencion.py`): primero reglas, el modelo pequeño solo cuando
  las reglas dudan, y una pregunta aclaratoria cuando la confianza es baja.
- **Puerta de cifras** (`cerebro/colaboracion/puerta.py`, `cifras.py`): cada número tiene que venir de una herramienta
  o de las palabras del usuario, y no se admiten sumas entre especialistas.
- **Verificador y ledger de evidencias** (`verifier.py`, `evidence.py`, `procedencia.py`): citas `[F#]`, comprobación
  de afirmaciones y abstención.
- **`pedir_dato` y la pizarra** (`cerebro/colaboracion/pizarra.py`): un especialista puede pedir un dato a otro a
  través del cerebro.
- **Garantías de los módulos** (`especialistas.py`, `colaboracion/modulos.py`): una herramienta `actua` se convierte en
  acción pendiente, una `prepara` se presenta como borrador, cada módulo solo puede escribir los hechos que declaró y
  cada resultado queda como fragmento citable.
- **Memoria** (`asistente/nucleo/memoria`), **consejo del hogar** (`asistente/nucleo/consejo`), tareas, propuestas y
  presupuesto por pregunta (`plataforma/ops/consumo.medir`).

## 2. Cómo funciona el adaptador

```
team.run_subagent ──CEREBRO_RUNTIME──┬─ propio    → run_subagent_propio (bucle original, sin cambios)
                                     └─ openhands → openhands_runtime.run_subagent
                                                     Agent(llm=LLMPuente, tools=[CerebroToolbox], include_default_tools=[])
                                                     Conversation(max_iteration_per_run, stuck_detection=False)
```

APIs del SDK que se usan (comprobadas contra el código instalado):

- `ToolDefinition` y `ToolExecutor`.
- `register_tool`, junto con `Tool(name=..., params=...)`.
- `Agent(llm, tools, system_prompt, include_default_tools, tool_concurrency_limit)`.
- `Conversation(agent, workspace, max_iteration_per_run, stuck_detection, visualizer)`, con `send_message` y `run`.
- `LLM.completion(messages, tools, ...) -> LLMResponse`.
- `openhands.sdk.testing.TestLLM`.

Documentación oficial: <https://docs.openhands.dev/sdk> y <https://github.com/OpenHands/software-agent-sdk>.

**Herramientas.** Se registra un solo toolset, `CerebroToolbox`. Su `create(ejecucion=<id>)` devuelve un
`HerramientaCerebro` por cada `ToolSpec` del toolbox del subagente. Cada uno conserva el nombre y el JSON Schema del
`ToolSpec` sin tocarlos, y su ejecutor llama a `toolbox.execute` con el `ToolCall` **original** del proveedor, de modo
que `arguments_error` también llega. Si el modelo llama a una herramienta que no existe, la llamada se encamina a una
herramienta oculta que pasa por el toolbox, y es el toolbox el que la rechaza, igual que antes.

**Modelo.** `LLMPuente` es un `LLM` del SDK. En cada `completion`:

1. entrega al `Conversation` del proveedor los resultados de herramienta y los mensajes nuevos;
2. ejecuta un `step(allow_tools=...)`;
3. traduce el `ModelTurn` a un `Message` del SDK.

OpenHands, por tanto, nunca llama a una API de modelos por su cuenta. La medición, el presupuesto por pregunta, los
proveedores, los reintentos y los lotes siguen funcionando igual que antes.

**Semántica conservada, paso a paso:**

- En el último paso se desactivan las herramientas. Cualquier llamada que llegue entonces recibe el error «Sin más
  pasos», ese error se entrega al proveedor y la herramienta no se ejecuta.
- Una negativa del modelo, o una llamada cortada por `max_tokens`, termina el subagente sin memo y sin ejecutar nada.
- Un `ProviderError` produce un memo vacío y el equipo se degrada en lugar de fallar. Cualquier otra excepción se
  propaga.
- El memo es el último turno de texto sin llamadas a herramientas. Si ese texto está vacío, el SDK recibe un «.» para
  poder cerrar la conversación, pero el memo sigue vacío.
- La traza, los eventos `tool_call`/`tool_result` y el uso de tokens tienen la misma forma que en el bucle propio.

## 3. Lo que no se migró, y por qué

1. **Bucle del asesor (`TaxAgent.run`) y bucle de síntesis del cerebro.** Entre un borrador y el siguiente intercalan
   el crítico, el verificador o la puerta de cifras. También amplían el presupuesto en cada revisión, inyectan
   `FORCE_ANSWER_MESSAGE` en el penúltimo paso y emiten `text_delta` mientras se genera el texto. El SDK permite
   continuar una conversación con `send_message` después de `run()`, así que es migrable: se ejecuta hasta el
   borrador, se aplica la puerta fuera del SDK y se reenvía la lista de problemas. Pero exige pasar el streaming
   (`on_token`) y los cambios de presupuesto a través del puente, y es la parte con más tests de comportamiento.
   Es el siguiente paso.
2. **Despacho en DAG de los encargos (`colaboracion/despacho.py`) y paralelismo de los investigadores.**
   `DelegateTool`/`TaskToolSet` (paquete `openhands-tools`) delegan en subagentes cuando el modelo lo decide. Aquí el
   plan es del código: hay dependencias entre encargos, `pedir_dato` pasa por la pizarra y se impone un tope de
   paralelismo. Además, `openhands-tools` no se ha añadido como dependencia. Cada encargo sí es ya una conversación
   OpenHands independiente, con una conversación por hilo.
3. **Proveedores y reintentos.** No se sustituyeron por el `LLM` de litellm del SDK, a propósito. Hacerlo perdería la
   medición por propósito, la API de lotes, los adjuntos PDF y el presupuesto por pregunta. El puente es la frontera.
4. **Persistencia y reanudación de conversaciones del SDK** (`persistence_dir`). No se usa: cada subagente vive lo
   que dura un turno y su rastro de auditoría ya es nuestra traza.
5. **Ids de llamada repetidos dentro de un mismo turno.** El bucle propio los tolera. Con el SDK, dos llamadas con el
   mismo id en un mismo turno se confundirían. Ningún proveedor real los produce.
