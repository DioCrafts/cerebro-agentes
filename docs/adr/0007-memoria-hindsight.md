# ADR 0007: La memoria de conversaciones pasa a Hindsight

- Estado: Aprobado
- Fecha: 2026-11-24
- Decisores: Plataforma del asistente
- Ámbito: `asistente/nucleo/recuerdos` (memoria de conversaciones, «Lo que sé de ti»). No toca la memoria de
  decisiones (`asistente/nucleo/memoria`).
- Documentación: [cerebro/memoria.md](../cerebro/memoria.md)

## 1) Contexto

El ADR 0003 dejó al cerebro del hogar con una memoria de conversaciones escrita a mano sobre SQLite. Guardaba los
mensajes del usuario y del asistente durante 30 días; una tarea programada resumía cada 15 minutos las
conversaciones inactivas con el modelo pequeño, y una puerta determinista solo dejaba pasar un recuerdo si su cita
literal estaba en los mensajes del usuario. La recuperación era un BM25 por usuario con sinónimos del hogar, un
lematizador ligero y, como opción, *embeddings* locales en MLX.

Funcionaba, pero todo era nuestro: el extractor, la fusión de recuerdos nuevos con los viejos (quién sustituye a
quién), la recuperación y sus umbrales, la retención y el modo por lotes. Las cifras de calidad que tenía (recall@5
0,989 en 44 consultas) salían de un conjunto sintético escrito para ese mismo motor. No razonaba con fechas
relativas («el año pasado cambié de banco»), no relacionaba personas y cosas entre conversaciones y solo fusionaba
dos recuerdos cuando compartían la misma clave normalizada.

[Hindsight](https://github.com/vectorize-io/hindsight) (Vectorize, licencia MIT, versión 1.2.3) resuelve esas partes
como biblioteca: extrae hechos con fecha y el fragmento de origen, los consolida en observaciones (un dato nuevo
sustituye al anterior) y recupera con cuatro vías (semántica, BM25, grafo de entidades y temporal) más un
reordenador. Se puede ejecutar embebido, en un proceso local con su propio PostgreSQL, sin Docker.

## 2) Decisión

1. La memoria de conversaciones se sustituye entera por Hindsight. Se borran el código del motor anterior, su tarea
   programada, su *prompt*, sus ajustes y sus tests. La migración `recuerdos_hindsight` v1 elimina de las bases
   existentes sus cinco tablas, sus dos disparadores y sus filas de `schema_migrations`.
2. Un banco de Hindsight por titular (expediente, cuenta), con un nombre derivado por SHA-256 para que Hindsight
   no vea identificadores. Solo se retienen las palabras del usuario, nunca las respuestas del asistente, y siempre
   después de las puertas de privacidad deterministas, que pasan a `asistente/nucleo/privacidad.py` porque las usa
   también el consejo del hogar.
3. Por defecto Hindsight corre como demonio embebido (`hindsight-all`, `HindsightEmbedded`), que la API arranca
   en segundo plano al arrancar ella. `HINDSIGHT_URL` apunta a un servidor ya en marcha cuando el demonio
   local no sirve (Docker).
4. Lo que sigue siendo nuestro: el contrato con el cerebro (`ProveedorMemoria`, tipos `hecho`, `vivencia` y
   `observacion`; la cita es el fragmento de origen y las observaciones no tienen cita), la API del usuario, el
   interruptor de pausa en SQLite, la exportación y el borrado de la cuenta.
5. La memoria de decisiones (`asistente/nucleo/memoria`) se queda como está, con datos tipados en SQLite. El
   optimizador fiscal, el planificador del hogar y la bandeja la leen como restricciones deterministas (un rechazo
   con su ámbito, su palanca y su caducidad); pasarla por un motor que devuelve texto ordenado por relevancia
   rompería esas garantías.

## 3) Alternativas

- A. Mantener el motor propio. Cero dependencias nuevas y todo bajo control, pero seguir escribiendo a mano
  extracción, fusión, razonamiento temporal y grafo, que es justo lo que Hindsight ya trae.
- B. Mem0. Extrae y guarda recuerdos, pero cada actualización la decide otra llamada al modelo y el recuerdo es el
  texto que escribe ese modelo. No guarda con cada hecho el fragmento del usuario del que sale, y la puerta de
  cifras del cerebro necesita esas palabras literales para aceptar una cifra de una conversación anterior.
- C. Zep o Graphiti. Graphiti construye un grafo temporal de conocimiento, que encaja con el problema, pero necesita
  una base de datos de grafos aparte (Neo4j o FalkorDB) y varias llamadas al modelo por mensaje. El servicio
  alojado de Zep sacaría la memoria de la máquina del usuario.
- D. Hindsight como servidor en Docker. Es el mismo motor, pero obliga a instalar y arrancar Docker en el Mac del
  usuario. Se descarta como opción por defecto y queda disponible con `HINDSIGHT_URL`.
- E. Hindsight embebido (elegida).

## 4) Consecuencias

- Dependencia nueva: `hindsight-all==1.2.3`. `uv.lock` pasa de 220 a 331 paquetes; entre los nuevos están
  `pg0-embedded` (el PostgreSQL embebido), `sentence-transformers`, `onnxruntime`, `litellm` y OpenTelemetry.
  `torch` ya estaba. LiteLLM vuelve al lock después de que el [ADR 0006](0006-un-solo-runtime-propio.md) lo sacara
  con OpenHands; ahora solo lo usa Hindsight por dentro.
- Hay un proceso más fuera del ciclo de vida de la app: el demonio sigue vivo al parar la API y se para con
  `hindsight-embed -p cerebro daemon stop`. Sus datos están en `~/.pg0/instances/hindsight-embed-cerebro/`, fuera
  del fichero SQLite, y la orden `copia` no los incluye; hay que copiarlos aparte.
- El primer arranque descarga los modelos locales y tarda uno o dos minutos. Mientras tanto los turnos van sin
  memoria (`vacio_por="no_disponible"`).
- Coste y privacidad: cada mensaje retenido cuesta llamadas de Hindsight al modelo (`claude-haiku-5-5` por defecto),
  con la misma clave pero fuera de nuestra capa de proveedores, así que no aparecen en `llm_usage` ni cuentan para
  el presupuesto por pregunta. Las palabras del usuario, ya filtradas, van al proveedor del modelo para la
  extracción. Con la configuración por defecto es Anthropic, el mismo que ya recibe cada pregunta.
- Olvidar un hecho borra el mensaje entero del que salió. Las observaciones se exportan, pero no se listan en el
  panel ni se pueden corregir u olvidar una a una.
- No hay ninguna mejora medida. Las cifras del motor anterior no sirven para compararlo, porque salían de un conjunto
  sintético escrito para él. La categoría `memoria` de la batería de evaluación (14 casos; los cuatro
  `memoria-real-*` retienen sus datos en bancos reales de Hindsight) es la medida que falta:
  `uv run python -m asistente.plataforma.cli.evaluacion run --categoria memoria`.

## 5) Cuándo revisar esta decisión

Reabrir si ocurre cualquiera de estas cosas:

- La categoría `memoria` falla en algún caso crítico con Hindsight, sobre todo los de otro usuario (aislamiento) y
  los de un dato corregido.
- El gasto de las llamadas de extracción, medido en la consola del proveedor, pesa más que el de las preguntas que
  ahorra.
- Una versión nueva de Hindsight cambia su API REST (`/v1/default/banks/…`) o el formato de sus datos sin una
  migración. El cliente usa pocas llamadas, listadas en `recuerdos/cliente.py`, y la versión está fijada.
- El demonio embebido da problemas en los Mac de los usuarios (arranques fallidos, consumo de memoria). Entonces se
  valora la opción D o volver a un motor propio más pequeño.
