# Medición de la UI con Primer frente a la base

Medición del 23 de noviembre de 2026 sobre la rama `claude/portar-ux-014bae`: la UI de `main` rediseñada con Primer, más las mejoras de UX portadas desde `claude/medir-ui-9b7c28` y la accesibilidad de `claude/jovial-meninsky-caf31e`. Se compara con la medición base (`docs/ui/medicion-base.md`), hecha sobre la UI anterior a Primer.

Se repite con `npm run medir -- --comparar base` desde `frontend/`. Los datos están en `frontend/medicion/resultados/primer.json`, las tablas por pantalla en `primer.md`, la comparación completa en `comparacion-base-primer.md` y las capturas en `frontend/medicion/capturas/primer/`.

## Condiciones

Las mismas que la base: build de producción con `vite preview`, la API simulada de `frontend/medicion/api-simulada.mjs`, Chromium 153 headless de la caché de Playwright 1.65, Lighthouse 13.6 de uno en uno, móvil (375×812) y escritorio (1440×900) en tema claro y oscuro, todo con `nice -n 15`. No se arrancó el backend ni ningún modelo.

La máquina estaba compartida con sesiones de QA. La base ya mostró cuánto ruido hay en las cifras de tiempo: el peor LCP de móvil varió de 2.733 a 3.451 ms entre dos mediciones seguidas y el INP aproximado hasta un 69 %. Las diferencias de tiempo menores que eso no significan nada. Las de accesibilidad, consistencia, bundle y UX salieron idénticas entre repeticiones.

El arnés se adaptó a la UI con Primer sin cambiar lo que mide: el botón de enviar se llama ahora «Preguntar», la navegación son las pestañas `nav[aria-label="Secciones"]` y un panel abierto puede ser una hoja lateral (diálogo modal) o una página en la columna principal. Las páginas no se cierran con Escape, como en GitHub; el recorrido con teclado vuelve a «Conversación» con Mayús+Tab e Intro, y eso cuenta como cerrar con teclado.

## Antes y después

| Área | Cifra | Base | Primer |
|---|---|---:|---:|
| Rendimiento | Lighthouse rendimiento, mediana móvil / escritorio | 96 / 100 | 86 / 99 |
| Rendimiento | LCP mediana móvil / escritorio | 2.427 / 608 ms | 3.643 / 824 ms |
| Rendimiento | LCP peor pantalla, móvil | 2.733 ms | 3.957 ms |
| Rendimiento | CLS peor pantalla, móvil / escritorio | 0,283 / 0,061 | 0,236 / 0,045 |
| Rendimiento | INP aproximado, peor interacción móvil / escritorio | 88 / 128 ms | 48 / 48 ms |
| Bundle | Carga inicial JS+CSS (gzip) | 135 KiB | 339,9 KiB |
| Bundle | JS total / CSS (gzip) | 234,8 / 8,3 KiB | 394,5 / 71 KiB |
| Accesibilidad | Nodos con violaciones axe: graves / moderadas / menores | 120 / 84 / 72 | 0 / 4 / 0 |
| Accesibilidad | Reglas axe incumplidas | 4 | 1 |
| Accesibilidad | Paradas de foco sin indicador visible | 2 | 0 |
| Accesibilidad | Saltos atrás en el orden del foco | 49 | 15 |
| Accesibilidad | Paradas de foco fuera de pantalla | 14 | 49 |
| Accesibilidad | Foco que se escapa de un diálogo modal | 0 | 0 |
| Accesibilidad | Tab hasta la caja de consulta, móvil / escritorio | 22 / 22 | 22 / 30 |
| Accesibilidad | Controles de menos de 24 px | 159 | 66 |
| Accesibilidad | Controles que solo muestran un icono | 40 | 1 |
| Consistencia | Tamaños de fuente distintos | 17 | 8 |
| Consistencia | Alturas de línea distintas | 33 | 18 |
| Consistencia | Alturas de botón / de campo distintas | 25 / 13 | 12 / 5 |
| Consistencia | Valores de espaciado distintos | 28 | 40 |
| Consistencia | Sombras distintas | 5 | 16 |
| Consistencia | Colores de borde distintos (cada tema) | 6 | 12 |
| UX | Entrar: clics + campos | 3 | 3 |
| UX | Preguntar: clics + campos | 3 | 3 |
| UX | Guardar un dato en el expediente: clics + campos | 4 | 4 |
| UX | Ver avisos y marcar uno como leído: clics | 2 | 2 |
| UX | Clics medios para abrir un panel, móvil / escritorio | 1,8 / 1,67 | 1,8 / 1,33 |
| UX | Paneles escondidos tras «Más», móvil / escritorio | 12 / 10 | 12 / 5 |
| UX | Pantallas sin indicador de carga | 6 | 0 |
| UX | Errores sin opción de reintentar | 12 | 0 |
| UX | Pantallas vacías sin explicación | 4 | 3 |
| UX | Textos poco claros detectados | 61 | 9 |

En la base, «Ver avisos y marcar uno» contaba 2 clics porque «Marcar como leído» estaba a la vista. En la rama de correcciones de la UI anterior pasó dentro de un `<details>` «Más acciones» y el arnés ya no lo encontraba; en esta medición el aviso queda marcado en los cuatro contextos y el foco se queda en el aviso («Quedan 12 días para el pago fraccionado del 4T») en vez de perderse.

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

1. La carga inicial pasó de 135 a 340 KiB con gzip y el LCP de móvil de 2,4 a 3,6 s (mediana). Lo precargado es sobre todo Primer: `ui.js` (89 KiB), su CSS (16 KiB), `FormControl.js` (39 KiB) y un `index.css` de 40 KiB, frente a 8 KiB de CSS en la base. En escritorio la diferencia apenas se nota (99 de Lighthouse). Hace falta repartir Primer en chunks por panel o quitar de la carga inicial los componentes que solo usan los paneles.
2. El CLS de móvil sigue por encima de 0,1: 0,236 en el simulador, 0,231 en autónomos y 0,128 en otras siete pantallas. Lighthouse señala la raíz del `PageLayout`, no el formulario; el hueco reservado para el resultado no basta.
3. Las paradas de foco fuera de pantalla subieron de 14 a 49. No se ha investigado qué pantallas las concentran; `primer.md` tiene el detalle por pantalla.
4. En escritorio hacen falta 30 pulsaciones de Tab para llegar a la caja de consulta (22 en la base) porque la lista de consultas va antes. El enlace «Saltar a la consulta» lo resuelve en una, pero el arnés no lo usa.
5. En móvil siguen 12 paneles tras «Más»: en 375 px solo caben las tres pestañas destacadas.
6. axe marca `region` en el menú flotante de «Más» (4 nodos, moderada): el contenido del `ActionMenu` de Primer se pinta en un portal fuera de los landmarks.
7. El inventario de Primer tiene más variedad que la UI anterior en espaciados (40 valores), sombras (16, casi todas las propias de los botones y menús de Primer) y colores de borde (12 por tema).
8. Autónomos, avisos en el móvil e investigar no explican su estado vacío; son formularios, como en la base.
9. Siguen sin cambiar los iconos de la PWA (la «H» de HaciendaGPT en los PNG), los textos legales de privacidad que hablan del «proveedor del modelo de lenguaje» y la jerga que envía el backend («Cálculo determinista» en `asistente/ambitos/fiscal/herramientas.py`).
