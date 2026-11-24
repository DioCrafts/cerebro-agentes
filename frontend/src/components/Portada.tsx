// The home page of an empty conversation, laid out like GitHub's dashboard: a greeting, what is waiting for the
// user (notices, actions to confirm), the areas of life Cerebro looks after (src/lib/areas.ts) as cards with
// example requests and links to their tools, and how it earns trust. The question box under it is the single
// way in: the brain picks the specialist, so the user never has to choose an area before asking.

import type { ReactNode } from "react";

import type { ModuloInfo } from "../apiModulos";
import type { Area } from "../lib/areas";
import { AREAS, estadoArea } from "../lib/areas";
import type { DefinicionPanel } from "../paneles/tipos";
import { Button, Caja, Label } from "../ui";
import { AppsIcon, ArrowRightIcon, BellIcon, CheckCircleIcon, ShieldCheckIcon, PersonIcon, QuoteIcon } from "../ui/iconos";
import { Monogram } from "./Brand";

const CONFIANZA = [
  {
    icono: QuoteIcon,
    titulo: "Cita sus fuentes",
    texto: "Cada afirmación lleva su fuente comprobada y cada cifra sale de un cálculo que se puede repetir.",
  },
  {
    icono: ShieldCheckIcon,
    titulo: "No actúa sin ti",
    texto: "Si hay que enviar un correo o dar de baja algo, lo dejo preparado y espera a que lo confirmes.",
  },
  {
    icono: PersonIcon,
    titulo: "Recuerda lo que le cuentas",
    texto: "Lo que sabe de ti lo puedes corregir o borrar en «Tus datos» y en «Recuerdos».",
  },
] as const;

export interface PortadaProps {
  /** Send a request to the conversation. */
  onPick: (pregunta: string) => void;
  /** Open a panel of the registry. */
  onAbrir: (id: string) => void;
  /** The panels offered now (a tool link shows only when its panel is offered). */
  paneles: DefinicionPanel[];
  /** GET /modulos, or null while unknown. */
  modulos: ModuloInfo[] | null;
  avisosSinLeer: number;
  accionesPendientes: number;
}

export function Portada({ onPick, onAbrir, paneles, modulos, avisosSinLeer, accionesPendientes }: PortadaProps) {
  const hayAvisos = paneles.some((p) => p.id === "avisos");
  const hayAreas = paneles.some((p) => p.id === "modulos");
  const pendientes = (hayAvisos && avisosSinLeer > 0) || accionesPendientes > 0;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-start gap-4 pt-2">
        <Monogram size={48} className="shrink-0" />
        <div className="min-w-0">
          <h2 className="m-0 text-xl font-semibold">¿En qué te ayudo hoy?</h2>
          <p className="m-0 mt-1 text-fg-muted">
            Llevo tus papeles, tus gastos y tu casa, investigo lo que necesites con fuentes y te aviso antes de que algo venza.
            Escríbemelo abajo con tus palabras.
          </p>
        </div>
      </header>

      {pendientes && (
        <Caja as="section" variante="atencion" aria-labelledby="titulo-pendiente">
          <Caja.Cabecera titulo={<span id="titulo-pendiente">Pendiente para ti</span>} nivel={3} />
          <Caja.Lista>
            {hayAvisos && avisosSinLeer > 0 && (
              <FilaPendiente
                icono={<BellIcon />}
                texto={avisosSinLeer === 1 ? "Tienes 1 aviso sin leer" : `Tienes ${avisosSinLeer} avisos sin leer`}
                boton="Ver avisos"
                onClick={() => onAbrir("avisos")}
              />
            )}
            {accionesPendientes > 0 && (
              <FilaPendiente
                icono={<CheckCircleIcon />}
                texto={
                  accionesPendientes === 1
                    ? "1 acción espera tu confirmación"
                    : `${accionesPendientes} acciones esperan tu confirmación`
                }
                boton="Revisar"
                onClick={() => onAbrir("modulos")}
              />
            )}
          </Caja.Lista>
        </Caja>
      )}

      <section aria-labelledby="titulo-areas" className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <h3 id="titulo-areas" className="m-0 text-base font-semibold">
            Tus áreas
          </h3>
          {hayAreas && (
            <Button size="small" variant="invisible" leadingVisual={AppsIcon} onClick={() => onAbrir("modulos")}>
              Gestionar áreas
            </Button>
          )}
        </div>
        <ul className="m-0 grid list-none gap-4 p-0 sm:grid-cols-2">
          {AREAS.map((area) => (
            <li key={area.id} className="flex">
              <TarjetaArea
                area={area}
                estado={estadoArea(area, modulos)}
                paneles={paneles}
                hayAreas={hayAreas}
                onPick={onPick}
                onAbrir={onAbrir}
              />
            </li>
          ))}
        </ul>
      </section>

      <ul className="m-0 grid list-none gap-4 p-0 sm:grid-cols-3">
        {CONFIANZA.map(({ icono: Icono, titulo, texto }) => (
          <li key={titulo} className="flex gap-3">
            <Icono className="mt-0.5 shrink-0 text-fg-muted" />
            <div>
              <p className="m-0 font-semibold">{titulo}</p>
              <p className="m-0 text-xs text-fg-muted">{texto}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function FilaPendiente({ icono, texto, boton, onClick }: { icono: ReactNode; texto: string; boton: string; onClick: () => void }) {
  return (
    <Caja.Fila className="flex items-center gap-3">
      <span className="shrink-0 text-fg-attention">{icono}</span>
      <span className="min-w-0 flex-1">{texto}</span>
      <Button size="small" onClick={onClick}>
        {boton}
      </Button>
    </Caja.Fila>
  );
}

interface TarjetaAreaProps {
  area: Area;
  estado: ReturnType<typeof estadoArea>;
  paneles: DefinicionPanel[];
  hayAreas: boolean;
  onPick: (pregunta: string) => void;
  onAbrir: (id: string) => void;
}

function TarjetaArea({ area, estado, paneles, hayAreas, onPick, onAbrir }: TarjetaAreaProps) {
  const { icono: Icono } = area;
  const herramientas = (area.paneles ?? [])
    .map((id) => paneles.find((p) => p.id === id))
    .filter((p): p is DefinicionPanel => p !== undefined);
  const idTitulo = `area-${area.id}`;
  const lista = estado === "lista";

  return (
    <Caja as="article" aria-labelledby={idTitulo} className="flex w-full flex-col">
      <Caja.Cabecera
        nivel={4}
        titulo={
          <span id={idTitulo} className="flex items-center gap-2">
            <Icono className="shrink-0 text-fg-muted" />
            {area.nombre}
          </span>
        }
        acciones={
          estado === "proximamente" ? (
            <Label variant="secondary">Próximamente</Label>
          ) : estado === "apagada" ? (
            <Label variant="attention">Desactivada</Label>
          ) : undefined
        }
      />
      <Caja.Cuerpo className={estado === "proximamente" ? "text-fg-muted" : undefined}>{area.descripcion}</Caja.Cuerpo>
      {lista && area.ejemplos && area.ejemplos.length > 0 && (
        <Caja.Lista aria-label={`Ejemplos de ${area.nombre}`}>
          {area.ejemplos.map((e) => (
            <Caja.Fila key={e.titulo} className="p-0!">
              <button
                type="button"
                onClick={() => onPick(e.pregunta)}
                className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-canvas-muted"
              >
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold text-fg-accent">{e.titulo}</span>
                  <span className="block text-xs text-fg-muted">{e.pregunta}</span>
                </span>
                <ArrowRightIcon className="mt-1 shrink-0 text-fg-muted" />
              </button>
            </Caja.Fila>
          ))}
        </Caja.Lista>
      )}
      {lista && herramientas.length > 0 && (
        <Caja.Pie className="mt-auto flex flex-wrap gap-2">
          {herramientas.map((p) => (
            <Button key={p.id} size="small" leadingVisual={p.icono} onClick={() => onAbrir(p.id)}>
              {p.etiqueta}
            </Button>
          ))}
        </Caja.Pie>
      )}
      {estado === "apagada" && hayAreas && (
        <Caja.Pie className="mt-auto">
          <Button size="small" leadingVisual={AppsIcon} onClick={() => onAbrir("modulos")}>
            Activar en Áreas
          </Button>
        </Caja.Pie>
      )}
    </Caja>
  );
}
