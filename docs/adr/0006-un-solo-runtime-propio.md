# ADR 0006: Un solo runtime propio para el cerebro y el investigador; fuera el SDK de OpenHands

- Estado: Aprobado
- Fecha: 2026-11-22
- Decisores: Plataforma del asistente
- Matiza: [ADR 0005](0005-cerebro-personal-y-openhands.md). Sustituye su punto 4 (orquestación con el SDK de
  OpenHands). Los puntos 1, 2, 3 y 5 (cerebro personal, Hacienda como primer módulo, garantías, nombre) siguen en
  vigor.

## 1) Contexto

El ADR 0005 decidió orquestar el cerebro con el SDK de OpenHands. La migración se quedó en la mitad
(`docs/cerebro/openhands.md`): `openhands_runtime.py` ejecutaba sobre el SDK el bucle acotado de especialistas,
investigadores y crítico, detrás de `CEREBRO_RUNTIME=openhands|propio`, con OpenHands por defecto. El bucle del asesor
fiscal, el de síntesis y el reparto de encargos en grafo seguían siendo propios. El investigador profundo
(`asistente/investigador/`) llamaba al modelo con el `LLM` del SDK, que por debajo es LiteLLM.

Había, por tanto, dos runtimes que mantener con la misma semántica, y la pregunta era si terminar la migración (A) o
volver a uno solo, el propio (B).

## 2) Qué aportaba OpenHands en la práctica

Para conservar las garantías, el adaptador tuvo que apagar casi todo lo que el SDK ofrece:

- Modelo: `LLMPuente` envolvía nuestro `LLMProvider`. OpenHands nunca llamaba a un modelo; la medición, el
  presupuesto por pregunta, los reintentos, los lotes y los adjuntos seguían siendo nuestros.
- Paralelismo: `tool_concurrency_limit=1`, para que el orden de las llamadas y los ids del ledger no cambiaran.
  El paralelismo entre encargos lo hace `ThreadPoolExecutor` en `despacho.py` y `team.investigar`.
- Reintentos: los del proveedor propio. El SDK no reintentaba nada.
- Detección de bucles: `stuck_detection=False`. El tope de pasos es el nuestro.
- Persistencia: no se usaba `persistence_dir`; la traza de auditoría es nuestra.
- Herramientas: `include_default_tools=[]`; cada herramienta era un envoltorio que llamaba a `toolbox.execute`.
- Trazas: el adaptador reconstruía nuestra traza y nuestros eventos `tool_call`/`tool_result` con la misma forma.

Lo único que hacía el SDK era repetir el turno y despachar la llamada a la herramienta: lo mismo que las 50 líneas de
`run_subagent` propio.

## 3) Medición con turnos reales

`scripts/comparar_runtimes.py`, 12 turnos del cerebro con todos los módulos activos
(`modulos_activos_para(None, None)`), Claude Sonnet 5.5, el índice FAISS de `data/faiss`, una pasada por runtime en
procesos separados y con bases de consumo separadas. El coste sale de la tabla `llm_usage`. El reranker local no
estaba instalado en la máquina y se usó el orden del recuperador en los dos casos, por eso varios turnos fiscales se
abstienen igual en ambos.

| Turno | Tipo | Modo propio / OH | Citas P / OH | Avisos P / OH | Viol. puerta P / OH | Latencia s P / OH | Coste $ P / OH |
|---|---|---|---|---|---|---|---|
| fiscal_alquiler | fiscal | abstained / abstained | 0 / 0 | 0 / 0 | 2 / 4 | 13,6 / 15,1 | 0,101 / 0,099 |
| fiscal_donativos | fiscal | abstained / abstained | 0 / 0 | 0 / 0 | 0 / 0 | 22,2 / 21,4 | 0,046 / 0,048 |
| fiscal_plazo_renta | fiscal | verified / verified | 1 / 1 | 0 / 0 | 0 / 0 | 24,8 / 27,5 | 0,057 / 0,064 |
| fiscal_maternidad | fiscal | abstained / abstained | 0 / 0 | 0 / 0 | 2 / 1 | 11,6 / 10,7 | 0,026 / 0,024 |
| multi_hipoteca_irpf | multi | unverified / unverified | 0 / 0 | 3 / 3 | 0 / 0 | 56,1 / 48,8 | 0,214 / 0,178 |
| multi_luz_ayudas | multi | verified / verified | 2 / 1 | 1 / 1 | 0 / 0 | 54,5 / 34,9 | 0,172 / 0,137 |
| multi_caducidades_fiscal | multi | verified / verified | 2 / 2 | 0 / 0 | 0 / 0 | 44,1 / 35,3 | 0,114 / 0,108 |
| abst_fuera | abstención | abstained / abstained | 0 / 0 | 0 / 0 | 0 / 0 | 1,6 / 2,0 | 0,003 / 0,003 |
| abst_futuro | abstención | abstained / abstained | 0 / 0 | 0 / 0 | 0 / 0 | 11,4 / 22,8 | 0,022 / 0,041 |
| accion_baja_suscripcion | acción | verified / partially_verified | 2 / 2 | 0 / 1 | 0 / 0 | 29,6 / 32,1 | 0,068 / 0,096 |
| accion_luz_cambio | acción | unverified / unverified | 0 / 0 | 2 / 2 | 0 / 0 | 15,2 / 14,1 | 0,026 / 0,026 |
| suscripciones_resumen | módulo | verified / clarification | 2 / 2 | 0 / 0 | 0 / 0 | 21,3 / 16,1 | 0,040 / 0,028 |
| Total | | | 11 / 10 | 6 / 7 | 4 / 5 | 306,0 / 280,8 | 0,889 / 0,852 |

Tokens (entrada sin caché / salida): propio 38.715 / 40.611; OpenHands 33.570 / 34.938. Errores: 0 en los dos.
Ningún turno de acción dejó una acción pendiente en ninguno de los runtimes (los módulos de suscripciones y luz no
tenían datos del hogar en esta máquina); eso es un hueco de la batería, no de un runtime.

Lectura: las diferencias por turno van en las dos direcciones y son del tamaño de la variación de una misma llamada
al modelo repetida. Los turnos más baratos con OpenHands son los que hicieron menos llamadas a herramientas en esa
pasada (multi_luz_ayudas: 6 frente a 4), algo que decide el modelo y no el runtime, porque el bucle es el mismo
paso a paso (lo comprobaban los tests de paridad). Con una pasada no se puede atribuir un 8 % de latencia a nadie.
Lo que sí se ve es que OpenHands no evitó ningún error, no mejoró ningún modo de respuesta y no cambió ninguna cita.

## 4) Coste de mantenimiento

- Código: `openhands_runtime.py` (432 líneas) y `tests/agent/test_openhands_runtime.py` (234), más el selector
  en `team.py`, el ajuste `CEREBRO_RUNTIME` y `docs/cerebro/openhands.md` (111). Terminar la migración (A) suponía
  pasar por el puente el streaming (`on_token`), los cambios de presupuesto entre revisiones y
  `FORCE_ANSWER_MESSAGE`, y el despacho en grafo necesitaba además `openhands-tools`. Es la parte con más tests de
  comportamiento del proyecto, para acabar con el mismo resultado.
- Dependencias: `openhands-sdk==1.60.2` arrastra 104 paquetes transitivos; 62 de ellos solo los pedía él
  (LiteLLM, que ocupa 108 MB instalado, OpenTelemetry con 11 paquetes, boto3, redis, fakeredis, fastmcp, grpcio,
  tree-sitter…). Al quitarlo, `uv.lock` pasa de 322 a 253 paquetes. Importar `openhands.sdk` tardaba 7 s.
- Versión fijada: 1.60.2 era la última compatible, porque desde 1.61 exige `pillow>=13` y `streamlit==1.57.0`
  pide `pillow<13`. Fijarla obligó a subir `pydantic` a 2.13.5. Cada actualización de streamlit o del SDK volvía a
  abrir este conflicto.
- Acoplamiento: ocho APIs del SDK (`ToolDefinition`, `ToolExecutor`, `register_tool`, `Tool`, `Agent`,
  `Conversation`, `LLM.completion`/`LLMResponse`, `MetricsSnapshot`/`TokenUsage`), varias internas
  (`openhands.sdk.llm.utils.metrics`), y un registro global de herramientas por nombre.
- Investigador: era la única parte del producto que llamaba a un modelo fuera de nuestra capa de proveedores.
  Sus llamadas no pasaban por `medir`, así que no aparecían en `llm_usage` ni contaban para el presupuesto por
  pregunta; el coste lo estimaba LiteLLM con sus propios precios.

## 5) Alternativas

- A. Terminar la migración (asesor, síntesis, grafo). Coste: el más alto de las tres, en las piezas con más
  tests. Beneficio medido: ninguno, porque las garantías obligan a desactivar lo que el SDK hace por su cuenta. Sigue
  el conflicto de `pillow` y las 62 dependencias.
- B. Un solo runtime propio (elegida). Se borra el adaptador y su ajuste, el investigador pasa a nuestros
  proveedores y sale la dependencia. Se pierde la opción de usar en el futuro la delegación y la persistencia del
  SDK sin trabajo previo.
- C. Dejar los dos runtimes. No cuesta nada hoy, pero duplica cada cambio del bucle y mantiene la dependencia
  por un camino que no aporta.

## 6) Decisión

1. `team.run_subagent` es el único bucle acotado de subagente. Desaparecen `openhands_runtime.py` y
   `CEREBRO_RUNTIME`. Los escenarios de los tests de paridad se conservan en `tests/agent/test_subagente.py` como
   contrato de ese bucle.
2. El investigador llama al modelo con un `LLMProvider` medido (`medir(create_provider(...), "investigador")`).
   `INVESTIGADOR_MODELO` sigue aceptando `proveedor/modelo`. Sus llamadas entran en `llm_usage` y en el presupuesto
   por pregunta como las demás.
3. Sale `openhands-sdk` de `pyproject.toml` y de `uv.lock`.
4. Si más adelante hace falta que el modelo decida en qué subagentes delegar o conversaciones persistentes, se evaluará con una medición como la
   de este ADR antes de añadir otro runtime, y entrará detrás de `LLMProvider` y `ToolRunner`, que son las fronteras
   que ya usa el código.

## 7) Consecuencias

- Queda un bucle que cambiar, no dos; 666 líneas menos entre adaptador y tests, y 69 paquetes menos en el lock.
- Se puede volver a subir `streamlit` y `pillow` sin el tope que imponía el SDK.
- El presupuesto del investigador en euros se calcula ahora con la estimación por tokens de `Presupuesto`; el coste
  real queda en `llm_usage` con los precios de `data/precios_llm.json`.
- `docs/cerebro/openhands.md` queda como registro histórico de la auditoría; el diferencial propio que enumera
  (enrutado, puerta de cifras, verificador, pizarra, garantías de módulos) no cambia.
- `scripts/comparar_runtimes.py` se queda para repetir la medición si algún día se evalúa otro runtime.
