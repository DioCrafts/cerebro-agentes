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
