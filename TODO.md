# TODO

Lo pendiente tras la ronda del 22 de noviembre de 2026: el plan de rendimiento y lo que quedó a medias en las sesiones de trabajo. Regla para todo lo de rendimiento: un cambio cada vez, medido contra la línea base de la batería (`python -m asistente.plataforma.cli.bateria`) y el benchmark de recuperación. Si un caso crítico empeora, el cambio no entra.

## Antes de nada: medir

- [ ] Fijar la línea base de la batería con modelo real. El código está hecho y reanuda por casos (`reports/bateria/en_curso.jsonl`); la ejecución se cortó dos veces. El 24 de noviembre no se pudo lanzar porque en esta máquina no hay `ANTHROPIC_API_KEY` (ni `.env`).
- [x] Línea base del buscador del agente sobre 130 consultas (97 con cita, 33 abstenciones, 12 de ellas fiscales «casi»), con recall denso a 50, reparto de fallos entre primera etapa y reranker, y AUROC de las abstenciones. Cifras en `docs/cerebro/benchmark-recuperacion.md` (24 de noviembre).
- [ ] La variante pageindex con Claude sobre las 130 consultas (`--variantes denso,pageindex`). Necesita `ANTHROPIC_API_KEY`.
- [ ] Descargar las páginas de la AEAT y reindexar `data/indice`: el índice local no tiene ninguna, así que las 20 consultas de estilo `aeat` no se pueden medir y `test_manifiesto_coincide_con_el_indice_local` falla.
- [x] Índice en SQLite y fuera FAISS y LangChain (24 de noviembre): migración verificada, la misma medición que con FAISS, búsqueda híbrida RRF activada por defecto (`BM25_ENABLED=true`) y `FAISS_INDEX_PATH` sustituido por `INDICE_DIR`. Detalle en `docs/cerebro/benchmark-recuperacion.md`.
- [ ] Terminar el índice ampliado (3.310 documentos descargados, solo 200 indexados en `data/faiss-v2`) con 2 o 3 procesos, y medirlo con el benchmark antes de cambiar `INDICE_DIR`. `data/faiss-v2` es FAISS y ya no se puede migrar: hay que construirlo de nuevo con el procesador en un directorio propio (por ejemplo `data/indice-v2`).

## Memoria de conversaciones (Hindsight)

- [ ] Medir la memoria nueva con modelo real: `uv run python -m asistente.plataforma.cli.evaluacion run --categoria memoria --out reports/evaluacion/memoria-hindsight.json`. Los cuatro casos `memoria-real-*` usan bancos reales de Hindsight. No hay cifras de este motor sobre datos personales en español (ADR 0007).
- [ ] Apuntar en `llm_usage` las llamadas de extracción de Hindsight (`HINDSIGHT_LLM_MODELO`). Ahora solo se ven en la consola del proveedor y no cuentan para el presupuesto por pregunta.

## Memoria (sin cambio de resultados)

- [ ] Una sola instancia del modelo de embeddings por proceso. `create_embeddings()` crea un `MLXEmbeddings` nuevo en cada llamada y cada uno carga 1,1 GB con `load()`; se llama desde seis sitios (buscador del asesor, capacidad de investigación, consejo, evaluación, cadena de QA, procesador). Lo mismo para el reranker y el índice FAISS.
- [ ] Tope de procesos del procesador según la RAM libre, no según los núcleos. Con 11 procesos de Docling y 3.310 documentos se llenó la memoria del Mac.
- [ ] Embeddings por tandas a medida que salen los fragmentos, en vez de todos al final.
- [ ] Tope global de llamadas concurrentes al modelo. `despacho.py`, `team.py` y `consejo/deliberacion.py` crean un hilo por encargo (`max_workers=len(jobs)`).

## Coste y latencia

- [ ] Trocear el HTML consolidado del BOE con lxml por su estructura fija (`h4.titulo_*`, `h4.capitulo_*`, `h4.seccion`, `h5.articulo`) en lugar de Docling. El 95 % de los fragmentos son del BOE; la jerarquía saldría exacta y el indexado mucho más rápido.
- [ ] Ampliar la caché de prompts: hoy solo se marca el prompt de sistema de Anthropic. Marcar también los bloques estables del investigador y de los especialistas (instrucciones, aviso de seguridad, fuentes que el lector y el crítico releen) y ponerlos delante del prompt.
- [ ] Activar el enrutado por niveles (`LLM_ROUTING_PROFILE=recomendado`): modelo pequeño o esfuerzo bajo para intención, extracción de hechos, selección de nodos, lector y planificador; modelo grande para síntesis, verificador y crítico. Hoy todo va al mismo modelo con `ANTHROPIC_EFFORT=high`. Es el mayor ahorro y el de más riesgo: solo con la batería en verde.
- [ ] Caché de resultados de recuperación por pregunta normalizada.
- [ ] Investigador: segunda ronda solo si el crítico señala lagunas; caché de páginas leídas por URL con fecha de lectura.

## Tests

- [ ] `pytest-xdist` con 2 a 4 procesos, no con todos los núcleos.
- [ ] Marcar como lentos los tests que cargan modelos o lanzan crawlers y dejarlos fuera de la pasada rápida.
- [ ] Arreglar los cuatro tests que fallan desde antes de esta ronda: `tests/modulos/correo/test_adversarial.py::test_el_agente_obediente_no_puede_actuar_por_un_correo`, `tests/modulos/correo/test_api.py::test_flujo_completo`, `tests/test_boe_ccaa_crawler.py::test_crawler_persists_only_pdfs_returned` y `tests/test_teac_crawler.py::test_crawler_persists_only_existing_criteria`.
- [ ] `tests/plataforma/test_migraciones.py::test_concurrent_processes_apply_each_migration_once` falla una de cada tres veces.

## Producto

- [ ] El corpus fiscal debe actualizarse solo: `scripts/actualizar_corpus.sh` está listo, falta programarlo (el ejemplo de launchd está en `docs/operations/es.hacienda-asesor.actualizar-corpus.plist`, sin instalar).
- [ ] Los módulos de la batería no tienen datos del hogar en esta máquina, así que ningún caso de acción llega a dejar una acción pendiente de confirmar. Añadir un expediente de prueba.
