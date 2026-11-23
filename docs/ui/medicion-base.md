# Medición base de la UI antes del rediseño

Medición del 23 de noviembre de 2026 sobre la rama `claude/integracion-auditoria` (commit `7d46a4f`), antes de rehacer la interfaz con el sistema de diseño Primer. Sirve de referencia para comparar después: el arnés está en `frontend/medicion/` y se repite con `npm run medir -- --comparar base` (ver `frontend/medicion/README.md`).

Los datos completos están en `frontend/medicion/resultados/base.json`, las tablas por pantalla en `frontend/medicion/resultados/base.md` y las 136 capturas en `frontend/medicion/capturas/base/`.

## Condiciones

Build de producción servido con `vite preview` y una API simulada en Node con respuestas de ejemplo que pasan los validadores de `src/api*.ts`; no se arrancó el backend ni ningún modelo. Navegador: Chromium 153 headless de la caché de Playwright 1.65. Lighthouse 13.6 en modo navegación con su limitación de red y CPU simulada (móvil: 4G lenta y CPU ×4). Se midieron dos viewports, móvil (375×812, DPR 2, táctil) y escritorio (1440×900), cada uno con tema claro y oscuro. La sesión se inicia con la cuenta activada (`auth_mode: "required"`).

La máquina estaba compartida con otras sesiones. Para saber cuánto ruido hay, se repitió la medición entera justo después (`resultados/comparacion-base-repeticion.md`): todas las cifras de accesibilidad, consistencia, bundle y UX salieron idénticas; las de tiempo variaron. La mediana del LCP se movió un 1-2 %, pero el peor LCP de móvil pasó de 2.733 a 3.451 ms, el INP aproximado varió hasta un 69 % y el primer texto del asesor un 33 %. Después del rediseño, una diferencia de tiempo menor que esas no significa nada.

## Cifras principales

| Área | Cifra | Móvil | Escritorio |
|---|---|---:|---:|
| Rendimiento | Lighthouse rendimiento, mediana de 17 pantallas | 96 | 100 |
| Rendimiento | LCP, mediana | 2.427 ms | 608 ms |
| Rendimiento | LCP, peor pantalla | 2.733 ms | 744 ms |
| Rendimiento | CLS, peor pantalla | 0,283 | 0,061 |
| Rendimiento | TBT, mediana | 0 ms | 0 ms |
| Rendimiento | INP aproximado, peor interacción | 88 ms | 128 ms |
| Rendimiento | Primer texto del asesor tras «Enviar» | 1.355 ms | 818 ms |
| Accesibilidad | Lighthouse accesibilidad, mediana | 100 | 100 |
| Accesibilidad | Controles de menos de 24 px (pantallas en tema claro) | 159 | 159 |
| Accesibilidad | Tab hasta la caja de consulta | 22 | 22 |
| UX | Herramientas escondidas tras «Más» | 12 de 15 | 10 de 15 |
| UX | Clics medios para abrir una herramienta | 1,8 | 1,67 |

El primer texto del asesor depende de la simulación, que tarda unos 550 ms en empezar a escribir: sirve para comparar la interfaz consigo misma, no como dato del servicio real.

## Rendimiento

El bundle tiene 40 chunks de JavaScript: 776 KB sin comprimir y 235 KiB con gzip. La carga inicial (JS y CSS precargados por `index.html`) son 135 KiB con gzip, de los que 97 KiB son `index.js` (331 KB sin comprimir). Entre lo precargado están `DictamenConsejo.js` (9,5 KiB gzip) y `presentation.js` (9,2 KiB gzip), aunque el dictamen del consejo solo se ve dentro de su panel. Lighthouse calcula entre 44 y 58 KiB de JavaScript sin usar en cada pantalla. Los paneles más pesados se cargan bajo demanda: `optimizador.js` 18,5 KiB, `simulador.js` 12,6 KiB y `modulos.js` 9,9 KiB, todos con gzip. El CSS ocupa 8,3 KiB.

En escritorio todo sale entre 99 y 100. En móvil la pantalla de entrar pinta en 908 ms, pero cualquier pantalla con la sesión iniciada tarda entre 2,3 y 2,7 s en el LCP. La causa probable está en `src/App.tsx`: la aplicación pide `/auth/config`, espera la respuesta, pide `/auth/me` y solo entonces monta la shell; mientras tanto enseña un `Splash` vacío. Con la red lenta simulada, esas dos idas y vueltas en serie se notan.

El CLS de móvil es 0,086 en casi todas las pantallas por el contenedor de la conversación (`div.flex-1.overflow-y-auto`), y sube a 0,258 en el simulador y 0,283 en autónomos, donde se mueve un `<fieldset>` al llegar los datos.

## Accesibilidad

Lighthouse da 100 en accesibilidad en todas las pantallas, pero axe-core, con las reglas de WCAG 2.2 AA y buenas prácticas, encuentra 4 reglas incumplidas: 0 nodos críticos, 120 graves, 84 moderados y 72 menores, sumando las 84 combinaciones de pantalla, viewport y tema.

| Regla | Gravedad | Nodos | Dónde |
|---|---|---:|---|
| `label-content-name-mismatch` | grave | 120 | Botones de la barra cuyo texto visible no está en su nombre accesible: se lee «Módulos» y se llama «Módulos del hogar» o «Módulos: 1 acciones pendientes de tu confirmación»; en «Tu expediente» se ve «4 datos». |
| `heading-order` | moderada | 80 | En los paneles se salta de `h1`/`h2` a `h3`, por ejemplo `.mt-5 > h3`. |
| `aria-allowed-role` | menor | 72 | El `role="dialog"` va sobre un elemento que no lo admite (`.max-w-[440px]` y equivalentes en cada panel). |
| `page-has-heading-one` | moderada | 4 | La pantalla de entrar no tiene `h1`. |

axe no encuentra problemas de contraste, lo que cuadra con `src/index.css`, que eligió los tokens de texto para superar 4,5:1 sobre `paper` y `surface` en los dos temas. axe no evalúa los controles desactivados, y el botón «Enviar» desactivado (`primary` al 50 % de opacidad) queda casi ilegible en las capturas.

El recorrido con Tab no pierde el foco en ningún diálogo (0 salidas de un modal), todos los paneles lo devuelven al botón que los abrió y el flujo completo solo con teclado funciona en los dos viewports: entrar, preguntar, abrir «Tu expediente» y cerrarlo con Escape, en 36 pulsaciones. Solo hay dos paradas sin indicador de foco visible, ambas en un campo del optimizador. No existe un enlace para saltar al contenido, y la caja de consulta queda a 22 pulsaciones de Tab porque va detrás de la barra lateral, la barra de herramientas y las cuatro sugerencias. En la conversación en móvil, 12 paradas de foco quedan fuera del área visible cuando reciben el foco.

En móvil, 713 controles miden menos de 44 px de lado (la recomendación habitual para el dedo) y 159 menos de 24 px, el mínimo de WCAG 2.5.8. El simulador concentra 25 de estos últimos y el planificador 15.

Hay 40 controles que solo muestran un icono y dependen de `aria-label` o del `title`. En la pantalla de inicio en móvil son siete: menú, avisos, módulos, más herramientas, expediente, tema y enviar.

## Consistencia visual

El inventario sale de `getComputedStyle` sobre todos los elementos visibles de las 84 combinaciones medidas.

| Propiedad | Valores distintos | Los más usados |
|---|---:|---|
| Tamaño de fuente | 17 | 12,5 · 12 · 11 · 13 · 13,5 · 11,5 · 16 · 14 · 15 · 14,5 · 15,5 · 22 · 19 · 18 · 20 · 30 · 26 px |
| Peso de fuente | 4 | 400, 600, 500, 700 |
| Altura de línea | 33 | 18,8 · 19,5 · 18 · 16,5 · 20,3 px… |
| Familia | 3 | ui-sans-serif, ui-monospace, Georgia (títulos) |
| Espaciado (padding, margin, gap) | 28 | 8 · 12 · 4 · 6 · 16 · 10 · 20 · 2 px…, más 159 y 159,5 px |
| Radio de borde | 6 | 8, pill, 12, 6, 4 y 16 px |
| Sombra | 5 | `shadow-card` y `shadow-pop` en cada tema, más un `shadow-sm` de Tailwind fuera de los tokens |
| Altura de botón | 25 | 27, 34, 38, 36, 20, 22, 31 y 28 px… |
| Altura de campo | 13 | 39, 42, 34, 33 y 37 px… |

Los 17 tamaños de fuente vienen de valores escritos a mano en las clases (`text-[13.5px]`, `text-[12.5px]`…), muchos a medio píxel unos de otros: entre 11 y 16 px hay 11 tamaños. La escala de Primer usa 12, 14, 16, 20, 24 y 32 px y tres alturas de control (28, 32 y 40 px); la app tiene 25 alturas de botón.

Los colores salen más ordenados: 9 de texto, 16 de fondo y 6 de borde en tema claro (9, 17 y 6 en oscuro), casi todos tokens. Lo que no es token son opacidades de `ink`, `line`, `primary` y `risk-high-fg` (`#1b232f66` para el velo de los paneles, `#e4ded499`, `#14645e80`, `#b2372566`), el blanco fijo del texto sobre `primary`, que se usa 214 veces, y 6 fondos blancos fijos que siguen blancos en tema oscuro.

## UX: flujos y estados

| Flujo | Clics | Campos | Pasos |
|---|---:|---:|---:|
| Entrar (correo, contraseña, «Entrar») | 1 | 2 | 4 |
| Preguntar al asesor | 2 | 1 | 3 |
| Guardar un dato en el expediente | 3 | 1 | 4 |
| Ver avisos y marcar uno como leído | 2 | 0 | 2 |
| Abrir una herramienta de la barra | 1 | 0 | 1 |
| Abrir una herramienta de «Más» | 2 | 0 | 2 |

Los flujos son cortos. El problema es encontrar las cosas: la barra solo muestra avisos, módulos y expediente en móvil (más simulador y autónomos en escritorio), y el resto de las 15 herramientas va en una sola lista dentro de «Más», sin agrupar. En móvil esa lista ocupa más que la pantalla.

Estados con la API lenta (2,5 s), caída (500) o sin datos, en escritorio claro:

- Todas las pantallas que piden datos enseñan un mensaje cuando el servicio falla, pero ninguna, salvo el asesor, ofrece reintentar. Hay que cerrar y volver a abrir el panel o recargar.
- Seis pantallas no muestran nada mientras cargan: simulador, autónomos, consejo, preferencias, revisión y lo que sé de ti. El panel abre con su contenido vacío durante 2,5 s.
- En expediente, simulador, autónomos, tareas, investigar y avisos en el móvil, el error es el `detail` del servidor tal cual, sin una frase propia que diga qué falló.
- El navegador desaparece del menú «Más» mientras el servicio no responde, sin aviso.
- Los estados vacíos de avisos, tareas, consejo, preferencias, revisión y lo que sé de ti explican qué aparecerá y cuándo. La heurística marca 4 pantallas sin explicación, pero tres son formularios (autónomos, investigar y avisos en el móvil) y el expediente no se pudo vaciar porque sus datos vienen de la conversación guardada en el navegador.

El detector de textos encontró 61 casos. Los más claros son nombres accesibles de 70 a 110 caracteres en el menú «Más» («Consejo del hogar: decisiones difíciles estudiadas por varias IA, por separado y de forma anónima»), jerga técnica en pantalla («Corpus legal de la app», «Web (buscador del proveedor)», «Cálculo determinista», «se envía al proveedor del modelo de lenguaje») y la etiqueta genérica «Más».

## Las 15 peores incidencias de UX

Ordenadas por impacto: cuántas personas lo sufren, en cuántas pantallas y cuánto les cuesta.

1. Recargar la página pierde la consulta abierta. La app arranca siempre en «Nueva consulta» y hay que volver a la anterior desde «Recientes», que en móvil está detrás del menú hamburguesa. Pasa con cada recarga, cada vuelta a la PWA que el sistema haya descargado de memoria y cada enlace `/?panel=…`.
2. Ningún error de panel permite reintentar (12 pantallas). Solo el asesor tiene botón de reintento; en el resto, el mensaje se queda fijo hasta cerrar el panel o recargar.
3. 12 de las 15 herramientas están escondidas tras «Más» en móvil y 10 de 15 en escritorio, en una lista plana. En móvil el menú es una hoja inferior más alta que la pantalla (captura `menu-mas--movil--claro.png`).
4. La cabecera móvil no se entiende. Son cinco botones con solo un icono (campana, casa, tres puntos, carpeta y luna), el título de la consulta se corta en «¿Teng…» o «Nueva co…», y encima ocupa una fila entera la barra de cuenta con el correo cortado y «Cerrar sesión» al mismo nivel que todo lo demás (captura `inicio--movil--claro.png`).
5. Los nombres accesibles no coinciden con el texto visible en los botones de la barra (120 nodos de `label-content-name-mismatch`). Quien usa control por voz dice «Módulos» y el botón se llama «Módulos: 1 acciones pendientes de tu confirmación»; el expediente enseña «4 datos» y se llama «Tu expediente».
6. Los paneles tardan en pintar en móvil: LCP de 2,3 a 2,7 s con la sesión iniciada frente a 0,9 s en la pantalla de entrar, por las dos peticiones de sesión en serie antes de montar la shell, con un `Splash` vacío mientras tanto.
7. Seis paneles no indican que están cargando. Con el servicio lento, el simulador, autónomos, consejo, preferencias, revisión y lo que sé de ti se ven vacíos o con ceros durante segundos, y parece que no hay datos.
8. Los errores enseñan el texto interno del servidor. En el expediente, «Próximos plazos» muestra en rojo el `detail` de la API y nada más; si el servidor manda algo técnico, eso es lo que lee la persona.
9. Una acción pendiente de confirmar se pinta como un peligro. El aviso «Tienes 1 acción pendiente de tu confirmación» usa `risk-high-bg` con borde rojo en la conversación, y la tarjeta «Pendiente de tu confirmación» de Módulos lleva borde rojo y un escudo con exclamación. Es un paso normal del flujo, no un error.
10. Hay tres pantallas con nombres casi iguales: «Tu expediente» («Lo que sé de ti en esta consulta»), «Lo que sé de ti» (recuerdos) y «Preferencias» («Lo que sé de tus preferencias»). Además, la marca dice «HaciendaGPT · Asesor fiscal» y la caja «Escribe tu consulta fiscal…», mientras los módulos ya cubren luz y gas, vivienda, tareas y un agente con navegador.
11. Cada aviso tiene cinco acciones con tres estilos distintos («Preguntar al asesor», «Marcar como leído», «No me interesa», «Ya lo hice», «Recuérdamelo más tarde»), y la cabecera del panel añade dos botones de solo icono sin texto: preferencias y actualizar (captura `panel-avisos--movil--claro.png`).
12. Hay 159 controles por debajo de 24 px y 713 por debajo de 44 px en móvil, sumando las pantallas en tema claro. Por pantalla, los que más tienen por debajo de 24 px son el simulador (25), el planificador (15), módulos y optimizador (10 cada uno).
13. La escala tipográfica está desordenada: 17 tamaños de fuente (11 de ellos entre 11 y 16 px), 33 alturas de línea, 25 alturas de botón y 13 de campo. Son los valores que el rediseño tiene que reducir a los pasos de Primer.
14. El simulador y autónomos saltan al cargar en móvil, con un CLS de 0,258 y 0,283 (el umbral de «bueno» es 0,1): el `<fieldset>` cambia de alto cuando llegan los datos del caso, y el resto de pantallas tienen 0,086 por el contenedor de la conversación.
15. Hay jerga interna en pantalla: «Cálculo determinista, sin inteligencia artificial», «Corpus legal de la app», «Web (buscador del proveedor)», «Su contenido se envía al modelo de lenguaje» y «Consejo… estudiadas por varias IA». Son pantallas pensadas para una familia que hace la renta.

## Paleta que el rediseño tiene que conservar

Valores resueltos con `getComputedStyle` en `<html>` con cada tema (`src/index.css`). Los tokens base son tripletes RGB (`--paper: 246 244 239`) que Tailwind expone como `--color-*`. Los usos son el número de elementos visibles que pintan ese color como texto, fondo o borde en todas las pantallas medidas.

| Token | Uso | Claro | Oscuro | Usos (claro) |
|---|---|---|---|---:|
| `--paper` | Fondo de la página | `#f6f4ef` | `#10141a` | 244 |
| `--surface` | Tarjetas, campos, paneles | `#ffffff` | `#191f28` | 1.102 |
| `--ink` | Texto principal | `#1b2330` | `#e9edf2` | 616 |
| `--ink-soft` | Texto secundario | `#404a59` | `#c4ccd6` | 942 |
| `--muted` | Texto terciario, ayudas | `#5e6774` | `#96a0ae` | 1.285 |
| `--line` | Bordes y separadores | `#e4ded3` | `#2e3642` | 1.305 |
| `--primary` | Botón principal, acento | `#14635e` | `#1a7a73` | 197 |
| `--primary-hover` | Hover del botón principal | `#0f4f4a` | `#218f87` | sin dato propio |
| `--primary-dark` | Texto de acento y enlaces | `#0f4f4a` | `#86d6cc` | 498 |
| `--primary-soft` | Fondo de acento suave | `#e2eeec` | `#183432` | 516 |
| `--navy` | Burbuja de la pregunta | `#1f3a5f` | `#2c4c78` | 36 |
| `--shadow` | Base de las sombras | `#1b2330` | `#000000` | sin dato propio |
| `--cited-fg` | Verificado, riesgo bajo: texto | `#186a46` | `#76d0a0` | 188 |
| `--cited-bg` | Verificado, riesgo bajo: fondo | `#e8f3ec` | `#143024` | 114 |
| `--cited-bd` | Verificado: borde | `#c2e2cf` | `#26543d` | 36 |
| `--uncited-fg` | Sin cita, riesgo medio: texto | `#8c5a04` | `#ecbc60` | 36 |
| `--uncited-bg` | Sin cita, riesgo medio: fondo | `#fbf1da` | `#382b10` | 36 |
| `--uncited-bd` | Sin cita: borde | `#eed8a6` | `#624b1c` | 0 |
| `--abstain-fg` | Se abstiene: texto | `#4c596b` | `#acb6c4` | 10 |
| `--abstain-bg` | Se abstiene: fondo | `#eef0f3` | `#222832` | 10 |
| `--abstain-bd` | Se abstiene: borde | `#d6dbe2` | `#3a4250` | 0 |
| `--risk-high-fg` | Riesgo alto, urgente, insignias | `#b23826` | `#f28874` | 114 |
| `--risk-high-bg` | Riesgo alto: fondo | `#fae8e4` | `#3c1b17` | 42 |

`--color-risk-medium-*` y `--color-risk-low-*` no tienen valor propio: son alias de `uncited` y `cited`. `--primary-hover` coincide con `--primary-dark` en claro, así que los usos de la tabla no los separan; los de `--surface` incluyen los 214 textos blancos sobre `primary`, que tienen el mismo valor en claro. Fuera de los tokens, la app usa el blanco `#ffffff` para el texto sobre `primary` en los dos temas, el velo de los paneles (`ink` al 40 %), `line` al 50-70 % en los hover, y `#f6f4ef` / `#10141a` como `theme-color` de la PWA (`src/lib/theme.ts`).

En Primer estos tokens corresponden a `bgColor-default` (`paper`), `bgColor-muted` o `overlay` (`surface`), `fgColor-default` (`ink`), `fgColor-muted` (`muted`), `borderColor-default` (`line`), `bgColor-accent-emphasis` (`primary`), `bgColor-success-muted`/`fgColor-success` (`cited`), `fgColor-attention` (`uncited`) y `fgColor-danger` (`risk-high`). La tabla completa, con los alias que genera Tailwind, está en `resultados/base.md`.

## Qué no mide este arnés

La API es simulada: los tiempos de respuesta del asesor y de los cálculos no son los del servicio. INP no existe en el modo navegación de Lighthouse; la cifra «INP aproximado» sale de la Event Timing API durante los flujos de Playwright en headless y solo vale para ver cambios grandes. El orden del foco se juzga con una heurística (saltos de más de 48 px hacia arriba sin cambiar de columna) y los estados vacíos y de carga con expresiones regulares sobre el texto: las cifras sirven para comparar, pero cada caso se debe revisar en su captura. Los textos se revisaron a mano solo en las capturas de móvil claro y escritorio claro.
