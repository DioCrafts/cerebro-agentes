// The registry of the shell's panels: what the navigation offers, when, and where its code lives.
//
// Adding a panel (any agent or contributor):
//   1. Write `src/paneles/<id>.tsx` with a default export `(props: PropsPanel) => JSX` that maps `ctx`
//      (see tipos.ts) onto your component.
//   2. Add ONE entry below, with `presentacion` ("pagina" or "dialogo": see tipos.ts and
//      docs/ui/sistema-de-diseno.md) and an Octicon from src/ui/iconos.ts. Nothing else changes: the top bar / «Más» menu, the deep link `/?panel=<id>`,
//      the lazy loading and the focus handling come from the shell.
//
// Visibility: `modulo` (the module must be on in GET /modulos — while that is unknown the panel is offered,
// as before the modules existed), `flag` (a feature flag: what the server offers, or a build-time switch),
// `requiereCuenta` (accounts on and signed in). `VITE_PANELES_OCULTOS=id1,id2` hides panels in a build.

import {
  AppsIcon,
  BellIcon,
  BookmarkIcon,
  BriefcaseIcon,
  ChecklistIcon,
  DeviceMobileIcon,
  FileDirectoryIcon,
  GlobeIcon,
  GraphIcon,
  NumberIcon,
  PeopleIcon,
  ProjectRoadmapIcon,
  SlidersIcon,
  TasklistIcon,
  TelescopeIcon,
} from "../ui/iconos";

import type { DefinicionPanel, Visibilidad } from "./tipos";

export const REGISTRO: DefinicionPanel[] = [
  {
    id: "avisos",
    grupo: "datos",
    etiqueta: "Avisos",
    titulo: "Avisos",
    icono: BellIcon,
    presentacion: "dialogo",
    descripcion: "Plazos, vencimientos, cambios que te afectan y avisos de tus áreas",
    orden: 10,
    destacado: true,
    insignia: (i) =>
      i.avisosSinLeer > 0 ? { valor: i.avisosSinLeer, etiqueta: `${i.avisosSinLeer} sin leer` } : null,
    cargar: () => import("./avisos"),
  },
  {
    id: "modulos",
    grupo: "datos",
    etiqueta: "Áreas",
    titulo: "Áreas que llevo por ti",
    icono: AppsIcon,
    presentacion: "pagina",
    descripcion: "Activa las áreas que quieres que lleve, rellena sus datos y confirma lo que preparan",
    orden: 20,
    destacado: true,
    insignia: (i) =>
      i.accionesPendientes > 0
        ? {
            valor: i.accionesPendientes,
            etiqueta: `${i.accionesPendientes} pendientes de tu confirmación`,
          }
        : null,
    cargar: () => import("./modulos"),
  },
  {
    id: "simulador",
    grupo: "impuestos",
    etiqueta: "Simulador",
    titulo: "Simulador de IRPF",
    icono: NumberIcon,
    presentacion: "pagina",
    descripcion: "Cambia una cifra y mira cómo cambia tu declaración",
    orden: 40,
    modulo: "fiscal",
    cargar: () => import("./simulador"),
  },
  {
    id: "autonomos",
    grupo: "impuestos",
    etiqueta: "Autónomos",
    titulo: "Autónomos: tu beneficio y tus pagos",
    icono: BriefcaseIcon,
    presentacion: "pagina",
    descripcion: "Tu beneficio del año y lo que toca pagar cada trimestre (modelos 130 y 303)",
    orden: 45,
    modulo: "fiscal",
    cargar: () => import("./autonomos"),
  },
  {
    id: "optimizador",
    grupo: "impuestos",
    etiqueta: "Optimizar",
    titulo: "Optimizar tus impuestos",
    icono: GraphIcon,
    presentacion: "pagina",
    descripcion: "La mejor combinación de lo que la ley te deja elegir antes de fin de año",
    orden: 50,
    modulo: "fiscal",
    cargar: () => import("./optimizador"),
  },
  {
    id: "planificar",
    grupo: "dinero",
    etiqueta: "Planificar",
    titulo: "Planificar tu dinero",
    icono: ProjectRoadmapIcon,
    presentacion: "pagina",
    descripcion: "¿Amortizar, aportar al plan, donar o guardar? El mejor reparto de tu dinero",
    orden: 30,
    cargar: () => import("./planificar"),
  },
  {
    id: "tareas",
    grupo: "decidir",
    etiqueta: "Tareas",
    titulo: "Tareas de varios pasos",
    icono: TasklistIcon,
    presentacion: "pagina",
    descripcion: "Objetivos de varios pasos y días; nada se hace fuera de la app sin tu confirmación.",
    orden: 64,
    cargar: () => import("./tareas"),
  },
  {
    id: "consejo",
    grupo: "decidir",
    etiqueta: "Consejo",
    titulo: "Consejo para decisiones difíciles",
    icono: PeopleIcon,
    presentacion: "pagina",
    descripcion: "Decisiones difíciles estudiadas por varias IA, por separado y de forma anónima",
    orden: 62,
    cargar: () => import("./consejo"),
  },
  {
    id: "investigar",
    grupo: "decidir",
    etiqueta: "Investigar",
    titulo: "Investigar con fuentes",
    icono: TelescopeIcon,
    presentacion: "pagina",
    descripcion: "Respuestas con fuentes: cada afirmación lleva su cita literal comprobada.",
    orden: 60,
    cargar: () => import("./investigar"),
  },
  {
    id: "navegador",
    grupo: "decidir",
    etiqueta: "Navegador",
    titulo: "Navegador: actúa por ti en webs oficiales",
    icono: GlobeIcon,
    presentacion: "pagina",
    descripcion: "Actúa por ti en webs oficiales, con tu sesión y bajo tu control.",
    orden: 66,
    flag: "navegador",
    cargar: () => import("./navegador"),
  },
  {
    id: "preferencias",
    grupo: "datos",
    etiqueta: "Mis preferencias",
    titulo: "Mis preferencias",
    icono: SlidersIcon,
    presentacion: "dialogo",
    descripcion: "Lo que me has dicho que no quieres, no puedes, prefieres o ya hiciste",
    ancho: "large",
    orden: 80,
    cargar: () => import("./preferencias"),
  },
  {
    id: "revision",
    grupo: "datos",
    etiqueta: "Revisión",
    titulo: "Revisión de errores",
    icono: ChecklistIcon,
    presentacion: "pagina",
    descripcion: "Lo que el asistente hizo mal, para que no vuelva a pasar.",
    orden: 85,
    cargar: () => import("./revision"),
  },
  {
    id: "recuerdos",
    grupo: "datos",
    etiqueta: "Recuerdos",
    titulo: "Recuerdos de nuestras conversaciones",
    icono: BookmarkIcon,
    presentacion: "dialogo",
    descripcion: "Lo que recuerdo de nuestras conversaciones, con la fecha en que me lo dijiste: puedes verlo, corregirlo o borrarlo",
    ancho: "large",
    orden: 82,
    cargar: () => import("./recuerdos"),
  },
  {
    id: "movil",
    grupo: "datos",
    etiqueta: "Avisos en el móvil",
    titulo: "Avisos en el móvil",
    icono: DeviceMobileIcon,
    presentacion: "dialogo",
    descripcion: "Te avisamos en este dispositivo cuando haya algo urgente",
    orden: 90,
    flag: "pwa",
    cargar: () => import("./movil"),
  },
  {
    id: "expediente",
    grupo: "datos",
    etiqueta: "Tus datos",
    titulo: "Tus datos",
    icono: FileDirectoryIcon,
    presentacion: "pagina",
    descripcion: "Lo que sé de ti en esta conversación. Corrige o borra cualquier dato: las respuestas dependen de ellos",
    orden: 25,
    destacado: true,
    insignia: (i) =>
      i.datosEnExpediente > 0 ? { valor: i.datosEnExpediente, etiqueta: `${i.datosEnExpediente} datos guardados` } : null,
    cargar: () => import("./expediente"),
  },
];

const precargados = new Set<string>();

/**
 * Downloads a panel's chunk before it opens (pointer over or focus on its tab), so the click finds it ready.
 * The module system keeps the promise: PanelHost's React.lazy then resolves at once. A failed download is
 * forgotten, so the next hover or the opening itself tries again.
 */
export function precargarPanel(panel: DefinicionPanel): void {
  if (precargados.has(panel.id)) return;
  precargados.add(panel.id);
  panel.cargar().catch(() => precargados.delete(panel.id));
}

function ocultosEnLaBuild(): Set<string> {
  const texto = (import.meta.env.VITE_PANELES_OCULTOS as string | undefined) ?? "";
  return new Set(
    texto
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  );
}

const OCULTOS = ocultosEnLaBuild();

export function esVisible(panel: DefinicionPanel, v: Visibilidad): boolean {
  if (OCULTOS.has(panel.id)) return false;
  if (panel.requiereCuenta && !v.conCuenta) return false;
  if (panel.flag && !v.flags[panel.flag]) return false;
  if (panel.modulo && v.modulosActivos !== null && !v.modulosActivos.has(panel.modulo)) return false;
  return true;
}

/** The panels offered right now, in navigation order. */
export function panelesVisibles(v: Visibilidad, registro: DefinicionPanel[] = REGISTRO): DefinicionPanel[] {
  return registro.filter((p) => esVisible(p, v)).sort((a, b) => a.orden - b.orden);
}

/**
 * The modules switched on according to GET /modulos, or null when unknown. A module the backend does not list
 * is not "off": an older backend without it keeps every panel as before.
 */
export function modulosActivos(datos: { modulos: { clave: string; activo: boolean }[] } | null): Set<string> | null {
  if (datos === null) return null;
  const listados = new Set(datos.modulos.map((m) => m.clave));
  const activos = new Set(datos.modulos.filter((m) => m.activo).map((m) => m.clave));
  // Modules the registry needs but the backend does not know are treated as on.
  for (const panel of REGISTRO) if (panel.modulo && !listados.has(panel.modulo)) activos.add(panel.modulo);
  return activos;
}
