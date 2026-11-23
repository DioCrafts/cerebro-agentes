# Benchmark de recuperación del módulo fiscal: FAISS frente a PageIndex

**Fecha:** 22 de noviembre de 2026 · **Decisión:** `faiss` sigue por defecto; `pageindex` queda como opción detrás del flag; la variante híbrida se ha retirado.

## Resultado con LLM real

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

## Cambio de índice: de FAISS a SQLite (`INDICE_BACKEND`)

El índice nuevo vive en un solo fichero, `<FAISS_INDEX_PATH>/indice.sqlite` (se cambia con `INDICE_SQLITE_PATH`). Guarda los vectores en una tabla `vec0` de `sqlite-vec` con métrica coseno, el texto en FTS5 con el tokenizador `unicode61 remove_diacritics 2` y los metadatos en JSON. No hay pickle, así que abrirlo no necesita `FAISS_TRUSTED_INDEX`. Con `BM25_ENABLED=true` la búsqueda fusiona el ranking denso y el de FTS5 con RRF (k=60), y FTS5 separa la puntuación: «721.» casa con «721», cosa que el `split()` de `rank-bm25` no hacía. El código está en [`asistente/conocimiento/indice/`](../../asistente/conocimiento/indice/).

El valor por defecto sigue siendo `faiss`. Para cambiarlo:

1. Medir la línea base con FAISS y guardar el JSON:
   ```bash
   INDICE_BACKEND=faiss python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal \
     --indice data/faiss --real --proveedor anthropic --umbral 0.45 --salida base-faiss.json
   ```
2. Migrar sin recalcular embeddings. Lee el pickle una sola vez, copia los vectores con `reconstruct_n`, comprueba que los recuentos cuadran y compara los 10 vecinos de 50 fragmentos al azar en FAISS y en `sqlite-vec`. Sale con código 1 si el solapamiento medio baja de 0,9:
   ```bash
   FAISS_TRUSTED_INDEX=true python -m asistente.plataforma.cli.indice migrar \
     --desde data/faiss --a data/faiss/indice.sqlite
   ```
3. Medir otra vez con `INDICE_BACKEND=sqlite` (y con `BM25_ENABLED=true`, aparte, para ver si FTS5 se porta mejor que `rank-bm25` en las consultas con referencias como «modelo 721» o «Ley 7/2012»). Pasar también la batería y `check_critical_regressions`.
4. Poner `sqlite` por defecto solo si ningún caso crítico empeora frente a la línea base. Si alguno empeora, se queda en `faiss` y se anota aquí cuál.

Con `INDICE_BACKEND=sqlite` la ingesta (`document_loader`) escribe directamente en `indice.sqlite`: cada lote es una transacción, el manifiesto con sha256 y huella del pipeline se escribe después y las bajas usan los `chunk_ids` del manifiesto, igual que con FAISS. Una reconstrucción completa borra el fichero y empieza de cero.

### Retirar FAISS y LangChain

LangChain ya solo entra por un fichero: [`asistente/conocimiento/indice/faiss_legado.py`](../../asistente/conocimiento/indice/faiss_legado.py). Es el único que importa `langchain_community` y `langchain_core`, y traduce en los dos sentidos entre el `Document` de LangChain y `FragmentoRecuperado` ([`asistente/conocimiento/fragmento.py`](../../asistente/conocimiento/fragmento.py)), el tipo que usa el resto del código. `langchain-core` ya no está en `pyproject.toml`; llega como dependencia de `langchain-community`.

El paso que queda, en este orden:

1. Medir la línea base con FAISS (paso 1 de arriba) y guardar `base-faiss.json`.
2. Migrar con `cli.indice migrar` (paso 2). No recalcula embeddings.
3. Medir con `INDICE_BACKEND=sqlite`, con la misma orden y `--salida sqlite.json`, y pasar la batería y `check_critical_regressions`.
4. Si ningún caso crítico empeora, poner `INDICE_BACKEND` a `sqlite` por defecto en `asistente/plataforma/settings.py`. Si alguno empeora, se queda en `faiss` y se apunta aquí cuál y con qué cifras.
5. Con `sqlite` por defecto, borrar en un solo commit:
   - `asistente/conocimiento/indice/faiss_legado.py`, `asistente/conocimiento/indice/migracion.py`, el subcomando `migrar` de `asistente/plataforma/cli/indice.py` y sus tests (`faiss_diminuto` en `tests/conocimiento/test_indice_sqlite.py`; los de FAISS en `tests/test_document_loader_incremental.py`);
   - `faiss-cpu` y `langchain-community` de `pyproject.toml`, y regenerar `uv.lock` (con ellos se va `langchain-core`);
   - en `nucleo/agent/search.py`, la rama FAISS de `FaissCorpusSearcher._cargar`; en `document_loader.py`, la rama FAISS de `process_documents` y `_atomic_save_index`; `FAISS_TRUSTED_INDEX` y el aviso S-17 de `docs/seguridad.md`.
   - Antes de borrar, `leer_fragmentos` necesita un sustituto sobre `indice.sqlite`, porque lo usan el grafo de doctrina (`plataforma/cli/doctrine_graph.py`) y el benchmark (`cargar_corpus_de_indice`). `IndiceHibrido` todavía no tiene un método que devuelva todos los fragmentos.
6. Comprobar que `git grep -n langchain -- asistente tests` no devuelve ningún import.
