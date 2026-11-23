# Medición de la UI y la UX

Arnés para medir la interfaz antes y después del rediseño. Corre entero en local, sin backend ni modelos: `vite preview` sirve el build y manda `/api` a una API simulada (`api-simulada.mjs`) con datos de ejemplo.

## Uso

```bash
npm run medir -- --nombre base
```

Construye el frontend, recorre los flujos en móvil (375×812) y escritorio (1440×900) con tema claro y oscuro, pasa Lighthouse pantalla a pantalla y guarda:

- `resultados/<nombre>.json`: todos los datos.
- `resultados/<nombre>.md`: las tablas (Lighthouse por pantalla, bundle por chunk, axe, foco, estilos, paleta, flujos, estados, textos).
- `capturas/<nombre>/`: una captura por pantalla, viewport y tema, más las de los estados de carga, error y vacío.

Después del rediseño:

```bash
npm run medir -- --comparar base
```

Mide con el nombre `despues` (o el que pases con `--nombre`) y escribe `resultados/comparacion-base-despues.md` con la tabla de antes y después. Para comparar dos mediciones ya guardadas sin volver a medir:

```bash
node medicion/medir.mjs --solo-comparar base despues
```

Opciones: `--sin-lighthouse`, `--sin-estados`, `--sin-build`, `--solo-escritorio`. Variables: `MEDIR_PUERTO_WEB` (4179), `MEDIR_PUERTO_API` (8799), `MEDIR_CHROMIUM` (ruta a un Chromium si el de Playwright no está descargado; si falta, se usa el más reciente de la caché de Playwright o Google Chrome).

El script corre con `nice -n 15`, abre un solo navegador headless a la vez y lanza Lighthouse de uno en uno al final. Tarda unos 10 minutos con todo.

## Qué mide

| Área | Cómo |
|---|---|
| Rendimiento | Lighthouse en modo navegación (LCP, CLS, TBT, FCP, Speed Index) para entrar, inicio y cada panel por `/?panel=<id>`, en móvil y escritorio. INP aproximado con la Event Timing API durante los flujos de Playwright (el modo navegación de Lighthouse no da INP). Tamaño del bundle por chunk en bytes, gzip y brotli. |
| Accesibilidad | axe-core (WCAG 2.2 A/AA y buenas prácticas) en cada pantalla, viewport y tema, agrupado por gravedad. Recorrido del foco con Tab: paradas, foco sin indicador visible, saltos atrás, foco fuera de pantalla y foco que escapa de un diálogo modal. Flujo completo solo con teclado (entrar, preguntar, abrir y cerrar «Tu expediente»). Etiquetas: controles sin nombre, solo con icono, o cuyo texto visible no coincide con el nombre accesible. |
| Consistencia visual | `getComputedStyle` sobre todos los elementos visibles: tamaños, pesos y alturas de línea de fuente, espaciados, radios, sombras, alturas de botón y de campo, y colores de texto, fondo y borde. |
| Paleta | Las variables CSS de las hojas de estilo resueltas en cada tema, con cuántas veces se usa cada una, y los colores usados que no son ningún token. |
| UX | Clics, campos y pasos de cada flujo; paneles escondidos tras «Más»; si el foco vuelve al cerrar un panel; estados de carga, error (y si se puede reintentar) y vacío de cada panel y del asesor; textos con jerga, genéricos o demasiado largos; errores de consola. |

## Si el rediseño cambia selectores

Desde el paso a Primer, `medir.mjs` entiende las dos interfaces: el botón de enviar se llama «Enviar» o «Preguntar», la navegación es la barra «Herramientas» o las pestañas `nav[aria-label="Secciones"] a[data-panel=…]`, y un panel abierto es una hoja (`[role=dialog][aria-modal=true]` visible) o una página (`main [data-ranura=<id>]` sin `hidden`). Las páginas se cierran con la pestaña «Conversación», porque no responden a Escape. Las hojas cerradas siguen montadas y ocultas, así que el recorrido del foco solo cuenta el diálogo visible.

Si el puerto de `vite preview` ya está ocupado (otra medición en marcha), el script se detiene: define `MEDIR_PUERTO_WEB` y `MEDIR_PUERTO_API` con otros libres.

En la UI anterior, los flujos localizan los controles por su papel y nombre accesible o por atributos estables: la caja «Escribe tu consulta», el botón «Enviar», `nav[aria-label="Herramientas"] button[data-panel=…]`, el menú `[role=menu]` de «Más herramientas», los paneles como `[role=dialog]`, y en el expediente el botón «Editar «Salario bruto anual»» y el campo `#fact-rendimiento_trabajo_bruto`. Si el rediseño los cambia, ajusta `medir.mjs` sin tocar qué se mide, para que la comparación siga siendo justa.

Si un flujo falla, el contexto queda marcado con `fallo`, se guarda `capturas/<nombre>/fallo--<viewport>--<tema>.png` y el proceso termina con código 1. Las rutas de la API que la simulación no conoce aparecen en `rutasNoSimuladas` del JSON.
