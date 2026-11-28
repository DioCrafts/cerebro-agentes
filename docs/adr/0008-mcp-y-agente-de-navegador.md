# ADR 0008: MCP en los dos sentidos y un agente de navegador sobre el árbol de accesibilidad

- Estado: Aprobado
- Fecha: 2026-11-26
- Decisores: Plataforma del asistente
- Ámbito: `asistente/capacidades/conectores` (cliente MCP), `asistente/plataforma/mcp` y
  `asistente/plataforma/cli/mcp.py` (servidor MCP), `asistente/capacidades/navegador` (misiones del agente y
  confinamiento de Chromium)
- Documentación: [capacidades/conectores.md](../capacidades/conectores.md),
  [capacidades/navegador.md](../capacidades/navegador.md)

## 1) Contexto

El asistente tenía dos límites que no venían de sus garantías, sino de cómo estaba hecho.

Las herramientas, el correo y el navegador hablan un contrato propio (`capacidades/contrato.py`). Para conectar
un servicio nuevo había que escribir una capacidad en Python, y ningún otro cliente (Claude Desktop, un IDE)
podía usar las calculadoras del hogar.

El navegador ejecuta guiones JSON con selectores CSS. El modelo solo interviene para proponer un selector cuando
la página cambia. Cada sitio nuevo exige escribir y revisar un guion, así que en la práctica hay dos: Datadis y
una sede de ejemplo.

## 2) Decisión

Son cuatro piezas. Las tres primeras pasan por las puertas que ya existían; la cuarta las refuerza por debajo
del navegador.

### 2.1 Cliente MCP: un servidor externo es una capacidad

Cada servidor MCP que el operador declara en `MCP_SERVIDORES` se registra como una capacidad `mcp_<clave>` y
cada herramienta suya como una operación. Todo pasa por `pedir()`, así que hereda sin código nuevo el
consentimiento por usuario, la validación de esquemas, el registro y el envoltorio de datos no confiables.

Lo que dice el servidor de sí mismo no decide nada:

- Una herramienta es de `lectura` solo si el operador la nombra en `lectura`. El resto es `actua`: queda como
  acción pendiente y se ejecuta cuando el dueño del expediente la confirma. Las anotaciones `readOnlyHint` y
  `destructiveHint` del servidor se ignoran.
- El modelo ve la definición (nombre, descripción y esquema de entrada) que el operador aprobó con
  `python -m asistente.plataforma.cli.mcp aprobar <clave>`, y antes de cada llamada se compara la huella de esa
  definición con la que el servidor sirve. Si no coinciden, lo que se rechaza es la llamada, no la herramienta:
  el modelo la sigue viendo ofrecida y recibe el error. La comparación se hace contra la lista que la sesión
  guardó al conectarse, así que un cambio a media sesión no se nota hasta la siguiente reconexión; el cliente no
  se suscribe a `tools/list_changed`.
- Las descripciones llegan al modelo marcadas como texto de un tercero, recortadas a 400 caracteres y pasadas
  por el saneado de datos recuperados, que quita los caracteres de control y sustituye los corchetes angulares.
  Eso corta los cebos conocidos; no es una garantía de que no quede ninguno. Del esquema de entrada se
  conservan los tipos, `required`, los enumerados y las cotas numéricas, y se descartan `default`, `examples`,
  `title`, `pattern`, `format`, `$ref` y `allOf`; las descripciones de cada campo se recortan a 160 caracteres.
- El cliente no declara `sampling`, `elicitation` ni `roots`: el servidor no puede pedir llamadas a nuestro
  modelo ni preguntar al usuario.
- El resultado siempre es dato de terceros (`datos_de_terceros=True`), con tope de tamaño y de tiempo
  (`MCP_MAX_RESULTADO`, `MCP_TIEMPO_CONEXION_S`, `MCP_TIEMPO_LLAMADA_S`).
- Una `lectura` se ejecuta sin preguntar, y sus argumentos salen hacia el servidor. Mientras los escribe la
  conversación eso no es un problema; en cuanto el turno ha leído datos de terceros, un modelo guiado por ese
  texto podría meter en ellos lo que sabe del expediente. Así que después de una lectura de terceros, otra
  lectura de un conector también queda como acción pendiente: el dueño ve qué se enviaría y a quién. Las
  lecturas que no salen del equipo no cambian (`HerramientaModulo.sale_del_equipo`).

El frente en el chat es el módulo `conectores`, con una herramienta `conectores_<clave>_<operación>` por
operación. Así las acciones usan el circuito que ya existe (`AccionPendiente`, `POST /acciones/{id}/confirmar`).

Se usa el SDK oficial `mcp==2.3.0`, que ya estaba en `uv.lock` como dependencia de Hindsight y ahora se declara.
Transportes: `stdio` y HTTP con streaming. Las conexiones viven en un hilo con su propio bucle de asyncio,
porque el resto del proyecto es síncrono.

### 2.2 Servidor MCP: las herramientas del hogar para otros clientes

`python -m asistente.plataforma.cli.mcp servir --caso <id>` expone por `stdio` las herramientas de los módulos
activos de ese expediente y las operaciones de las capacidades que no tienen módulo propio ni llaman a un
modelo. Al otro lado hay un programa que no controlamos, y suele llevar un modelo, así que se le trata como a
un turno que ha leído datos de terceros:

- `lectura` se ejecuta;
- `prepara` calcula el borrador, pero lo que escribiría en el expediente queda como acción pendiente;
- `actua` crea la acción pendiente y responde que espera confirmación en la aplicación. Nunca actúa.

Las anotaciones MCP salen del `efecto`. Las capacidades `mcp_*` no se reexportan.

No se monta en la API por HTTP. Haría falta decidir cómo se identifica un cliente MCP remoto (un servidor de
recursos OAuth sobre las sesiones actuales) y esa decisión no cabe aquí. Por `stdio` el proceso lo arranca
quien ya tiene acceso a la base de datos.

### 2.3 Agente de navegador: misiones sobre la instantánea ARIA

Una misión es un objetivo en texto para un sitio declarado. En cada paso el modelo recibe la instantánea ARIA de
Playwright (`aria_snapshot(mode="ai")`, con referencias `[ref=e12]`) y elige una acción: `ir`, `pulsar`,
`rellenar`, `seleccionar`, `esperar`, `descargar`, `detener` o `terminar`. No hay selectores CSS.

El modelo propone y el código decide, como en la reparación de selectores:

- La política de red no cambia: solo los orígenes del sitio y, tras el login del usuario, solo los métodos de
  lectura (GET, HEAD y OPTIONS) más los prefijos POST que el guion declare en `peticiones_permitidas`. Un clic
  que se cuele no puede sacar un POST que el sitio no haya declarado; un formulario con `method="get"` sí se
  puede enviar.
- `pulsar` se rechaza si el enlace sale del sitio, o si el control parece enviar, firmar, pagar, aceptar o
  borrar. Para decidirlo se miran tres cosas: el texto visible del control, su atributo `formaction` y si es de
  tipo `submit` o `image` dentro de un formulario cuyo método no sea GET. Es una heurística sobre palabras y
  atributos, así que un botón sin texto que envíe por JavaScript se le escapa. `detener` localiza ese control,
  hace la captura y para; deja una acción pendiente informativa.
- `rellenar` no escribe en campos de credenciales ni de pago, detectados por nombre, id y etiqueta. El valor
  tiene que venir de lo que escribió el usuario (el objetivo o los datos de la misión): se compara sin
  distinguir mayúsculas ni acentos, se admiten cinco formatos de fecha derivados de un dato ISO y vale
  cualquier fragmento del objetivo de tres caracteres o más. `seleccionar` no pasa por esa puerta: solo exige
  que la opción exista y sea única.
- La instantánea llega al modelo como dato no confiable, con los identificadores personales enmascarados por
  patrones (los mismos que usa el esqueleto de los guiones, en `navegador/personales.py`). Un nombre propio en
  mitad de una tabla no lo cazan, y las fechas y los importes se conservan a propósito, porque una misión suele
  tener que leerlos.
- Topes por misión: 25 pasos (`NAVEGADOR_AGENTE_MAX_PASOS`), una llamada al modelo por paso, 12.000 caracteres
  de instantánea (`NAVEGADOR_AGENTE_MAX_INSTANTANEA`), 6 rechazos y 3 acciones repetidas. En el peor caso son
  del orden de 300.000 caracteres de entrada por misión. El gasto se mide con el propósito `navegador:agente`.

Un sitio (`sitios/*.json`) declara orígenes, login, máscaras y descarga, sin pasos. Los sitios de los guiones
existentes sirven tal cual. Los guiones siguen siendo la vía determinista y la recomendada cuando existen.

`NAVEGADOR_AGENTE` está apagado por defecto: con él, el texto de las páginas sale hacia el proveedor del modelo,
y eso lo decide el operador.

### 2.4 Confinamiento de Chromium

El contexto ya era efímero (sin perfil, sin cookies guardadas). Se añade:

- carpeta de descargas propia de cada ejecución, con el perfil y el lanzador, borrada al terminar. La carpeta de
  datos del navegador sigue siendo la temporal que Playwright crea en cada `launch()`, como antes;
- `--host-resolver-rules`: Chromium no resuelve ningún nombre fuera de los orígenes permitidos, tampoco para lo
  que no pasa por la interceptación de rutas. Una conexión a una IP escrita en la página no la para esta regla,
  sino la interceptación;
- en macOS, el proceso corre bajo `sandbox-exec` con un perfil que le niega leer y escribir en el directorio del
  usuario, salvo los ficheros del propio navegador: ahí están los perfiles reales de los navegadores, el
  llavero, `~/.ssh` y los documentos. Una página apuntada a `file:///Users/…` recibe «acceso denegado».

El perfil es `(allow default)` más esas denegaciones, no una lista blanca. Así que fuera del directorio del
usuario Chromium sigue pudiendo escribir (`/tmp`, otro volumen) y leer (`/etc`, `/Library/Keychains`), y la
comunicación con otros procesos del mismo usuario queda abierta. En Linux no hay equivalente incluido: el
contenedor del despliegue y el sandbox propio de Chromium son la frontera.

`NAVEGADOR_CONFINAMIENTO` tiene tres valores y solo uno falla cerrado:

- `auto`, el valor por defecto, envuelve el proceso donde puede y lo lanza sin envolver donde no: cualquier
  plataforma que no sea macOS, y un macOS sin `sandbox-exec`. La tarea no se detiene; se registra como
  `confinado="ninguno"` y la persona lo ve en el registro de la tarea.
- `sandbox-exec` se niega a ejecutar la tarea si no puede envolver el proceso. Es el valor que hay que poner
  cuando un navegador sin confinar no es aceptable. Apple ha marcado `sandbox-exec` como obsoleto pero lo sigue
  incluyendo; el día que desaparezca, con este valor las tareas se paran en vez de seguir sin confinamiento.
- `ninguno` no lo envuelve nunca y no avisa, porque lo ha elegido el operador.

## 3) Alternativas

- Usar el conector MCP del lado del servidor de Anthropic (`mcp_servers` en la petición). El modelo llamaría a las
  herramientas sin pasar por `pedir()`: sin consentimiento, sin confirmación y solo con Claude. Descartado.
- Fiarse de las anotaciones del servidor para distinguir lectura de acción. La especificación las define
  como pistas no fiables. Descartado.
- Manejar el navegador con la herramienta de uso del ordenador de Claude. Trabaja con capturas y coordenadas: no se
  puede comprobar en código qué control hay bajo un clic antes de darlo, las capturas llevan a un tercero todos
  los datos visibles sin enmascarar, cuesta más por paso y solo funciona con un proveedor. Queda para lo que no
  tiene DOM (aplicaciones de escritorio) y pediría una máquina virtual, que es otra decisión.
- Adoptar un agente de navegador de terceros (browser-use, Stagehand). Traen su propio bucle y su propia capa de
  modelos; habría que apagarles casi todo para conservar las puertas, como pasó con OpenHands
  ([ADR 0006](0006-un-solo-runtime-propio.md)).
- Sustituir los guiones por el agente. Un guion revisado es reproducible y no gasta modelo. Se mantienen.

## 4) Consecuencias

- Conectar un servicio pasa de escribir una capacidad a declarar un servidor y aprobar sus herramientas. Después
  hay que reiniciar la API, porque el registro de capacidades se construye al importar, y activar el módulo
  `conectores` y la capacidad `mcp_<clave>` para quien vaya a usarlos.
- Dependencia declarada: `mcp==2.3.0`. No entra ningún paquete nuevo en `uv.lock`, porque ya estaba como
  dependencia transitiva de Hindsight. Eso ata la versión a lo que pida Hindsight, y el cliente HTTP importa un
  módulo privado del SDK (`mcp.shared._httpx_utils`): una subida de cualquiera de los dos puede romperlo.
- El módulo `conectores` añade al chat tantas herramientas como operaciones aprobadas; conviene aprobar pocas,
  porque cada definición ocupa contexto en todos los turnos y empeora el enrutado.
- Un servidor MCP por `stdio` es un proceso con los permisos del servicio. Aprobarlo equivale a instalar un
  programa: el confinamiento de ese proceso queda fuera de este ADR, aunque el lanzador de 2.4 hace justo eso
  para Chromium.
- La identidad frente a un servidor externo es una por despliegue: las credenciales salen del fichero del
  operador y hay una sesión por servidor para todo el proceso. En una instalación con cuentas, todas las
  personas que activen un conector comparten la misma cuenta del servicio. Cada sesión atiende de una en una,
  así que una llamada que agote `MCP_TIEMPO_LLAMADA_S` hace esperar a los demás.
- Las misiones envían texto de páginas al proveedor del modelo. Eso lo dice un consentimiento aparte
  (`navegador-mision-2026-11`), que se exige al iniciar una misión; los guiones siguen con el texto anterior,
  que no cambia.
- El perfil de `sandbox-exec` se ha probado con el Chromium de Playwright 1.65 en macOS 26 contra webs
  simuladas. Los tests corren sin ventana; con ventana se comprobó a mano. Otra versión, o un inicio de sesión
  con certificado del llavero, pueden necesitar permisos nuevos.

## 5) Qué falta por comprobar

Las misiones no se han probado contra ningún sitio real: en esta máquina los tests no salen a la red y usan un
modelo de pega. El único sitio incluido, `sitios/boe.json`, está marcado `verificado_contra_sitio_real: false`.
`NAVEGADOR_AGENTE` se queda apagado hasta que una batería con modelo real, contra un sitio real y con casos
adversarios, diga qué proporción de misiones termina bien y a qué coste.
