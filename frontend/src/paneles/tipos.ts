// The contract between the shell and its panels (see registro.ts).
//
// The shell owns the conversation, the case and the data several panels share (notices, modules, pending
// actions); a panel module receives all of it as `ctx` and maps it onto its component's props. A new panel
// therefore needs no change in the shell: one component file plus one entry in registro.ts.

import type { Icon } from "@primer/octicons-react";
import type { ComponentType } from "react";

import type { AdvisorNotification, CaseState, Fact, ObligationCandidate } from "../api";
import type { AccionPendiente, ModulosResponse } from "../apiModulos";
import type { SimulatorSeed } from "../components/SimulatorPanel";

export type ValorDato = string | number | boolean | null;

export interface ContextoPanel {
  /** The conversation on screen: panels keyed by it never leak figures into another one. */
  conversacionId: string;
  caso: {
    id: string | null;
    facts: Fact[];
    taxPeriod: string | null;
    obligations: ObligationCandidate[];
  };
  /** The advisor is answering: panels that write to the case wait. */
  ocupado: boolean;
  /** Change a fact of the case (null deletes it); resolves to whether it was saved. */
  cambiarDato: (nombre: string, valor: ValorDato) => Promise<boolean>;
  guardandoDato: string | null;
  errorCaso: string | null;
  /** The facts that belong to the areas' modules (shown in «Áreas», not in «Tus datos»). */
  datosDeModulos: Set<string>;
  /** Put a case the server returned on screen. */
  aplicarCaso: (caso: CaseState) => void;
  /** Re-read the case from the server (after a panel changed it there). */
  recargarCaso: () => void;
  modulos: {
    datos: ModulosResponse | null;
    error: string | null;
    cambiar: (clave: string, activo: boolean, borrar: boolean) => Promise<boolean>;
    nombres: Record<string, string>;
    /** Read the modules and the actions again (after an error). */
    recargar: () => void;
  };
  acciones: {
    items: AccionPendiente[];
    decidir: (id: string, confirmar: boolean) => Promise<void>;
    recargar: () => void;
  };
  avisos: {
    items: AdvisorNotification[];
    cargando: boolean;
    error: string | null;
    marcarLeido: (id: string) => void;
    recargar: () => void;
  };
  /** Send a question to the advisor (closes the panel first). */
  preguntar: (texto: string) => void;
  /** Open another panel (closing this one). */
  abrir: (id: string) => void;
  /** Figures the optimiser handed to the simulator for this conversation (null: none). */
  semillaSimulador: { id: number; seed: SimulatorSeed } | null;
  usarEnSimulador: (seed: SimulatorSeed) => void;
}

export interface PropsPanel {
  ctx: ContextoPanel;
  open: boolean;
  onClose: () => void;
}

/** Counters shown on the navigation (a badge). */
export interface Insignias {
  avisosSinLeer: number;
  accionesPendientes: number;
  datosEnExpediente: number;
}

/** What decides whether a panel is offered. */
export interface Visibilidad {
  /** Keys of the modules switched on, or null while unknown (an older backend, or still loading). */
  modulosActivos: Set<string> | null;
  /** Feature flags: what the server offers (`navegador`, `push`) and what the build hides. */
  flags: Record<string, boolean>;
  /** The backend has user accounts and somebody is signed in. */
  conCuenta: boolean;
}

/** Sections of the «Más» menu, in this order: the areas of life (src/lib/areas.ts), then the user's own data. */
export type GrupoPanel = "dinero" | "impuestos" | "decidir" | "datos";

export const GRUPOS: { id: GrupoPanel; nombre: string }[] = [
  { id: "dinero", nombre: "Dinero y casa" },
  { id: "impuestos", nombre: "Impuestos y trámites" },
  { id: "decidir", nombre: "Investigar y decidir" },
  { id: "datos", nombre: "Tú y tus datos" },
];

export interface DefinicionPanel {
  /** Stable id: the value of `?panel=` (lowercase, digits and hyphens). */
  id: string;
  /** Short label for the navigation. */
  etiqueta: string;
  /**
   * The title of its page or sheet and the tab's tooltip: short (≤ 50 characters) and starting with the
   * label, so what a voice-control user says («Simulador») is the start of what they see (WCAG 2.5.3).
   */
  titulo: string;
  /** An Octicon (src/ui/iconos.ts). */
  icono: Icon;
  /**
   * How the shell shows it (docs/ui/sistema-de-diseno.md, «Página o diálogo»):
   *   "pagina"  — in the main column, instead of the conversation, under its tab; for work that takes a while.
   *   "dialogo" — a side sheet over the conversation (HojaLateral); for a quick look or a quick change.
   *   absent    — a panel not yet migrated: it draws its own overlay, as before.
   * A migrated panel's component renders only its content: the shell puts the title, icon and frame.
   */
  presentacion?: "pagina" | "dialogo";
  /** Subtitle of the sheet or description of the page (migrated panels). */
  descripcion?: string;
  /** Width of the side sheet ("dialogo"): 480px by default. */
  ancho?: "medium" | "large" | "xlarge";
  /** Section of the «Más» menu. */
  grupo: GrupoPanel;
  /** Order in the navigation (lower first). */
  orden: number;
  /** Always in the top bar, at every width (the rest go to «Más» when space runs out). */
  destacado?: boolean;
  /** Offered only while this module is switched on (offered while that is unknown). */
  modulo?: string;
  /** Offered only while this feature flag is on. */
  flag?: string;
  /** Offered only with accounts. */
  requiereCuenta?: boolean;
  /** A counter on the tab; `etiqueta` is what a screen reader hears after the label («3 sin leer»). */
  insignia?: (insignias: Insignias) => { valor: number; etiqueta: string } | null;
  /** The panel module (lazy: its code is downloaded the first time it opens). */
  cargar: () => Promise<{ default: ComponentType<PropsPanel> }>;
}
