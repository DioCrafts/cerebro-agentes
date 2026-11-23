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
  BellIcon,
  BookmarkIcon,
  BriefcaseIcon,
  ChecklistIcon,
  DeviceMobileIcon,
  FileDirectoryIcon,
  GlobeIcon,
  GraphIcon,
  HomeIcon,
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
    etiqueta: "Avisos",
    titulo: "Avisos de tu asesor",
    icono: BellIcon,
    presentacion: "dialogo",
    descripcion: "Plazos, plan de fin de año, cambios que te afectan y avisos de tus módulos",
    orden: 10,
    destacado: true,
    insignia: (i) =>
      i.avisosSinLeer > 0 ? { valor: i.avisosSinLeer, etiqueta: `Avisos: ${i.avisosSinLeer} sin leer` } : null,
    cargar: () => import("./avisos"),
  },
  {
    id: "modulos",
    etiqueta: "Módulos",
    titulo: "Módulos del hogar",
    icono: HomeIcon,
    orden: 20,
    destacado: true,
    insignia: (i) =>
      i.accionesPendientes > 0
        ? {
            valor: i.accionesPendientes,
            etiqueta: `Módulos: ${i.accionesPendientes} acciones pendientes de tu confirmación`,
          }
        : null,
    cargar: () => import("./modulos"),
  },
  {
    id: "simulador",
    etiqueta: "Simulador",
    titulo: "Abrir el simulador de IRPF",
    icono: NumberIcon,
    orden: 30,
    modulo: "fiscal",
    cargar: () => import("./simulador"),
  },
  {
    id: "autonomos",
    etiqueta: "Autónomos",
    titulo: "Abrir los cálculos para autónomos",
    icono: BriefcaseIcon,
    orden: 40,
    modulo: "fiscal",
    cargar: () => import("./autonomos"),
  },
  {
    id: "optimizador",
    etiqueta: "Optimizar",
    titulo: "Optimizar mis impuestos",
    icono: GraphIcon,
    orden: 50,
    modulo: "fiscal",
    cargar: () => import("./optimizador"),
  },
  {
    id: "planificar",
    etiqueta: "Planificar",
    titulo: "Planificar el dinero del hogar",
    icono: ProjectRoadmapIcon,
    orden: 60,
    cargar: () => import("./planificar"),
  },
  {
    id: "tareas",
    etiqueta: "Tareas",
    titulo: "Tareas de varios pasos: renovar documentos, preparar la renta, cambiar de tarifa…",
    icono: TasklistIcon,
    presentacion: "pagina",
    descripcion: "Objetivos de varios pasos y días; nada se hace fuera de la app sin tu confirmación.",
    orden: 65,
    cargar: () => import("./tareas"),
  },
  {
    id: "consejo",
    etiqueta: "Consejo",
    titulo: "Consejo del hogar: decisiones difíciles estudiadas por varias IA, por separado y de forma anónima",
    icono: PeopleIcon,
    orden: 66,
    cargar: () => import("./consejo"),
  },
  {
    id: "investigar",
    etiqueta: "Investigar",
    titulo: "Investigar una pregunta con fuentes (corpus legal, BOE y, si lo permites, la web)",
    icono: TelescopeIcon,
    presentacion: "pagina",
    descripcion: "Respuestas con fuentes: cada afirmación lleva su cita literal comprobada.",
    orden: 68,
    cargar: () => import("./investigar"),
  },
  {
    id: "navegador",
    etiqueta: "Navegador",
    titulo: "Agente con navegador: actúa por ti en webs oficiales",
    icono: GlobeIcon,
    presentacion: "pagina",
    descripcion: "Actúa por ti en webs oficiales, con tu sesión y bajo tu control.",
    orden: 70,
    flag: "navegador",
    cargar: () => import("./navegador"),
  },
  {
    id: "preferencias",
    etiqueta: "Preferencias",
    titulo: "Lo que sé de tus preferencias",
    icono: SlidersIcon,
    orden: 80,
    cargar: () => import("./preferencias"),
  },
  {
    id: "revision",
    etiqueta: "Revisión",
    titulo: "Revisión de casos: lo que el asistente hizo mal, para que no vuelva a pasar",
    icono: ChecklistIcon,
    presentacion: "pagina",
    descripcion: "Lo que el asistente hizo mal, para que no vuelva a pasar.",
    orden: 85,
    cargar: () => import("./revision"),
  },
  {
    id: "recuerdos",
    etiqueta: "Lo que sé de ti",
    titulo: "Lo que recuerdo de nuestras conversaciones, con tus palabras (verlo, corregirlo o borrarlo)",
    icono: BookmarkIcon,
    orden: 85,
    cargar: () => import("./recuerdos"),
  },
  {
    id: "movil",
    etiqueta: "Avisos en el móvil",
    titulo: "Recibir los avisos en este dispositivo",
    icono: DeviceMobileIcon,
    orden: 90,
    flag: "pwa",
    cargar: () => import("./movil"),
  },
  {
    id: "expediente",
    etiqueta: "Tu expediente",
    titulo: "Tu expediente",
    icono: FileDirectoryIcon,
    orden: 100,
    destacado: true,
    insignia: (i) =>
      i.datosEnExpediente > 0 ? { valor: i.datosEnExpediente, etiqueta: `${i.datosEnExpediente} datos` } : null,
    cargar: () => import("./expediente"),
  },
];

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
