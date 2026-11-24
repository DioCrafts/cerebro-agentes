// The areas of life Cerebro looks after, as the home page and the sidebar show them. Presentation only: what an
// area can do lives in the backend (its modules in GET /modulos) and in the panels of the registry
// (src/paneles/registro.ts). An area groups both and gives the user a way in: a sentence, example requests that
// go to the conversation, and links to its tools.
//
// Adding an area, or bringing one out of «Próximamente»: one entry here. An area with `modulos` is ready when any
// of them is listed and on in GET /modulos; one listed but off sends the user to «Áreas» to switch it on. An
// area marked `proximamente` has no backend yet and offers no examples, so nothing promises an answer that does
// not exist.

import type { Icon } from "../ui/iconos";
import { CreditCardIcon, HeartIcon, HomeIcon, KeyIcon, LawIcon, TelescopeIcon } from "../ui/iconos";

export interface EjemploArea {
  /** Short label of the suggestion. */
  titulo: string;
  /** What is sent to the conversation when it is chosen. */
  pregunta: string;
}

export interface Area {
  id: string;
  nombre: string;
  icono: Icon;
  /** One sentence: what Cerebro does for you here. */
  descripcion: string;
  /** Backend modules (GET /modulos `clave`) behind the area. None: it rests on the brain's own tools. */
  modulos?: string[];
  /** Panels of the registry that belong to the area, linked from its card when they are offered. */
  paneles?: string[];
  ejemplos?: EjemploArea[];
  /** No backend yet: shown as «Próximamente». */
  proximamente?: boolean;
}

export const AREAS: Area[] = [
  {
    id: "dinero",
    nombre: "Dinero y gastos",
    icono: CreditCardIcon,
    descripcion: "Leo los extractos de tu banco, encuentro las suscripciones que sigues pagando y te digo dónde rinde más tu ahorro.",
    modulos: ["suscripciones"],
    paneles: ["planificar"],
    ejemplos: [
      {
        titulo: "¿Qué suscripciones pago?",
        pregunta: "Revisa los movimientos de mi banco y dime qué suscripciones estoy pagando y cuáles podría dar de baja.",
      },
      {
        titulo: "¿Amortizo o invierto?",
        pregunta: "Tengo 10.000 € ahorrados. ¿Me conviene más amortizar hipoteca, aportar al plan de pensiones o guardarlos?",
      },
    ],
  },
  {
    id: "casa",
    nombre: "Casa y suministros",
    icono: HomeIcon,
    descripcion: "La factura de la luz, la hipoteca y los papeles que caducan: DNI, ITV, garantías y contratos.",
    modulos: ["luz", "hipoteca", "caducidades"],
    ejemplos: [
      {
        titulo: "¿Pago de más en la luz?",
        pregunta: "Mira mi última factura de la luz y dime si hay una tarifa más barata para mi consumo.",
      },
      {
        titulo: "Qué me caduca pronto",
        pregunta: "¿Qué documentos, garantías o contratos me caducan en los próximos tres meses?",
      },
    ],
  },
  {
    id: "impuestos",
    nombre: "Impuestos y trámites",
    icono: LawIcon,
    descripcion: "Renta, IVA y modelos de autónomo con la norma citada, y las ayudas públicas que puedes pedir.",
    modulos: ["fiscal", "ayudas"],
    paneles: ["simulador", "autonomos", "optimizador"],
    ejemplos: [
      {
        titulo: "Vender la vivienda",
        pregunta: "He vendido mi vivienda habitual y quiero comprar otra. ¿Qué plazo tengo para reinvertir y no tributar?",
      },
      {
        titulo: "Ayudas que puedo pedir",
        pregunta: "Tenemos dos hijos y vivimos de alquiler. ¿Hay alguna ayuda pública que podamos pedir este año?",
      },
    ],
  },
  {
    id: "decidir",
    nombre: "Investigar y decidir",
    icono: TelescopeIcon,
    descripcion: "Investigo a fondo con cada afirmación citada, llevo encargos de varios días y reúno a varias IA para las decisiones difíciles.",
    paneles: ["investigar", "consejo", "tareas", "navegador"],
    ejemplos: [
      {
        titulo: "Investiga a fondo",
        pregunta: "Investiga qué coche eléctrico de segunda mano me conviene por menos de 20.000 € y cita las fuentes.",
      },
      {
        titulo: "¿Alquilar o comprar?",
        pregunta: "Con 40.000 € ahorrados y 2.800 € netos al mes, ¿me conviene más comprar piso o seguir de alquiler?",
      },
    ],
  },
  {
    id: "salud",
    nombre: "Salud",
    icono: HeartIcon,
    descripcion: "Tus citas médicas y tus analíticas. Todavía no lo llevo; mientras tanto puedo investigar una duda con fuentes.",
    proximamente: true,
  },
  {
    id: "vivienda",
    nombre: "Buscar vivienda",
    icono: KeyIcon,
    descripcion: "Comparar pisos con tus cifras y tus requisitos. Todavía no lo llevo; hoy puedes encargármelo como investigación.",
    proximamente: true,
  },
];

export type EstadoArea = "lista" | "apagada" | "proximamente";

/**
 * Whether an area can be used now. `modulos` is GET /modulos (null while unknown or with an older backend: the area
 * counts as ready, as the registry does with its panels). A module the backend does not list does not make the
 * area «apagada»: only one listed and switched off does, and only when none of the area's modules is on.
 */
export function estadoArea(area: Area, modulos: { clave: string; activo: boolean }[] | null): EstadoArea {
  if (area.proximamente) return "proximamente";
  if (!area.modulos || area.modulos.length === 0 || modulos === null) return "lista";
  const propios = modulos.filter((m) => area.modulos?.includes(m.clave));
  if (propios.length === 0) return "lista";
  return propios.some((m) => m.activo) ? "lista" : "apagada";
}
