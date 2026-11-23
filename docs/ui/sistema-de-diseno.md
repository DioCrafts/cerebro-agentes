# Sistema de diseño de la interfaz

La interfaz web (`frontend/`) copia la estructura, la tipografía, el espaciado, la densidad, los componentes y los patrones de interacción de GitHub, con los colores de la app. La base es Primer, el sistema de diseño que GitHub publica con licencia MIT: `@primer/react` 38, `@primer/primitives` 11 y `@primer/octicons-react` 19. Del lenguaje de diseño de GitHub se usa todo; de su marca, nada: ni el logotipo ni el nombre aparecen en la app.

Esta guía es para quien migre un panel. Si solo vas a leer una sección, lee «Cómo migrar un panel» y «Prohibido».

## Por qué `@primer/react` y no primitivas propias

Se comprobó en noviembre de 2026 con `@primer/react` 38.46.0:

- Admite React 19: su `peerDependencies` pide `react: 18.x || 19.x` (y `react-is` de la misma versión, que se instala aparte).
- Desde la versión 38 ya no usa styled-components ni la prop `sx`. Cada componente trae su CSS Module, sin capa (`@layer`). Tailwind 4 pone su reset y sus utilidades en las capas `base` y `utilities`, así que los estilos de Primer siempre ganan al reset de Tailwind y una utilidad de Tailwind solo pisa a Primer si lleva `!`.
- Hubo dos choques y los dos están resueltos:
  1. `PageLayout` define su propia variable `--spacing`, la misma que usa Tailwind para `p-*`, `gap-*` y `m-*`. Dentro de una página todas esas utilidades valían 0. `src/ui/tokens.css` fija `--spacing` con `@theme inline`, de modo que cada utilidad lleva el valor escrito (`gap-6` = `calc(1.5rem)`).
  2. `UnderlineNav` escribe en inglés el botón de desbordamiento («More») y no deja cambiarlo. `src/ui/PestanasNavegacion.tsx` reproduce sus estilos y su comportamiento con el menú «Más».
- Vitest necesita procesar el CSS que importa Primer desde su JavaScript: `vitest.config.ts` lo hace con `server.deps.inline`.

Escribir primitivas propias sobre `@primer/primitives` habría supuesto rehacer foco, menús, diálogos y navegación por teclado, que Primer ya resuelve y prueba con lectores de pantalla.

## Tema: los colores de la app en los papeles de Primer

Los colores siguen donde estaban, en `src/index.css`, como tripletas RGB (`--paper`, `--surface`, `--ink`, `--primary`…), en claro bajo `:root` y en oscuro bajo `.dark`. `src/ui/tema.css` asigna cada papel de color de Primer a uno de esos colores. Primer pone el resto tal cual: tipografía (sistema, 14 px con interlineado 1,5), escala de espaciado de 4 px, radios (3, 6 y 12 px), sombras, anillo de foco y alturas de control.

| Papel de Primer | Claro | Oscuro |
| --- | --- | --- |
| canvas `bgColor-default` (página, cajas) | `surface` (blanco) | `paper` |
| canvas `bgColor-muted` (cabecera, cabeceras de caja, barra lateral) | `paper` | `surface` |
| `fgColor-default` / `fgColor-muted` | `ink` / `muted` | `ink` / `muted` |
| `borderColor-default` | `line` | `line` |
| accent (enlaces, foco, botón principal, pestaña actual) | `primary`, fondo suave `primary-soft` | texto `primary-dark`, fondo `primary` |
| success | `cited-*` (verde) | `cited-*` |
| attention | `uncited-*` (ámbar) | `uncited-*` |
| danger y severe | `risk-high-*` (rojo) | `risk-high-*` |
| done | `navy` | mezcla de `navy` e `ink` |
| neutral | `abstain-*` (pizarra) | `abstain-*` |

Los fondos `*-emphasis`, que llevan texto blanco encima, usan en los dos temas los colores de texto del tema claro (`--enfasis-*` en `index.css`). En oscuro, los verdes, ámbares y rojos claros no llegan a 4,5:1 bajo texto blanco.

Contraste medido sobre el lienzo en el que se usa cada color (claro / oscuro): texto normal 15,8 / 15,7; `fg-muted` 5,2 / 6,3; `fg-accent` 6,4 / 9,9; `fg-success` 6,0 / 8,9; `fg-attention` 5,3 / 9,4; `fg-danger` 5,5 / 6,7; `fg-done` 10,5 / 5,9. Blanco sobre cualquier `*-emphasis`, 5,1 como mínimo. El borde de los campos de texto usa `borderColor-emphasis`, que llega a 3,1:1 (el mínimo de WCAG para componentes de interfaz); el `borderColor-default` de la app, como el de GitHub, se queda por debajo y no se usa para recuadrar campos.

El tema se aplica en `<html>` con la clase `dark` y los atributos `data-color-mode`, `data-light-theme` y `data-dark-theme`. Lo hacen `public/tema.js` antes del primer pintado y `src/lib/theme.ts` al cambiarlo. Como están en `<html>`, los menús y diálogos de Primer, que se pintan en portales fuera de `#root`, también los heredan.

## Estructura de la app

`src/shell/Asesor.tsx` monta una página de GitHub:

1. La cabecera global (`CabeceraGlobal` sobre `ui/CabeceraApp`) lleva el enlace «Saltar al contenido», botón de menú (solo en móvil), monograma, migas «Cerebro / título de la consulta» (es el `h1`), campo de búsqueda y comandos, tema, avisos con punto de no leídos y menú de cuenta.
2. Debajo, las pestañas de sección (`ui/PestanasNavegacion`) muestran «Conversación» y un enlace por panel del registro, con contador. Lo que no cabe va a «Más», agrupado por las secciones de `GRUPOS` (`paneles/tipos.ts`): Impuestos, Hogar, Tú y tus datos, Agentes. «Conversación», la pestaña actual y los paneles con `destacado` (avisos, módulos y expediente) nunca pasan a «Más». El nombre accesible de cada pestaña es su texto visible; el contador añade, solo para lectores de pantalla, lo que cuenta («Avisos, 3 sin leer»). Cada pestaña es un enlace real (`/?panel=avisos`), así que Ctrl o ⌘ más clic la abre en otra pestaña del navegador.
3. La paleta de comandos (`ui/PaletaComandos`) se abre con `/` o Ctrl/⌘+K y busca secciones, consultas y acciones sin distinguir mayúsculas ni tildes.
4. El `PageLayout` tiene a la izquierda las consultas (`shell/ListaConsultas`, un `NavList` con el botón «Nueva»); en móvil se abren en un `Dialog` lateral. En el contenido, la conversación o el panel que se muestre como página.
5. La conversación (`components/Hilo.tsx` sobre `ui/Conversacion`) es la línea de tiempo de GitHub. Cada pregunta es un comentario del usuario con cabecera teñida de acento; cada respuesta, un comentario de Cerebro con botón de copiar, cuerpo en Markdown con el estilo de GitHub (`ui/markdown.css`) y la valoración en el pie. La respuesta en curso aparece como un evento de la línea de tiempo («está buscando en la normativa…») seguido de un comentario que se rellena en directo. El formulario (`components/Composer.tsx`) es el de comentar de GitHub y queda fijo al pie mientras se desplaza la página.

## Página o diálogo

Cada panel del registro (`src/paneles/registro.ts`) declara cómo se muestra con `presentacion`:

- `"pagina"`: ocupa el contenido, bajo su pestaña, en lugar de la conversación, con un `PageHeader` (icono, título y descripción). Es para trabajo que lleva un rato y se consulta a pantalla completa: simulador, autónomos, optimizador, planificador, tareas, investigar, revisión, módulos o el expediente.
- `"dialogo"`: hoja lateral sobre la conversación (`ui/HojaLateral`), para echar un vistazo o hacer un cambio rápido sin perder el hilo: avisos, preferencias, lo que sé de ti, avisos en el móvil.
- sin `presentacion`: panel todavía sin migrar. Dibuja su propia capa, como antes.

En los dos primeros casos el componente del panel pinta solo su contenido; el shell pone el marco, el título (`titulo`), el subtítulo (`descripcion`) y el icono. Los dos marcos siguen montados al cerrarse, así que lo que el usuario escribió sigue ahí al volver. La hoja admite `ancho: "medium" | "large" | "xlarge"` (480, 640 o 960 px; en móvil ocupa toda la pantalla).

Si un panel necesita acciones propias (actualizar, abrir otra cosa), van en una fila de botones `size="small"` al principio del contenido, alineada a la derecha, como en el panel de avisos.

## Qué usar para cada cosa

Todo se importa de `src/ui` (componentes) y de `src/ui/iconos` (Octicons).

| Necesitas | Usa |
| --- | --- |
| Un bloque con borde, cabecera y filas (lista, tarjeta, sección de ajustes) | `Caja`, `Caja.Cabecera`, `Caja.Lista` › `Caja.Fila`, `Caja.Cuerpo`, `Caja.Pie`; `variante="atencion"` para lo que espera una confirmación, `"peligro"` solo para lo que borra o falla |
| Un error al cargar o al hacer algo | `AvisoError` (`frase` propia, `detalle` del servidor y `onReintentar` si se puede repetir), o `AvisoFallo` con un `Fallo` guardado en el estado |
| Un aviso dentro de un panel | `Flash` (`variant="warning"`, `"success"`) |
| Un estado vacío | `Blankslate` con `Blankslate.Visual` (Octicon de 24), `Heading` y `Description` |
| Algo que se está cargando | `CargandoLista`, `CargandoTexto`, `CargandoBloque` (skeletons). `Spinner` solo dentro de un botón o junto a una palabra |
| Botones | `Button` (`variant="primary"` para la acción principal, una por vista; `"danger"` para borrar; `"invisible"` para acciones secundarias), `IconButton` con `aria-label` |
| Etiquetas de estado o importes | `Label` (`accent`, `success`, `attention`, `danger`, `done`, `secondary`), `StateLabel`, `CounterLabel` |
| Formularios | `FormControl` con `FormControl.Label`, `FormControl.Caption` y `FormControl.Validation`; `TextInput`, `Textarea`, `Select`, `Checkbox`, `Radio`, `ToggleSwitch` |
| Una confirmación | `useConfirm()` o `ConfirmationDialog`, nunca `window.confirm` en código migrado |
| Menús | `ActionMenu` con `ActionList` |
| Navegación dentro de un panel | `SegmentedControl` (2 a 5 vistas de lo mismo) o `NavList` |
| Un texto de la app en Markdown (respuestas, avisos) | `RichText` (pinta `.markdown-body` y las citas `[F2]`) |
| Una línea de tiempo | `Conversacion`, `Comentario`, `EventoConversacion` o `Timeline` |
| Títulos | `Heading` (`as="h2"`/`"h3"` según la jerarquía real, `variant="small"/"medium"/"large"`) |
| Texto secundario | clase `text-fg-muted`; tamaño pequeño `text-xs` (12 px) |

Para maquetar sirven las utilidades de Tailwind de diseño (`flex`, `grid`, `gap-*`, `p-*`, `m-*`, `w-*`, `sm:`/`md:`) y los tokens de color, radio y sombra de Primer con nombre de Tailwind, definidos en `src/ui/tokens.css`:

- Texto: `text-fg-default`, `text-fg-muted`, `text-fg-accent`, `text-fg-success`, `text-fg-attention`, `text-fg-danger`, `text-fg-done`, `text-fg-on-emphasis`.
- Fondos: `bg-canvas-default`, `bg-canvas-muted`, `bg-accent-muted`, `bg-danger-muted`… (todos los papeles con `-muted` y `-emphasis`).
- Bordes: `border-border-default`, `border-border-muted`, `border-border-accent`, `border-border-danger-muted`…
- Radios: `rounded-small` (3 px), `rounded-medium` (6 px), `rounded-large` (12 px). Sombras: `shadow-resting-sm`, `shadow-floating-md`.

La escala de espaciado es la de Primer: 4, 8, 12, 16, 24, 32 px, que en Tailwind son `1`, `2`, `3`, `4`, `6`, `8`. Dentro de una caja, 16 px (`p-4`); entre cajas, 16 px; entre secciones de una página, 24 px.

## Lo que entra en la carga inicial

La carga inicial (lo que `index.html` enlaza o precarga) es React, el shell (cabecera, pestañas, `PageLayout`, lista de consultas, estado vacío y caja de la consulta) y lo que Primer necesita para eso. En noviembre de 2026 eran 217 KiB con gzip. Todo lo demás llega con un `import()` dinámico:

- cada panel del registro (`cargar` en `paneles/registro.ts`), que las pestañas precargan al pasar el ratón o al recibir el foco (`precargarPanel`);
- los turnos de la conversación (`Hilo`), que se piden en reposo tras el primer pintado o al arrancar si se reabre una conversación con turnos (`shell/precargas.ts`);
- la paleta de comandos, el menú de consultas del móvil, la pantalla de entrada y «Privacidad y datos».

Si un componente nuevo solo se ve tras un clic, impórtalo con `React.lazy` desde quien lo abre, no desde `src/ui/index.ts`: lo que exporta ese barril y usa el shell acaba en la carga inicial. Los Octicons se reexportan uno a uno en `src/ui/iconos.ts`; un icono nuevo es un nombre más en esa lista.

El build poda las variables de los dos temas de `@primer/primitives` que nadie lee (`frontend/plugins-vite/podarVariables.ts`): de unas 1.900 por tema quedan las que nombran `src/` o `@primer/react`. Si una variable de Primer deja de funcionar en la app, mira primero si aparece escrita en algún sitio de `src/`; una variable que se construye en tiempo de ejecución con un prefijo que no está en esos fuentes se podaría.

Lo que aparece después del primer pintado no debe empujar el contenido: reserva su sitio con un esqueleto de la altura real (`CargandoBloque`) o ponlo donde no desplace nada, como el botón «Instalar la aplicación» de la cabecera.

## Prohibido

La regla de ESLint `ui/solo-tokens` (`frontend/eslint/solo-tokens.js`) falla en todo fichero de `src/` que no esté marcado como legado si encuentra:

- colores de la paleta de Tailwind: `bg-red-500`, `text-white`, `border-gray-200`, `text-black`…;
- los tokens antiguos de la app: `bg-paper`, `bg-surface`, `text-ink`, `text-ink-soft`, `text-muted`, `border-line`, `bg-primary`, `text-primary-dark`, `bg-navy`, `*-grounding-*`, `*-risk-*`;
- colores escritos a mano: `bg-[#fff]`, `text-[rgb(…)]`, `accent-[rgb(var(--primary))]`;
- `shadow-card`, `shadow-pop`, `font-serif` y tamaños de letra arbitrarios (`text-[13.5px]`);
- importar `lucide-react` (los iconos son Octicons).

Tampoco se escriben `style={{ color: … }}` ni CSS con colores literales: un CSS Module nuevo usa variables de Primer (`var(--fgColor-muted)`, `var(--base-size-16)`, `var(--borderRadius-medium)`).

Un fichero sin migrar lleva en la primera línea `/* ui-legado: pendiente de migrar a src/ui (docs/ui/sistema-de-diseno.md) */` y la regla lo salta. Migrarlo consiste en borrar esa línea y corregir lo que la regla señale después.

## Cómo migrar un panel

El ejemplo de referencia es el panel de avisos: `src/components/NotificationsPanel.tsx`, `src/paneles/avisos.tsx` y su entrada en `registro.ts`.

1. En `src/paneles/registro.ts`, añade a la entrada del panel `presentacion` (`"pagina"` o `"dialogo"`) y, si quieres subtítulo, `descripcion`. Toca solo las líneas de tu panel: otras sesiones editan el mismo fichero.
2. En el componente, quita el marco que dibujaba él mismo: el `if (!open) return null`, el fondo oscuro con `fixed inset-0`, el `<aside role="dialog">`, la cabecera con título y botón de cerrar y el `useEffect` que escuchaba Escape. El componente devuelve directamente su contenido. Las props `open` y `onClose` dejan de hacer falta salvo que el panel las use para otra cosa (por ejemplo, cargar datos al abrirse).
3. Borra la línea `/* ui-legado */` y ejecuta `nice -n 15 npx eslint src/components/TuPanel.tsx`. Cada aviso dice qué clase sobra.
4. Sustituye el marcado a mano por componentes de `src/ui` según la tabla anterior. En el panel de avisos: los grupos son `Caja` con `CounterLabel` en la cabecera; el error, un `Flash`; los dos estados vacíos, `Blankslate`; la carga, `CargandoLista`; «Urgente» y «Nuevo», `Label`; los importes y plazos, `Label` de `success` y `attention`; los botones, `Button size="small"`.
5. Si el panel abría otro panel anidado dentro de sí, cámbialo por `ctx.abrir("<id>")`. Avisos lo hacía con las preferencias y ahora llama a `ctx.abrir("preferencias")`.
6. Pruebas: añade o adapta un `*.test.tsx` con Testing Library que busque por rol y nombre accesible (`getByRole("region", { name: … })`, `getByRole("button", { name: … })`). `NotificationsPanel.test.tsx` sirve de modelo.
7. Comprueba, de uno en uno y con `nice -n 15`: `npx tsc -b`, `npx eslint .`, `npx vitest run` y, al final, `npm run build`.

Antes y después, en código:

```tsx
// Antes
<p role="alert" className="rounded-lg border border-risk-high-fg/30 bg-risk-high-bg px-3 py-2 text-[13px] text-ink-soft">{error}</p>
<span className="rounded-pill bg-primary px-2 py-px text-white">Nuevo</span>

// Después
<Flash variant="danger">{error}</Flash>
<Label variant="accent">Nuevo</Label>
```

## Accesibilidad

- Las hojas laterales (`HojaLateral`) son `role="dialog"` con `aria-modal`, nombre (el título) y descripción (el subtítulo). Atrapan el foco mientras están abiertas, se cierran con Escape y, al cerrarse, el shell devuelve el foco a lo que las abrió. La prueba `PanelHost.test.tsx` lo cubre.
- Una página que se abre recibe el foco en su sección, nombrada con su título.
- Los paneles heredados conservan su trampa de foco (`FocoEnDialogo` en `PanelHost`).
- Hay enlace para saltar al contenido, un único `h1` (las migas de la cabecera) y `h2` y `h3` por debajo.
- Los iconos decorativos no llevan etiqueta (los Octicons son `aria-hidden` por defecto); todo `IconButton` lleva `aria-label`.
- No uses el color como único indicador: «Urgente» es una etiqueta con texto, no solo un borde rojo.

## Paneles pendientes de migrar

Cuatro grupos, sin ficheros en común, para repartirlos entre sesiones paralelas. Cada uno incluye también su `src/paneles/<id>.tsx`. Todos tocan su propia entrada de `registro.ts` y nada más de ese fichero.

1. Conversación y consejo, unas 2.600 líneas: `ActivityPanel`, `TrustStrip`, `ProcedenciaRespuesta`, `DoctrineNotice`, `VerificationPanel`, `SourceCard`, `ObligationCard`, `EvaluacionFeedback`, `ConvocarConsejo`, `DictamenConsejo`, `RepasoCard`, `MemoriaBotones` y `paneles/consejo.tsx`. Son las piezas que se ven dentro de los comentarios del hilo y dentro de los avisos.
2. Cálculos fiscales, unas 2.900 líneas: `SimulatorPanel`, `simulador/EditorDescendientes`, `ObligacionDeclararCard`, `OptimizerPanel`, `optimizador/*`, `AutonomosPanel`, `PlanificadorPanel`, `lib/optimizador/textos.ts` y `lib/planificador.ts` (los dos últimos guardan clases de color en cadenas).
3. Hogar y datos personales, unas 2.800 líneas: `ModulosPanel`, `CorreoSeccion`, `CapacidadesSeccion`, `CasePanel`, `CaseExtras`, `PreferenciasPanel`, `RecuerdosPanel` y `AvisosMovilPanel`.
4. Herramientas y cuenta, unas 2.550 líneas: `TareasPanel`, `InvestigarPanel`, `NavegadorPanel`, `RevisionCasosPanel`, `AuthScreen`, `AuthPrivacyNotice` y `PrivacyPanel` (este último lo abre `App.tsx` fuera del registro, con su propia capa; al migrarlo puede pasar a usar `HojaLateral` directamente).

`MemoriaBotones` (grupo 1) también lo usa `optimizador/PalancaCard` (grupo 2), pero solo como consumidor: el grupo 2 no necesita editarlo.
