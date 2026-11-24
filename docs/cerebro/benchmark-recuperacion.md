# Benchmark de recuperación del módulo fiscal

**Última decisión (24 de noviembre de 2026):** el índice es SQLite (sqlite-vec y FTS5) y FAISS y LangChain se han retirado; la búsqueda del agente es híbrida por defecto (`BM25_ENABLED=true`, RRF de denso y FTS5). `pageindex` sigue solo en el benchmark.

## Índice SQLite y búsqueda híbrida (24 de noviembre de 2026)

El índice FAISS de `data/faiss` se migró a `data/indice/indice.sqlite` sin recalcular embeddings: 15.219 fragmentos y 15.219 vectores, y en una muestra de 50 fragmentos los 10 vecinos coinciden en un 99,4 % y el primero en todos. Medido con el benchmark, el buscador denso sobre SQLite da exactamente las cifras de FAISS, consulta por consulta. Tras eso se borraron el adaptador FAISS, la migración, `faiss-cpu`, `langchain-community` y `FAISS_TRUSTED_INDEX` (detalle al final de este documento).

Las variantes del benchmark son ahora `denso` y `rrf`, el buscador del agente con `BM25_ENABLED` apagado y encendido, y `pageindex`. Se midieron sobre las mismas 77 consultas con cita medibles que la línea base, con Qwen3-Embedding-0.6B, Qwen3-Reranker-0.6B, 30 candidatos y `k=3`. Los pesos de RRF son los de `settings.py` (0,6 denso y 0,4 léxico). La consulta FTS5 une con OR las palabras de la pregunta. Al principio entraban también «qué» o «el», que casan con casi todo; ahora se quitan las palabras vacías del español (`PALABRAS_VACIAS` en `conocimiento/indice/almacen.py`).

```bash
KMP_DUPLICATE_LIB_OK=TRUE python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal \
  --indice data/indice --real --variantes denso,rrf --salida reports/benchmark/sqlite-denso-rrf.json
```

| 77 consultas con cita | top-1 | en top-3 | `original` (26) top-1 / top-3 | `cuerpo` (51) top-1 / top-3 |
|---|---|---|---|---|
| `denso` | 48 % | 65 % | 65 % / 81 % | 39 % / 57 % |
| `rrf` con palabras vacías | 43 % | 68 % | 58 % / 88 % | 35 % / 57 % |
| `rrf` sin palabras vacías | 44 % | 68 % | 58 % / 88 % | 37 % / 57 % |

Consulta a consulta frente a `denso`, el RRF sin palabras vacías mete en el top-3 dos citas que la búsqueda densa no llevaba allí («régimen Beckham» y retenciones) y no saca ninguna. En primera posición pierde tres (devengo del IRPF, factura en el IVA y pensión compensatoria) y no gana ninguna. Ninguna de las dos diferencias es significativa (prueba binomial exacta, p = 0,5 y p = 0,25). Quitar las palabras vacías recuperó una de las cuatro primeras posiciones que perdía la versión anterior.

**Por qué queda encendido.** El agente no lee solo el primer fragmento: cada búsqueda le devuelve 6 (`AGENT_SEARCH_DEFAULT_K`) y elige qué citar. Lo que cuenta es que la cita correcta esté en ese grupo, y en el top-3 RRF no empeora ninguna consulta. La mejora está donde se esperaba: en las consultas `original`, que nombran artículos y modelos, sube del 81 % al 88 %. La pérdida en primera posición es real pero pequeña. Se vuelve a la búsqueda densa con `BM25_ENABLED=false`, sin reindexar.

Lo que este benchmark no mide: el top-6 que recibe el agente (el benchmark usa `k=3`), las consultas `aeat` (el índice local no tiene esas páginas) y el efecto sobre las respuestas, que es trabajo de la batería con modelo real.

## Línea base con 130 consultas (24 de noviembre de 2026, aún con FAISS)

Esta es la cifra contra la que se compara cualquier cambio del buscador. Se midió el buscador del agente (variante `faiss`, hoy `denso`) con los modelos locales: Qwen3-Embedding-0.6B, Qwen3-Reranker-0.6B y 30 candidatos para el reranker. No hace falta clave de API. El corpus son los 4.732 fragmentos de las seis normas citadas, sacados del índice `data/faiss` reconstruido ese día a partir de 43 textos del BOE. El JSON completo está en `reports/benchmark/faiss-130-real.json`, fuera de git.

```bash
KMP_DUPLICATE_LIB_OK=TRUE python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal \
  --indice data/faiss --real --variantes faiss --salida reports/benchmark/faiss-130-real.json
```

Ese índice no tiene ninguna página de la AEAT. En disco no hay ninguna descargada, y por eso falla `test_manifiesto_coincide_con_el_indice_local`. Las 20 consultas de estilo `aeat` fallan todas por falta de fuente, no por culpa del buscador, así que las cifras de abajo las dejan fuera. Para medirlas hay que descargar las páginas y reindexar.

| consultas con cita medibles (77) | top-1 | en top-3 | IC 95 % del top-3 |
|---|---|---|---|
| todas | 48 % | 65 % | 54 %–75 % |
| `original` (26) | 65 % | 81 % | |
| `cuerpo` (51) | 39 % | 57 % | |

En el ranking denso, antes del reranker, la cita correcta aparece entre las 3 primeras posiciones en el 62 % de estas 77 consultas, entre las 10 primeras en el 79 %, entre las 30 en el 90 % y entre las 50 en el 92 %. De los 27 fallos, 8 son de la primera etapa: la cita no llega a los 30 candidatos. Los otros 19 son de la reordenación: la cita llega y el reranker no la sube a las tres primeras posiciones. El reranker coloca en el top-3 a 11 consultas que el ranking denso dejaba fuera y saca a 9 que ya estaban, una ganancia neta de 2 sobre 77 por unos 3 segundos por consulta. Con este tamaño de muestra eso no se distingue de cero. La mejora que queda está en reordenar: casi todas las citas que faltan ya están entre los 30 candidatos.

Abstenciones: 33 consultas cuya respuesta no está en el corpus. De ellas, 12 son de estilo `casi`, añadidas este día: preguntas fiscales cuya respuesta está en una norma que el corpus del benchmark no tiene, como una ley autonómica, una norma foral, el IGIC o los impuestos especiales. Algunas sí tendrían respuesta en el índice completo de producción (la ley de tributos cedidos de Madrid o el texto refundido de haciendas locales, por ejemplo), así que la etiqueta solo vale para el corpus de seis normas. El buscador del agente no se abstiene nunca. Lo que se mide es si su puntuación permitiría hacerlo con un umbral (AUROC: 1 separa del todo, 0,5 no distingue):

| abstenciones | n | AUROC similitud densa | AUROC reranker |
|---|---|---|---|
| `fuera_de_corpus` (ajenas a la fiscalidad) | 15 | 0,86 | 0,89 |
| `original` | 6 | 0,80 | 0,94 |
| `casi` (fiscales, otra norma) | 12 | 0,71 | 0,65 |

El reranker da más de 0,9 a 9 de las 12 consultas `casi`. Ante «¿qué bonificación tiene en Madrid el impuesto de sucesiones cuando heredan los hijos?» devuelve los artículos 2 y 21 de la LISD con 1,0. Ningún umbral sobre estas puntuaciones separa una pregunta fiscal sin respuesta de una con respuesta: esa decisión tiene que tomarla un modelo que lea los fragmentos.

Pendiente con clave de API: la variante `pageindex` sobre las 130 consultas (`--variantes denso,pageindex`). Su selector usa ahora esfuerzo bajo y 8.000 tokens de salida, porque Opus 5.5 siempre piensa y los 1.024 de antes podían agotarse antes de la respuesta.

## Modelo de embeddings: Qwen3-Embedding-0.6B frente a F2LLM-v2-0.6B (24 de noviembre de 2026)

**Decisión:** se queda Qwen3-Embedding-0.6B. Con la búsqueda híbrida de producción (`rrf`), F2LLM-v2-0.6B mete la cita en el top-3 en una consulta más y acierta la primera posición en dos menos; con la densa, tres más en el top-3 y cinco menos en primera posición. Ninguna de esas diferencias es significativa con 77 consultas, y cambiar de modelo obliga a reindexar los 15.219 fragmentos del índice de producción.

[F2LLM-v2-0.6B](https://huggingface.co/codefuse-ai/F2LLM-v2-0.6B) es de CodeFuse (Ant Group), tiene licencia Apache-2.0 y está entrenado sobre Qwen3-0.6B-Base. Comparte con Qwen3-Embedding-0.6B la arquitectura, el tokenizador, el pooling del último token y las 1024 dimensiones, así que el embedder del proyecto lo carga sin cambios. Se comparó solo con el modelo del mismo tamaño: la familia llega a 14B, y un modelo mayor ganaría por tamaño, no por calidad.

Los dos modelos recalculan los vectores de los mismos 4.732 fragmentos (las seis normas del benchmark, sacadas de `data/indice/indice.sqlite`). Usan el mismo reranker (Qwen3-Reranker-0.6B con 30 candidatos) y `k=3`. El índice no tiene páginas de la AEAT, así que las 20 consultas de ese estilo quedan fuera, como en la línea base, y se cuentan 77 consultas con cita. El JSON completo está en `reports/benchmark/embeddings-qwen3-vs-f2llm.json`, fuera de git.

| variante | modelo | top-1 | en top-3 | recall denso @3 | @10 | @30 |
|---|---|---|---|---|---|---|
| `denso` | Qwen3-Embedding | 48 % | 65 % | 62 % | 79 % | 90 % |
| | F2LLM-v2 | 42 % | 69 % | 64 % | 81 % | 92 % |
| `rrf` (la de producción) | Qwen3-Embedding | 44 % | 68 % | 52 % | 70 % | 90 % |
| | F2LLM-v2 | 42 % | 69 % | 51 % | 75 % | 95 % |

Consulta a consulta, con `rrf`: Qwen3 acierta la primera posición en 3 consultas que F2LLM falla y F2LLM en 1 que Qwen3 falla (p = 0,62 con la prueba binomial exacta sobre esos pares); en el top-3, 4 consultas son solo de F2LLM y 3 solo de Qwen3 (p = 1). Con `denso` la primera posición se reparte 6 a 1 a favor de Qwen3 (p = 0,12) y el top-3, 5 a 2 a favor de F2LLM (p = 0,45). El rango recíproco medio del ranking denso casi no cambia (0,588 frente a 0,575). Con Qwen3 recalculado salen las mismas cifras que con los vectores guardados en el índice (48 % y 65 % en `denso`, 44 % y 68 % en `rrf`), así que el recálculo no introduce diferencias.

Abstenciones: F2LLM separa algo mejor las preguntas ajenas a la fiscalidad por similitud densa (AUROC 0,90 frente a 0,86) y peor las de estilo `casi` (0,62 frente a 0,71). Ninguno de los dos sirve para decidir la abstención con un umbral, que es la conclusión de la línea base.

El benchmark midió 749 s para vectorizar el corpus con Qwen3 y 312 s con F2LLM, pero esa cifra no compara los modelos: mientras se medía Qwen3 corría a la vez la batería de tests. Con la misma arquitectura y el mismo tamaño, los dos tardan lo mismo.

Para medir otro modelo de embeddings se convierte a MLX bf16 en su propia carpeta y se pasa con `--modelo-embeddings`, que se puede repetir. El benchmark recalcula los vectores del corpus con cada modelo y termina con una tabla comparativa:

```bash
.venv/bin/python scripts/convert_to_mlx.py --hf-path codefuse-ai/F2LLM-v2-0.6B --mlx-path data/models/f2llm-v2-0.6b-mlx-bf16
KMP_DUPLICATE_LIB_OK=TRUE .venv/bin/python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal \
  --indice data/indice --real --variantes denso,rrf \
  --modelo-embeddings data/models/qwen3-emb-mlx-bf16 \
  --modelo-embeddings data/models/f2llm-v2-0.6b-mlx-bf16 \
  --salida reports/benchmark/embeddings-qwen3-vs-f2llm.json
```

La medición de los dos modelos, con el reranker, tardó 50 minutos en este Mac. Conviene no lanzar a la vez otras cargas pesadas si se quieren comparar tiempos. Si algún día gana otro modelo, basta con cambiar `EMBEDDING_MODEL`: cambia la huella del manifiesto y la siguiente ingesta reconstruye el índice entero.

Para volver a comparar F2LLM hace falta más muestra: las páginas de la AEAT indexadas y más consultas de estilo `cuerpo`, que es donde F2LLM saca ventaja (63 % en top-3 frente a 57 %).

## Resultado con LLM real (32 consultas, 22 de noviembre)

Medido sobre el corpus real del procesador (las cinco normas que citan las consultas, tal como quedaron en `data/faiss` tras quitar el banner del BOE), embeddings Qwen3-Embedding-0.6B en MLX y Claude Sonnet 5.5 como selector del árbol. 32 consultas: 26 esperan norma y artículo, 6 son abstenciones. `k=3`, umbral denso 0,45. El coste usa 3 $ / 15 $ por millón de tokens de entrada / salida, un supuesto configurable con `--precio-entrada` y `--precio-salida`.

| variante | cita top-1 | cita en top-k | abst. correctas | falsas abst. | latencia media | p95 | llamadas LLM | tokens | coste/consulta |
|---|---|---|---|---|---|---|---|---|---|
| faiss | 62 % | 73 % | 33 % | 0 % | 29 ms | 53 ms | 0 | 0 | 0 |
| pageindex | 58 % | 81 % | 100 % | 0 % | 3,8 s | 7,7 s | 2,5 | 3.095 | 0,010 |
| hibrido (retirado) | 54 % | 69 % | 33 % | 0 % | 3,7 s | 7,2 s | 2,1 | 4.113 | 0,013 |

El umbral denso decide cuánto se abstiene FAISS, sin cambiar sus aciertos: con 0,25 acierta el 73 % en top-k y no se abstiene nunca; con 0,45, el 33 % de las abstenciones; con 0,55, el 67 %.

```bash
python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal \
  --indice data/faiss --real --proveedor anthropic \
  --precio-entrada 3 --precio-salida 15 --salida r.json
```

## Por qué esta decisión

FAISS acierta el artículo en primera posición tanto o más que PageIndex (62 % frente a 58 %), responde en 29 ms y no cuesta nada por consulta. PageIndex encuentra el artículo entre los tres primeros en tres consultas más (81 % frente a 73 %) y se abstiene bien en las seis que debía, pero tarda unas 130 veces más y paga cada consulta. Con 32 consultas, tres de diferencia no bastan para cambiar el defecto. PageIndex sigue en el benchmark como variante de comparación; no está conectado al agente.

El híbrido (FAISS elige el documento, el árbol la sección) fue peor que las dos variantes puras en top-1, en top-k y en coste, así que se ha borrado del código.

## Tres fallos que falseaban la medición

1. Docling tomaba por títulos la cabecera de la página del BOE: «Agencia Estatal Boletín Oficial del Estado», el aviso de que el texto consolidado no tiene valor jurídico (unos 500 caracteres) y la fecha de actualización. Los ponía delante de los 14.034 fragmentos de las leyes. Ahora `sin_banner_boe` los quita en el procesador. En modo simulado, FAISS pasó del 19 % al 35 % de citas en top-k.
2. PageIndex se paraba en la raíz: `navegar` recogía los fragmentos de cada nodo visitado, y la raíz de cada ley guarda unos 15 fragmentos sin título (preámbulo e índice). Con ellos llenaba el cupo de 3 y no bajaba nunca a un artículo, de modo que daba 0 %. Ahora prioriza las hojas y usa los nodos intermedios solo para rellenar.
3. El benchmark exigía OpenAI y un corpus de juguete. Se añadieron `--proveedor anthropic` y `--indice`, que carga los fragmentos reales de un índice FAISS y les asigna norma por el identificador del BOE.

## Cómo está integrado PageIndex

[PageIndex](https://github.com/VectifyAI/PageIndex) es un RAG sin vectores: un LLM recorre un índice en árbol del documento, parecido a una tabla de contenidos, hasta la sección que responde. No usamos la librería, porque su `PageIndexClient` mantiene su propio almacén, rehace el árbol desde el PDF y redacta la respuesta él mismo, saltándose el grounding gate. La idea está implementada en [`asistente/conocimiento/llm/arbol.py`](../../asistente/conocimiento/llm/arbol.py) sobre los fragmentos que ya indexamos: un árbol por documento con la ruta de títulos de Docling, y un selector (`SelectorLLM`) que elige `node_id` en cada nivel. En los tests y en el modo simulado lo sustituye `SelectorLexico`, que no razona; por eso los porcentajes simulados de PageIndex no sirven para decidir.

Desde noviembre de 2026, la variante `faiss` del benchmark es el buscador del agente (`FaissCorpusSearcher`: vecinos densos, reranker con `--real` y saneado). Las cifras de arriba se midieron con la cadena LangChain anterior, que filtraba por umbral de similitud y podía abstenerse; el buscador del agente no se abstiene por sí solo, así que sus abstenciones salen a 0 % y hay que repetir la medición antes de comparar.

## Límites

Son 32 consultas sobre cinco leyes. El corpus del benchmark no incluye las páginas de la AEAT, que en producción compiten con las leyes. Las abstenciones son solo seis, así que cada una vale 17 puntos. Para afinar la decisión hacen falta más consultas, sobre todo de las que usan el vocabulario del cuerpo del artículo y no el de su título.

## FAISS y LangChain, retirados (24 de noviembre de 2026)

El índice vive en un solo fichero, `<INDICE_DIR>/indice.sqlite` (por defecto `data/indice`; se cambia con `INDICE_SQLITE_PATH`). Guarda los vectores en una tabla `vec0` de `sqlite-vec` con métrica coseno, el texto en FTS5 con el tokenizador `unicode61 remove_diacritics 2` y los metadatos en JSON. No hay pickle: abrirlo no ejecuta código de nadie. FTS5 separa la puntuación, así que «721.» casa con «721». El código está en [`asistente/conocimiento/indice/`](../../asistente/conocimiento/indice/): `almacen.py` (el almacén), `buscador.py` (el buscador del agente, con su cerrojo de proceso) e `ingesta.py` (escritura de la ingesta y `leer_fragmentos`, que usan el grafo de doctrina y el benchmark).

Se siguieron los pasos que estaban previstos: línea base con FAISS, migración verificada, la misma medición sobre SQLite (idéntica) y borrado en bloque de `faiss_legado.py`, `migracion.py`, `cli/indice.py`, `faiss-cpu`, `langchain-community` (con él se fueron `langchain-core` y `langsmith`), `INDICE_BACKEND`, `FAISS_TRUSTED_INDEX` y `FAISS_INDEX_PATH`, que pasa a llamarse `INDICE_DIR`. Si un despliegue sigue definiendo `FAISS_INDEX_PATH`, la aplicación lo avisa al arrancar en lugar de buscar sin corpus. Un índice FAISS que no se haya migrado ya no se puede convertir con este código: hay que reconstruirlo con `python -m asistente.plataforma.cli.processor --full`. `tests/plataforma/test_dependencias_retiradas.py` falla si algún módulo vuelve a importar `faiss` o LangChain.

La ingesta escribe directamente en `indice.sqlite`: cada lote es una transacción, el manifiesto con sha256 y huella del pipeline se escribe después y las bajas usan los `chunk_ids` del manifiesto. Una reconstrucción completa borra el fichero y empieza de cero. La batería no se pudo pasar porque esta máquina no tiene `ANTHROPIC_API_KEY`.
