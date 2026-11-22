# Benchmark de recuperación del módulo fiscal: FAISS frente a PageIndex y el híbrido

**Fecha:** 2026-11-22 · **Estado:** experimental, detrás de un flag · **Recomendación:** dejar `faiss` por defecto y mantener `hibrido` como candidato, sin promoverlo hasta tener una pasada con `--real` (ver §5).

## 1. Qué es PageIndex y cómo lo hemos integrado

[PageIndex](https://github.com/VectifyAI/PageIndex) (VectifyAI, licencia MIT, `pip install pageindex`, v0.2.10 en la fecha de consulta) es un RAG *sin vectores*. Construye un índice jerárquico del documento, parecido a una tabla de contenidos, cuyos nodos tienen `title`, `node_id`, `summary` y `nodes`. Después un LLM **razona** sobre ese índice y baja nivel a nivel hasta la sección que responde.

**No hemos añadido la librería como dependencia.** Su API pública es `PageIndexClient` (`submit_document`, `chat`…), un agente completo que:

- mantiene su propio almacén (`./.pageindex`);
- reconstruye el árbol a partir del PDF;
- **escribe la respuesta él mismo**.

Eso duplica lo que ya hace Docling y se salta nuestra cadena con grounding gate y citas obligatorias. La parte que sí sería reutilizable, `md_to_tree`, solo genera un árbol de headings, y ese árbol ya lo tenemos en `dl_meta["headings"]` de cada chunk. Por eso hemos implementado la idea en [`asistente/conocimiento/llm/arbol.py`](../../asistente/conocimiento/llm/arbol.py), con el mismo esquema de nodos y búsqueda por razonamiento, sobre los chunks que ya indexamos:

- `construir_arboles`: un árbol por documento fuente, a partir de la ruta de headings de Docling o la estrategia BOE (`dl_meta.headings`, `headings` o `section`). El resumen de cada nodo interno es la tabla de contenidos de lo que cuelga de él (equivale al `prefix_summary` de PageIndex), y el de cada hoja es el comienzo de su texto. No se hace ninguna llamada al LLM al indexar.
- `SelectorLLM`: el modelo elige `node_id` a partir de títulos y resúmenes y devuelve JSON. El prompt marca el contenido como dato no confiable y se descartan los ids que el modelo invente.
- `SelectorLexico`: un "LLM simulado" determinista que se usa en tests y en el benchmark. Contabiliza los tokens del prompt que habría enviado.

### El flag

```bash
FISCAL_RECUPERADOR=faiss       # defecto: pipeline actual sin cambios (denso + BM25 + filtro + reranker)
FISCAL_RECUPERADOR=pageindex   # solo navegación en árbol, sin vectores
FISCAL_RECUPERADOR=hibrido     # FAISS/BM25 elige los documentos y el árbol encuentra la sección
```

El flag se aplica en `chain._create_retriever`, sobre los mismos chunks del docstore de FAISS. Con `faiss` el código no pasa por la rama nueva. En el modo híbrido:

- la pertinencia y la abstención las decide el recuperador denso;
- las secciones que encuentra el árbol van primero;
- los chunks densos rellenan hasta `k`, por si la navegación se equivoca de rama.

## 2. Benchmark reproducible

```bash
python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal              # simulado, sin red
python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal --real       # embeddings + LLM reales
python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal --salida r.json --umbral 0.3 --k 5
```

**Datos** (versionados en `eval_data/recuperacion/`):

- `corpus_fiscal.jsonl`: 38 chunks de 28 artículos de LIRPF, LIVA, LIS, LGT y LISD, con la jerarquía Título > Capítulo > Artículo tal como la deja Docling. Los textos son paráfrasis breves de los artículos, no el BOE literal. Los artículos largos están partidos en dos chunks. Hay solapes léxicos deliberados ("vivienda", "deducción", "dividendos", "plazo") para que la estructura importe.
- `fiscal.jsonl`: 32 consultas, de las que 26 esperan una cita (norma + nº de artículo) y 6 son abstenciones.
  - 6 consultas derivan de las citas que exigen los evals del módulo (`eval_data/modulos/fiscal.jsonl`: art. 99 y 115 LIVA, art. 12 LIRPF…) y llevan un campo `origen`. Esos evals prueban calculadoras, no recuperación, así que se han convertido en preguntas de normativa. Un test comprueba que cada `origen` existe y que cada cita esperada está en el corpus.

**Métricas** por variante:

- **cita top-1**: el primer chunk es de la norma y el artículo correctos. Es la cita que mostraría el gate.
- **cita en top-k**.
- **abstenciones correctas**: devuelve `[]` cuando la consulta queda fuera del corpus.
- **falsas abstenciones**: devuelve `[]` cuando sí había cita.
- **latencia estimada**: la medida más llamadas LLM × latencia supuesta, por defecto 800 ms. Con `--real` se usa solo la medida.
- **llamadas y tokens LLM por consulta**, y **coste por consulta**, con precio por millón de tokens configurable (por defecto 0,15 entrada y 0,60 salida; es un supuesto, no una tarifa concreta).

**Modo simulado:**

- la parte densa son embeddings deterministas por *hashing* de términos con FAISS real, más BM25 fusionado por RRF (0,6 / 0,4, como `BM25_ENABLED`);
- se abstiene cuando ningún chunk denso supera el umbral de similitud, que es lo que hace hoy el `EmbeddingsFilter`;
- el árbol usa `SelectorLexico`.

Es reproducible bit a bit, salvo la latencia medida.

## 3. Resultados (modo simulado, `k=3`, umbral denso 0,25)

| variante | cita top-1 | cita en top-k | abst. correctas | falsas abst. | lat. est. media (ms) | p95 (ms) | llamadas LLM | tokens | coste/consulta |
|---|---|---|---|---|---|---|---|---|---|
| faiss | **81 %** | **81 %** | **100 %** | 19 % | **1** | **1** | **0** | **0** | **0** |
| pageindex | 46 % | 54 % | 83 % | 19 % | 2600 | 4801 | 3,25 | 1585 | 0,000247 |
| hibrido | 58 % | 69 % | **100 %** | 19 % | 1775 | 4800 | 2,22 | 674 | 0,000108 |

El coste y la latencia de `faiss` no incluyen la reformulación MultiQuery que se hace en producción (1 llamada LLM). Esa llamada es común a las tres variantes en producción, porque el híbrido usa el mismo recuperador base, así que no cambia la comparación.

**Lectura:**

- **Donde el árbol acierta y FAISS no:** consultas cortas que reproducen el título del artículo, como `q-iva-base` ("base imponible del IVA") o `q-is-dividendos`. El denso las deja por debajo del umbral (falsa abstención) y la navegación las encuentra por el título. Es la ventaja que se le supone a PageIndex.
- **Donde el árbol falla:** consultas que hablan con vocabulario del cuerpo del artículo y no de su título. Por ejemplo, `q-130-dificil` y `q-suministros-autonomo` están en el art. 30 LIRPF ("Normas para la determinación del rendimiento neto en estimación directa"), cuyo título no menciona "gastos de difícil justificación" ni "suministros". El selector léxico se va a la rama "Rendimientos del trabajo". Un LLM real sí debería saber que eso es estimación directa, y aquí está el límite del simulador.
- **El híbrido recupera las abstenciones** (100 %, el denso decide la pertinencia) y mejora a `pageindex` puro en cita en top-k. En top-1 pierde frente a FAISS porque pone la rama del árbol por delante del chunk denso, que en este corpus suele estar bien.
- **Coste y latencia:** `pageindex` hace unas 3 llamadas por consulta (documento, título y capítulo) con prompts de ~1,6 k tokens, porque el resumen del nodo raíz es la tabla de contenidos entera. El híbrido se salta la elección de documento y se queda en ~2 llamadas y ~0,7 k tokens. Con 800 ms por llamada son +1,8 s (híbrido) y +2,6 s (pageindex) por consulta, frente a ~0 de FAISS.

## 4. Límites de esta medición (importante)

1. **El simulador no razona.** `SelectorLexico` puntúa por solape de términos con títulos y resúmenes. Lo que mide el modo simulado es la fontanería, el coste estructural (llamadas y tokens) y el efecto de la jerarquía. **No mide la calidad de razonamiento de un LLM**, que es justo la apuesta de PageIndex. Por eso los porcentajes de las variantes de árbol son una cota inferior pesimista.
2. **No hemos podido ejecutar `--real` en esta sesión.** No hay clave de OpenAI ni índice FAISS local (`.faiss`) en el entorno. El CLI está listo: `--real` usa `create_embeddings()` y `SelectorLLM(ChatOpenAI(OPENAI_MODEL))`. También acepta `--corpus` para medir sobre un volcado de chunks reales.
3. **El corpus es pequeño y parafraseado** (28 artículos). La hipótesis de PageIndex (documentos largos y jerárquicos donde los chunks pierden contexto) brilla en la LIRPF completa o en los manuales AEAT de cientos de páginas, no en 38 chunks.

## 5. Recomendación

**Ganadora hoy: `faiss` (se queda por defecto).** En la medición disponible da la mejor exactitud de cita (81 % top-1), abstiene correctamente el 100 % de las veces y no añade ni latencia ni coste. Ninguna variante de árbol la supera con el selector simulado, y no hay datos con LLM real que justifiquen cambiar el defecto.

**Candidata a promover: `hibrido`, no `pageindex` puro.**

- El híbrido conserva la abstención de FAISS (el grounding gate sigue igual de estricto) y cuesta menos de la mitad que `pageindex` en tokens y llamadas.
- Es el único que puede corregir el caso en que FAISS acierta el documento pero no el artículo.
- `pageindex` puro empeora las abstenciones (83 %, porque la navegación encuentra algo vagamente parecido) y es el más lento. Lo descartamos como defecto para un módulo cuya regla es abstenerse sin cita.

**Siguiente paso para decidir de verdad:**

1. Ejecutar `--real` con el modelo de producción sobre el corpus fijo y sobre un volcado de chunks reales de la LIRPF y el Manual de Renta.
2. Promover `hibrido` solo si mejora la **cita top-1** en ≥ 5 puntos sin bajar las abstenciones correctas del 100 %, con +2 s o menos de p95.
3. Si se promueve, dos mejoras baratas que este informe sugiere:
   - **fusionar el ranking** (árbol y denso por RRF en vez de "árbol primero");
   - **resúmenes de nodo generados por LLM al indexar** (el `if_add_node_summary` de PageIndex), para que los títulos poco descriptivos como el del art. 30 LIRPF dejen de ser un punto ciego.
