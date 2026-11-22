# Ejército investigador profundo (`asistente/investigador/`)

Investigación profunda donde la fiabilidad manda: cada afirmación del informe lleva una cita literal de una
fuente que se leyó de verdad; lo que no se puede respaldar se elimina (o se marca). Construido sobre el SDK de
OpenHands (`openhands-sdk`, probado con 1.63.1).

## Entrada

```python
from asistente.investigador import investigar, Presupuesto

informe = investigar(
    "¿Qué deducciones por alquiler hay en Madrid?",
    Presupuesto(max_tokens=200_000, max_euros=1.0, max_profundidad=2),
    llm=mi_llm,                 # openhands.sdk.LLM, o {"planificador": …, "lector": …, "critico": …, "redactor": …}
    web=None,                   # por defecto, el buscador web de asistente/capacidades/investigacion
    al_progresar=print,         # recibe Evento(tipo, mensaje, datos)
    sin_fuente="eliminar",      # o "marcar"
)
print(informe.markdown())
```

CLI: `python -m asistente.plataforma.cli.investigar "pregunta" [--max-tokens N] [--max-euros X] [--profundidad N]
[--marcar] [--json]`. El progreso sale por stderr y el informe por stdout. Modelo: `INVESTIGADOR_MODELO`
(nombre LiteLLM, p. ej. `anthropic/claude-sonnet-5-5`) y `INVESTIGADOR_API_KEY`/`ANTHROPIC_API_KEY`.

## Roles y flujo

```
planificador ──► subpreguntas ──► [buscador → lector] × N en paralelo ──► crítico ──┐
      ▲                                                                              │ lagunas y
      └──────────────────────── (si quedan lagunas y profundidad < máx.) ◄───────────┘ profundidad
                                                                                     ▼
                                                                                 redactor ──► verificación determinista ──► Informe
```

| Rol | Qué hace | Archivo |
|---|---|---|
| Planificador | Divide la pregunta en ≤ `max_subpreguntas` subpreguntas con consultas; en rondas siguientes cubre las lagunas del crítico. | `ejercito.py: planificar` |
| Buscador | Usa la herramienta web (`HerramientaWeb.buscar_y_leer`) y registra cada página leída como `Fuente` (F1, F2…), sin duplicados. | `ejercito.py: buscar`, `web.py` |
| Lector | Extrae hallazgos con **una fuente y una cita literal**. Una cita que no aparece en el texto leído, o una cifra que no está en la cita, se descarta al momento. | `ejercito.py: leer` |
| Crítico | Intenta refutar cada hallazgo con la fuente delante: `sostenido`, `dudoso` (se queda, con nota) o `refutado` (sale). Señala lagunas para otra ronda. | `ejercito.py: criticar` |
| Redactor | Escribe afirmaciones que citan ids de hallazgos. Después, `citas.py` comprueba cada una: fuentes leídas, citas literales, cifras presentes en las citas. Las que fallan se eliminan o se marcan `sin_fuente`. Un resumen con cifras sin respaldo se sustituye. | `ejercito.py: redactar/_verificar`, `citas.py` |

Cada rol es una llamada `openhands.sdk.LLM.completion` con mensajes `system`/`user` del SDK y respuesta JSON
(`llm.py`). Se puede usar un `LLM` para todo o uno por rol (p. ej. un modelo barato para lectores y uno fuerte para
crítico y redactor). Buscadores y lectores corren en un `ThreadPoolExecutor` (`Presupuesto.paralelismo`).

**Por qué llamadas `LLM` y no un `Agent` con herramientas por rol**: el flujo es fijo y cada paso tiene que ser
verificable; un bucle agéntico libre añadiría coste y superficie de inyección sin ganar fiabilidad. Cuando el
cerebro (migrado a `openhands.sdk.Agent`) quiera usar el ejército, lo enchufa como una herramienta que llama a
`investigar(...)`; `asistente/nucleo/agent/` no se ha tocado.

## Herramientas reutilizadas

- **Web**: `WebDeCapacidad` adapta el `BuscadorWeb` de `asistente/capacidades/investigacion/web.py`
  (búsqueda y lectura en servidor, listas de dominios permitidos/bloqueados). Cualquier objeto con
  `buscar_y_leer(pregunta, consultas, max_lecturas) -> list[Documento]` sirve (los tests usan uno falso).
- **Detector de inyección**: el compartido de `asistente/conocimiento/llm/security.py`.
- **Navegador** (`asistente/capacidades/navegador`): no se usa todavía. Ejecuta guiones declarativos en webs
  oficiales tras el login del usuario, no lectura abierta; encaja como herramienta adicional (p. ej. leer una sede
  electrónica concreta) implementando `HerramientaWeb` sobre un guion de lectura.

## Presupuesto (`Presupuesto`, `presupuesto.py`)

- `max_tokens`: antes de cada llamada se estima (entrada + salida máxima); si se pasaría, no se llama.
- `max_euros`: se usa el coste que informe el proveedor; si no informa, tokens × `euros_por_millon_tokens`.
- `max_profundidad`: rondas plan → búsqueda → crítica. `max_subpreguntas`, `max_fuentes_por_subpregunta`,
  `max_lecturas`: límites de anchura.
- Al agotarse se para de llamar a modelos y se escribe con lo reunido; si ni el redactor cabe, el informe lista los
  hallazgos ya verificados (sin llamada). `Informe.presupuesto_agotado` y `Informe.avisos` lo dicen; `Informe.coste`
  da tokens, €, llamadas y lecturas.

## Progreso observable

`al_progresar(Evento)` recibe, en vivo: `plan`, `busqueda`, `fuente` (con `sospechosa`), `hallazgo`, `critica`,
`afirmacion_eliminada`, `presupuesto`, `aviso`, `fin`. El `Informe` final conserva plan, fuentes, hallazgos,
críticas y afirmaciones eliminadas para auditar.

## Defensa ante prompt injection (`seguridad.py`)

1. El texto web pasa por el detector compartido: se quitan invisibles, se neutraliza el cebo y la fuente queda
   marcada `sospechosa` (con aviso en el informe).
2. Llega al modelo solo dentro de `<contenido_web fuente=… url=…>`; las etiquetas de cierre dentro del contenido se
   desactivan y el prompt de sistema dice que es material no confiable, nunca órdenes.
3. Ningún modelo tiene herramientas con efectos: lo peor que puede lograr una página es meter hallazgos falsos, y
   esos no sobreviven a la comprobación literal de citas ni al crítico.
4. El texto original no se reescribe: las citas se comprueban contra lo que realmente se leyó.

## Tests

`tests/investigador/test_investigador.py`, con `openhands.sdk.testing.TestLLM` (un LLM con guion por rol) y una
web simulada: citas válidas contra la fuente leída, eliminación (y marcado) de afirmaciones sin fuente o con cifras
inventadas, crítico que refuta, inyección neutralizada antes del modelo, presupuesto de tokens/€/lecturas y
profundidad respetado, y búsquedas paralelas sin mezclar fuentes.

## Integración pendiente

- La dependencia `openhands-sdk>=1.63.1` se añadió a mano en `pyproject.toml` (no había `uv` en esta máquina;
  `uv.lock` sin regenerar). La sesión que migra `asistente/nucleo/agent/` añade la misma: dejar una sola línea y
  ejecutar `uv lock`.
- Enchufar `investigar` como herramienta del cerebro.
