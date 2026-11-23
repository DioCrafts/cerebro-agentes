# Medición de la UI con Primer frente a la base

Medición del 23 de noviembre de 2026 sobre la rama `claude/portar-ux-014bae`: la UI de `main` rediseñada con Primer, más las mejoras de UX portadas desde `claude/medir-ui-9b7c28` y la accesibilidad de `claude/jovial-meninsky-caf31e`. Se compara con la medición base (`docs/ui/medicion-base.md`), hecha sobre la UI anterior a Primer.

La columna «Rendimiento» es la medición del mismo día sobre la rama `claude/rendimiento-ui-c0bce8`, que recupera parte del rendimiento en móvil sin tocar la accesibilidad ni la consistencia (sección «Rendimiento en móvil» más abajo). Sus datos están en `frontend/medicion/resultados/rendimiento.json` y `comparacion-base-rendimiento.md`.

Se repite con `npm run medir -- --comparar base` desde `frontend/`. Los datos están en `frontend/medicion/resultados/primer.json`, las tablas por pantalla en `primer.md`, la comparación completa en `comparacion-base-primer.md` y las capturas en `frontend/medicion/capturas/primer/`.

## Condiciones

Las mismas que la base: build de producción con `vite preview`, la API simulada de `frontend/medicion/api-simulada.mjs`, Chromium 153 headless de la caché de Playwright 1.65, Lighthouse 13.6 de uno en uno, móvil (375×812) y escritorio (1440×900) en tema claro y oscuro, todo con `nice -n 15`. No se arrancó el backend ni ningún modelo.

La máquina estaba compartida con sesiones de QA. La base ya mostró cuánto ruido hay en las cifras de tiempo: el peor LCP de móvil varió de 2.733 a 3.451 ms entre dos mediciones seguidas y el INP aproximado hasta un 69 %. Las diferencias de tiempo menores que eso no significan nada. Las de accesibilidad, consistencia, bundle y UX salieron idénticas entre repeticiones.

El arnés se adaptó a la UI con Primer sin cambiar lo que mide: el botón de enviar se llama ahora «Preguntar», la navegación son las pestañas `nav[aria-label="Secciones"]` y un panel abierto puede ser una hoja lateral (diálogo modal) o una página en la columna principal. Las páginas no se cierran con Escape, como en GitHub; el recorrido con teclado vuelve a «Conversación» con Mayús+Tab e Intro, y eso cuenta como cerrar con teclado.

## Antes y después

| Área | Cifra | Base | Primer | Rendimiento |
|---|---|---:|---:|---:|
| Rendimiento | Lighthouse rendimiento, mediana móvil / escritorio | 96 / 100 | 86 / 99 | 91 / 100 |
| Rendimiento | LCP mediana móvil / escritorio | 2.427 / 608 ms | 3.643 / 824 ms | 3.464 / 806 ms |
| Rendimiento | LCP peor pantalla, móvil | 2.733 ms | 3.957 ms | 3.639 ms |
| Rendimiento | CLS peor pantalla, móvil / escritorio | 0,283 / 0,061 | 0,236 / 0,045 | 0,018 / 0,003 |
| Rendimiento | INP aproximado, peor interacción móvil / escritorio | 88 / 128 ms | 48 / 48 ms | 64 / 56 ms |
| Bundle | Carga inicial JS+CSS (gzip) | 135 KiB | 339,9 KiB | 217,1 KiB |
| Bundle | JS total / CSS (gzip) | 234,8 / 8,3 KiB | 394,5 / 71 KiB | 392,3 / 52,3 KiB |
| Accesibilidad | Nodos con violaciones axe: graves / moderadas / menores | 120 / 84 / 72 | 0 / 4 / 0 | 0 / 4 / 0 |
| Accesibilidad | Reglas axe incumplidas | 4 | 1 | 1 |
| Accesibilidad | Paradas de foco sin indicador visible | 2 | 0 | 0 |
| Accesibilidad | Saltos atrás en el orden del foco | 49 | 15 | 21 |
| Accesibilidad | Paradas de foco fuera de pantalla | 14 | 49 | 52 |
| Accesibilidad | Foco que se escapa de un diálogo modal | 0 | 0 | 0 |
| Accesibilidad | Tab hasta la caja de consulta, móvil / escritorio | 22 / 22 | 22 / 30 | 22 / 30 |
| Accesibilidad | Controles de menos de 24 px | 159 | 66 | 66 |
| Accesibilidad | Controles que solo muestran un icono | 40 | 1 | 1 |
| Consistencia | Tamaños de fuente distintos | 17 | 8 | 8 |
| Consistencia | Alturas de línea distintas | 33 | 18 | 18 |
| Consistencia | Alturas de botón / de campo distintas | 25 / 13 | 12 / 5 | 12 / 5 |
| Consistencia | Valores de espaciado distintos | 28 | 40 | 40 |
| Consistencia | Sombras distintas | 5 | 16 | 16 |
| Consistencia | Colores de borde distintos (cada tema) | 6 | 12 | 12 |
| UX | Entrar: clics + campos | 3 | 3 | 3 |
| UX | Preguntar: clics + campos | 3 | 3 | 3 |
| UX | Guardar un dato en el expediente: clics + campos | 4 | 4 | 4 |
| UX | Ver avisos y marcar uno como leído: clics | 2 | 2 | 2 |
| UX | Clics medios para abrir un panel, móvil / escritorio | 1,8 / 1,67 | 1,8 / 1,33 | 1,8 / 1,33 |
| UX | Paneles escondidos tras «Más», móvil / escritorio | 12 / 10 | 12 / 5 | 12 / 5 |
| UX | Pantallas sin indicador de carga | 6 | 0 | 0 |
| UX | Errores sin opción de reintentar | 12 | 0 | 0 |
| UX | Pantallas vacías sin explicación | 4 | 3 | 3 |
| UX | Textos poco claros detectados | 61 | 9 | 9 |

En la base, «Ver avisos y marcar uno» contaba 2 clics porque «Marcar como leído» estaba a la vista. En la rama de correcciones de la UI anterior pasó dentro de un `<details>` «Más acciones» y el arnés ya no lo encontraba; en esta medición el aviso queda marcado en los cuatro contextos y el foco se queda en el aviso («Quedan 12 días para el pago fraccionado del 4T») en vez de perderse.

## Rendimiento en móvil

La medición de Primer dejó la carga inicial en 340 KiB con gzip y el LCP mediano de móvil en 3,6 s. Los paneles ya se cargaban bajo demanda; lo que pesaba estaba en el núcleo. El análisis del bundle (rollup-plugin-visualizer sobre el build) dio cuatro causas:

- 48 KiB del tema heredado de Primer en JavaScript (`legacy-theme/ts/color-schemes.js`), que solo usaba el `ThemeProvider` antiguo. `ui/ProveedorUI.tsx` usa ahora el de `@primer/react/next`, que no lleva valores de tema en JS.
- 38 de los 41,5 KiB del CSS principal eran las hojas de tema de `@primer/primitives`, con unas 1.900 variables cada una y una segunda copia para el modo `auto`, que la app no usa nunca. `frontend/plugins-vite/podarVariables.ts` es un plugin de PostCSS que quita de los bloques de tema las variables que no nombran ni `src/` ni `@primer/react`, y las copias de `auto`. El CSS principal bajó a 20 KiB.
- `Hilo` (los turnos de la conversación con fuentes, comprobaciones y consejo), `AuthScreen`, `PrivacyPanel`, la paleta de comandos y el `Dialog` del menú del móvil se cargaban al arrancar aunque la primera pantalla no los pinte. Ahora van con `React.lazy`. `Hilo` se pide con la primera tecla en la caja de la consulta o al arrancar si se reabre una conversación con turnos.
- React, `react-dom` y `scheduler` van en un chunk `react` propio, que no cambia entre despliegues de la app.

Con eso la carga inicial quedó en 217 KiB (−36 %). Las pestañas precargan el chunk de su panel al pasar el ratón o al recibir el foco, y los Octicons se reexportan uno a uno en `ui/iconos.ts`.

Las trazas de Lighthouse enseñaron otras dos cosas que retrasaban el LCP. `/auth/me` salía después de `/auth/config` en la primera visita; ahora salen a la vez salvo que la app sepa que las cuentas están desactivadas. Y un panel abierto por enlace (`/?panel=avisos`) pedía su chunk, con unos 100 KiB de dependencias, en el mismo render que el shell, antes del primer pintado; `PanelHost` espera ahora a un fotograma después de ese pintado. Precargar ese chunk al arrancar empeoraba el LCP de «Optimizar» de 3,2 a 3,7 s, así que no se hace.

El CLS de 0,128 de casi todas las pantallas de móvil venía del aviso «Instala Cerebro en este dispositivo», que aparece con `beforeinstallprompt` después del primer pintado y empujaba el `PageLayout` hacia abajo. En Chrome y Edge la instalación es ahora un botón «Instalar la aplicación» en la cabecera; en iOS, donde no hay ese evento, el aviso con las instrucciones sigue y sale desde el primer render. El simulador y autónomos reservan el hueco de su resultado a su altura real (11 + 8,25 rem y 13,25 rem). El peor CLS de móvil pasó de 0,236 a 0,018.

### Objetivos que no se alcanzan

Se pedían 150 KiB de carga inicial, un LCP mediano de móvil de 2,5 s y 95 de Lighthouse en móvil. Quedan en 217 KiB, 3,46 s y 91. Lo que queda en la carga inicial es React (66 KiB), el CSS (35 KiB, del que 20 son el CSS principal y 15 el de los componentes de Primer) y el código de Primer que necesita la cabecera: `ActionMenu` y `AnchoredOverlay` para «Más» y la cuenta, `Tooltip` dentro de cada `IconButton`, `@primer/behaviors`, el polyfill de popover y `live-region-element`, unos 58 KiB. Bajar de ahí obliga a sacar Primer de la cabecera y de la caja de la consulta. `@primer/react` solo exporta su barril (no hay rutas por componente en su `exports`), así que tampoco se puede agrupar en un chunk «primer» sin arrastrar los componentes de los paneles: la prueba dio un chunk de 171 KiB.

El LCP de móvil es bimodal. En las seis pantallas cuyo mayor elemento sale con el shell (inicio, módulos, optimizar, tareas, preferencias, recuerdos) está en 2,75–2,88 s con 95–96 de Lighthouse. En las demás el mayor elemento es el contenido del panel, que necesita su chunk y el chunk compartido `FormControl` (40 KiB, porque el `FormControl` de Primer importa `Autocomplete`, `Select` y `TextInputWithTokens` para reconocer el campo), y queda en 3,2–3,6 s. «Entrar» (3,5 s) espera además a `/auth/me` y al chunk de `AuthScreen`.

### Paradas de foco fuera de pantalla

Las 52 paradas son todas los dos enlaces «Saltar a la consulta» y «Saltar al contenido» (el arnés guarda ahora cuáles en `fueraDePantallaDetalle`). Tenían `position: absolute` y, al volver a ellos con Tab desde el final de una página desplazada, quedaban arriba, fuera de la vista; ahora son `fixed`. Aun así el arnés los cuenta: en su recorrido el elemento tiene `:focus` y `document.hasFocus()` es verdadero, pero el estilo calculado sigue siendo el de reposo (`top: -100px`, `translateY(-200%)`). Con el mismo Chromium (Chrome for Testing y headless shell) y un recorrido de Tab aislado, los dos aparecen en `top: 8px`. Esperar un fotograma antes de leer lo corrige en «inicio» pero no en «conversación», así que el arnés se dejó como estaba para no cambiar la forma de medir. Fuera de esos dos enlaces no queda ninguna parada fuera de pantalla; los menús cerrados, las hojas laterales ocultas y las páginas de panel con `hidden` ya no reciben el foco. Los saltos atrás (15 → 21) son la vuelta de Tab desde el final de la página a esos enlaces, que ahora están arriba del viewport.

## Qué se portó y qué se descartó

De `47e6d72` (reabrir la última conversación) se portó todo tal cual: `lib/storage.ts` guarda la conversación en pantalla y `useConsulta` la reabre si sigue en el historial del dueño actual. El arnés ya no necesita volver a ella desde la lista al recargar.

De `f1b2e99` (avisos y módulos) se portaron «Reintentar» en los errores de Avisos y de Módulos (`ctx.modulos.recargar`) y el color de atención para lo pendiente de confirmar: la tarjeta de Módulos usa una variante nueva de `Caja`, `atencion`, en vez de `peligro`. Se descartaron tres cosas. La cabecera de Avisos ya tenía botones con texto en Primer («Mis preferencias», «Actualizar»). El aviso de acciones pendientes en la conversación ya era un `Flash` de tipo `warning`. Y meter «Marcar como leído» en un desplegable «Más acciones» empeoraba el flujo: era justo lo que impedía al arnés marcar el aviso. Con Primer cada aviso tiene tres controles de estilos coherentes («Preguntar al asesor», «Marcar como leído» y el menú «Tu respuesta»), así que se dejó así; tampoco se hizo primario «Preguntar al asesor», porque la guía pide un botón primario por vista y la bandeja tiene varios avisos.

De `aa4f9bb` (arranque) se portaron `/auth/me` en paralelo con `/auth/config` cuando el modo guardado es `required`, el monograma en el splash y la carga bajo demanda de `DictamenConsejo` y `ProgresoConsejo`, que con Primer había vuelto a la carga inicial a través de `Hilo` → `ConvocarConsejo`.

De `8d4e3bb` (navegación) se portaron las secciones de «Más» (`grupo` en el registro y `GRUPOS` en `paneles/tipos.ts`), los títulos cortos que empiezan por la etiqueta, los renombres «Mis preferencias» y «Recuerdos», las insignias que dicen qué cuentan («3 sin leer») y el test del registro. Además, las pestañas con `destacado` (avisos, módulos y expediente) ya no pasan nunca a «Más»: con Primer «Tu expediente» había acabado escondido en escritorio. Se descartaron la cabecera en dos filas y el menú «Cuenta» de la UI anterior: la cabecera de Primer ya pone la navegación debajo y la cuenta en un `ActionMenu`.

De `d9f96cf` (estados de carga y error) se portó el comportamiento con un componente de Primer, `ui/AvisoError` (un `Flash` con frase propia, el detalle del servidor y «Reintentar»), usado en simulador, autónomos, optimizador, planificador, tareas, consejo, investigar, navegador, preferencias, revisión, recuerdos, avisos en el móvil y los plazos del expediente. El simulador y autónomos reservan con un esqueleto el sitio del resultado mientras llega. Se portaron también los cambios de jerga («Calculado con fórmulas fijas…», «Leyes y criterios que guarda la app», «Internet», «la empresa que da la inteligencia artificial»). Se descartó `EstadoPanel.tsx` con sus clases de la UI anterior: los esqueletos `Cargando*` de `src/ui` ya cubrían la carga.

De `2ffcb1b` se portó el arreglo de la trampa de foco y se descartó el de los títulos del menú «Más»: `ActionList.GroupHeading` de Primer ya pone el papel correcto dentro de `role="menu"`.

## Los dos defectos que quedaban

El foco se escapaba de Autónomos porque la trampa de los paneles heredados (`FocoEnDialogo` en `PanelHost.tsx`) no contaba los `<summary>` y tomaba por última parada un campo que en realidad estaba antes. Ahora cuenta `summary`, deja fuera lo que está dentro de un `<details>` cerrado, oculto o inerte, y lo comprueba un test nuevo en `PanelHost.test.tsx` que falla con el código anterior. Con Primer, Autónomos ya es una página y no un diálogo, pero la trampa sigue sirviendo a cualquier panel sin `presentacion`. El arnés también tenía su parte: las hojas laterales cerradas siguen montadas y ocultas, y contaba como «fuera del diálogo» cada parada de una página; ahora solo mira el diálogo visible.

Marcar un aviso como leído ya funcionaba en la UI de Primer. Se cambiaron dos cosas para que sea fiable: `useAvisos` lo marca en pantalla al momento y lo devuelve a «no leído» si el servidor falla (`useAvisos.test.ts`), y el foco pasa al aviso cuando desaparece el botón (`NotificationsPanel.test.tsx`). El arnés cuenta el aviso como marcado solo si la pantalla tiene un botón «Marcar como leído» menos que antes.

## Incidencias abiertas

1. La carga inicial es de 217 KiB con gzip (135 en la base) y el LCP mediano de móvil de 3,46 s (2,4 en la base). La sección «Rendimiento en móvil» explica qué queda y por qué.
2. El CLS de móvil está por debajo de 0,1 en todas las pantallas (el peor, 0,018 en el simulador).
3. El arnés cuenta como fuera de pantalla los enlaces «Saltar…» al volver a ellos con Tab, aunque en Chrome salen arriba y visibles (sección «Paradas de foco fuera de pantalla»).
4. En escritorio hacen falta 30 pulsaciones de Tab para llegar a la caja de consulta (22 en la base) porque la lista de consultas va antes. El enlace «Saltar a la consulta» lo resuelve en una, pero el arnés no lo usa.
5. En móvil siguen 12 paneles tras «Más»: en 375 px solo caben las tres pestañas destacadas.
6. axe marca `region` en el menú flotante de «Más» (4 nodos, moderada): el contenido del `ActionMenu` de Primer se pinta en un portal fuera de los landmarks.
7. El inventario de Primer tiene más variedad que la UI anterior en espaciados (40 valores), sombras (16, casi todas las propias de los botones y menús de Primer) y colores de borde (12 por tema).
8. Autónomos, avisos en el móvil e investigar no explican su estado vacío; son formularios, como en la base.
9. Siguen sin cambiar los iconos de la PWA (la «H» de HaciendaGPT en los PNG), los textos legales de privacidad que hablan del «proveedor del modelo de lenguaje» y la jerga que envía el backend («Cálculo determinista» en `asistente/ambitos/fiscal/herramientas.py`).
