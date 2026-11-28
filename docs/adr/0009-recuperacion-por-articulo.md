# ADR 0009: La legislación se indexa por artículo, con sus versiones, y el agente la lee por identificador

- Estado: Aprobado
- Fecha: 2026-11-26
- Decisores: Plataforma del asistente
- Ámbito: `asistente/conocimiento/boe`, `asistente/conocimiento/indice`, `asistente/conocimiento/processor`, las
  herramientas de búsqueda del agente (`asistente/nucleo/agent/tools.py`) y los CLI `boe_seed` y `boe_watch`
- Medición: [cerebro/benchmark-recuperacion.md](../cerebro/benchmark-recuperacion.md)

## 1) Contexto

El buscador partía cada ley con Docling en fragmentos de 512 tokens, sin mirar dónde empieza y acaba un artículo.
El índice daba por vigente el texto consolidado del día de la descarga, así que una pregunta sobre el IRPF de 2022
se respondía con la redacción de 2026. La búsqueda léxica no reducía las palabras a su raíz: «deducción» no casaba
con «deducciones». Y el agente solo tenía una herramienta, `buscar_fuentes`, que devuelve los fragmentos más
parecidos a una frase: no podía abrir el artículo 68 de la LIRPF aunque supiera que era ese, ni seguir una remisión.

La línea base del 24 de noviembre lo cuantifica. La cita correcta llegaba a los 30 candidatos en el 90 % de las
consultas y a las tres primeras posiciones en el 65 %; el reranker ganaba 2 consultas netas de 77 por unos 3 segundos
cada una.

La API de datos abiertos del BOE (`/datosabiertos/api/legislacion-consolidada/id/<ID>/texto`) sirve cada norma
consolidada por bloques. Un bloque es un artículo, una disposición o un encabezado, y trae todas sus versiones con
la norma que las escribió y la fecha en que entraron en vigor. El artículo 68 de la LIRPF tiene 13.

## 2) Decisión

1. La fuente de una norma consolidada es su texto por bloques (`<ID>.boe.xml`, con un sidecar `<ID>.boe.json` que
   guarda título, sigla y fecha de la última modificación). `boe_seed` y `boe_watch` lo descargan. Lo que el BOE no
   consolida, como una sentencia del Tribunal Constitucional, sigue llegando como página y pasa por Docling.
2. La estrategia `boe-bloques-v1` trocea por artículo. Cada fragmento es un tramo de hasta 800 caracteres de un
   artículo, cortado entre párrafos, que empieza por su norma y su artículo: «LIRPF · Artículo 68. Deducciones».
   Lleva como metadatos la norma, el bloque, el número de artículo, la ruta de títulos y capítulos, los apartados
   que cubre y desde cuándo rige. El identificador del fragmento sale de la norma y del bloque, no de la ruta del
   fichero.
3. Solo se vectoriza la versión vigente de cada artículo. Si un artículo está derogado, se vectoriza su última
   redacción con texto, marcada con la fecha en que dejó de regir. Todas las versiones, enteras, van a la tabla
   `preceptos` del índice, que no tiene vectores.
4. La búsqueda devuelve un fragmento por artículo. Los demás fragmentos del mismo artículo se descartan y su sitio
   lo ocupa el siguiente artículo; el artículo entero se lee con `leer_articulo`.
5. La búsqueda sin fecha devuelve solo lo que rige hoy. Con `vigente_en`, el fragmento de un artículo reformado
   después se sustituye por la redacción de ese día y el de un artículo que aún no existía se descarta.
6. El agente tiene dos herramientas más. `leer_articulo` devuelve un artículo o una disposición por norma y número,
   en la fecha que se pida, como fragmento citable, con sus fechas de vigencia, la última nota de modificación del
   BOE y los artículos a los que remite. `indice_norma` lista las normas disponibles o el índice de una. Se ofrecen
   también a los investigadores del equipo fiscal.
7. El lado léxico del índice guarda raíces. Cada fragmento se indexa en FTS5 reducido con el lematizador Snowball
   del español (`snowballstemmer`) y la consulta pasa por la misma reducción. Es el esquema 2 del índice; uno del
   esquema 1 se actualiza al abrirlo para escribir, sin tocar los vectores.
8. La fusión RRF pesa 0,9 el ranking denso y 0,1 el léxico (antes 0,6 y 0,4) y lee cada ranking hasta la posición
   60 aunque se pidan pocos resultados.

Las cifras de cada punto y la decisión sobre el reranker están en el documento del benchmark.

## 3) Alternativas

- A. Seguir con Docling y añadir metadatos de artículo por expresiones regulares sobre los títulos. Es lo que
  hacía el benchmark para puntuar. No da versiones ni fechas, y la jerarquía depende de que Docling acierte los
  encabezados, cosa que falló con la cabecera del BOE en 14.034 fragmentos.
- B. Trocear el HTML de `act.php` por sus clases (`h5.articulo`). Estaba en el TODO. Da la estructura vigente, pero
  no las versiones anteriores, y depende del marcado de una página pensada para leerse.
- C. Vectorizar todas las versiones de todos los artículos y filtrar por fecha en la consulta. Multiplica el índice
  (657 versiones para los 227 preceptos de la LIRPF) y llena los candidatos de redacciones casi idénticas del mismo
  artículo.
- D. Poner en la cabecera de cada fragmento la ruta completa de títulos y capítulos. Se midió: con ella el
  artículo correcto queda primero en el 47 % de las consultas, y con la cabecera corta en el 56 %. Los fragmentos
  de un mismo capítulo empiezan igual y el embedding los distingue peor.
- E. Recuperación contextual con un modelo que escriba el contexto de cada fragmento
  ([ADR 0002](0002-contextual-embeddings-deferred.md)). La cabecera de la decisión 2 da el contexto que importa
  de forma determinista y sin coste, y la alternativa D indica que más contexto delante del texto no ayuda. No se
  ha medido un contexto generado.
- F. Sustituir la búsqueda por la navegación del árbol con un modelo (`pageindex`). Cuesta una o varias llamadas
  por búsqueda. Aquí la navegación la hace el propio agente con `indice_norma` y `leer_articulo` cuando la
  necesita, y la búsqueda por similitud sigue siendo gratis.

## 4) Consecuencias

- El corpus cambia de formato para las normas consolidadas. Hay que volver a descargar con `boe_seed` y reindexar;
  mientras una norma tenga los dos ficheros en la misma carpeta, el procesador indexa solo los bloques.
- Dependencia nueva: `snowballstemmer==3.1.1`, en Python puro.
- El índice de producción no está en esta máquina, así que las cifras son de las seis normas del benchmark. El
  efecto sobre las respuestas es trabajo de la batería con modelo real y no se ha medido.
- Las remisiones se detectan con una expresión regular sobre la redacción habitual («artículo 23 de esta Ley»,
  «artículo 20 de la Ley 37/1992»). Sirven de pista al agente y no se usan para decidir nada.
- Una versión lleva la fecha de entrada en vigor que publica el BOE. Cuando una reforma tiene efectos desde una
  fecha distinta, el BOE lo dice en la nota de la versión, que `leer_articulo` muestra; el índice no la interpreta.
- Las normas que no son del BOE consolidado (doctrina de la DGT, resoluciones del TEAC, páginas de la AEAT,
  boletines autonómicos) siguen con Docling y sin versiones.
- `siglas.py` tiene las seis normas del benchmark. Una norma sin sigla se nombra por su título; el catálogo de
  `boe_seed` admite un campo `sigla` por entrada.
