// What the household brain did for one answer, in the user's terms:
//   · «Consulté a: Hipoteca · Impuestos» — which specialists answered (and which could not);
//   · «¿De dónde sale cada cifra?» — every figure of the answer, whose it is and the source it was read from
//     (the brain's figures gate attributed each one: a specialist's calculation, your own words, a cited source);
//   · the live plan while the turn runs («Consulto a Hipoteca e Impuestos en paralelo», each one's progress).
// Data: AgentResponse.cerebro (asistente/nucleo/cerebro/contrato.py -> InformeCerebro) and the brain's events.

import { Calculator, CheckCircle2, ChevronDown, CircleDashed, Loader2, MessageSquareQuote, Network, ScrollText, TriangleAlert } from "lucide-react";
import clsx from "clsx";

import type { AtribucionCifraCerebro, Citation, InformeCerebro, ProgressEvent, ResumenInformeCerebro } from "../api";
import type { ChatTurn } from "../lib/storage";

/** A chat turn as kept in this browser, with the brain's report when the brain answered it. */
export type TurnoConCerebro = ChatTurn & { cerebro?: InformeCerebro | null };

const ESTADO_NO_OK: Record<string, string> = {
  sin_respuesta: "sin respuesta",
  error: "no pudo responder",
  denegado: "no disponible",
  presupuesto: "sin respuesta (límite de uso)",
};

function nombres(informes: ResumenInformeCerebro[]): ResumenInformeCerebro[] {
  const vistos = new Set<string>();
  return informes.filter((i) => (vistos.has(i.especialista) ? false : (vistos.add(i.especialista), true)));
}

/** «Consulté a: Hipoteca · Impuestos», under the answer. Nothing when the brain answered alone. */
export function ConsultadosLinea({ cerebro }: { cerebro?: InformeCerebro | null }) {
  if (!cerebro || cerebro.informes.length === 0) return null;
  const unicos = nombres(cerebro.informes);
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12.5px] text-muted">
      <Network size={13} aria-hidden className="text-primary-dark" />
      <span className="font-medium text-ink-soft">Consulté a:</span>
      {unicos.map((informe, i) => (
        <span key={informe.especialista} className="inline-flex items-center gap-1">
          {i > 0 && <span aria-hidden>·</span>}
          <span className={clsx(informe.estado !== "ok" && "text-risk-high-fg")}>
            {informe.nombre}
            {informe.estado !== "ok" && ` (${ESTADO_NO_OK[informe.estado] ?? informe.estado})`}
          </span>
        </span>
      ))}
      {cerebro.revisiones_sintesis > 0 && (
        <span className="ml-1 text-[12px]">
          · revisé la respuesta {cerebro.revisiones_sintesis === 1 ? "una vez" : `${cerebro.revisiones_sintesis} veces`} antes de enseñártela
        </span>
      )}
    </p>
  );
}

interface FilaProcedencia {
  literal: string;
  quien: string;
  icono: "calculo" | "usuario" | "fuente";
  concepto?: string;
  herramienta?: string;
  fragmentId?: string;
  fuente?: string;
}

function filas(cerebro: InformeCerebro, citations: Citation[]): FilaProcedencia[] {
  const atribuciones: AtribucionCifraCerebro[] = cerebro.puerta?.atribuciones ?? [];
  const porClave = new Map(cerebro.informes.map((i) => [i.especialista, i.nombre]));
  const cifras = new Map(cerebro.informes.flatMap((i) => i.cifras.map((c) => [c.id, c] as const)));
  const titulos = new Map(citations.filter((c) => c.fragment_id).map((c) => [c.fragment_id as string, c.title]));
  const vistos = new Set<string>();
  const salida: FilaProcedencia[] = [];
  for (const a of atribuciones) {
    const clave = `${a.literal}|${a.cifra_id ?? ""}|${a.fragment_id ?? ""}|${a.del_usuario}`;
    if (vistos.has(clave)) continue;
    vistos.add(clave);
    const cifra = a.cifra_id ? cifras.get(a.cifra_id) : undefined;
    if (a.del_usuario) {
      salida.push({ literal: a.literal, quien: "Tú lo dijiste", icono: "usuario" });
      continue;
    }
    const fragmentId = a.fragment_id ?? cifra?.fragment_id ?? undefined;
    const quien = a.especialista
      ? a.especialista === "cerebro"
        ? "Asistente (cálculo)"
        : (porClave.get(a.especialista) ?? a.especialista)
      : "Fuente citada";
    salida.push({
      literal: a.literal,
      quien,
      icono: a.cifra_id ? "calculo" : "fuente",
      concepto: cifra?.concepto,
      herramienta: cifra?.herramienta ?? undefined,
      fragmentId,
      fuente: fragmentId ? titulos.get(fragmentId) : undefined,
    });
  }
  return salida;
}

const ICONO = { calculo: Calculator, usuario: MessageSquareQuote, fuente: ScrollText } as const;

/** «¿De dónde sale cada cifra?»: figure → who → source, collapsed by default. */
export function ProcedenciaRespuesta({
  cerebro,
  citations,
  onCite,
}: {
  cerebro?: InformeCerebro | null;
  citations: Citation[];
  onCite?: (fragmentId: string) => void;
}) {
  if (!cerebro) return null;
  const lista = filas(cerebro, citations);
  if (lista.length === 0) return null;
  return (
    <details className="group mt-3 rounded-lg border border-line bg-surface">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-[13px] font-semibold text-ink-soft hover:text-ink [&::-webkit-details-marker]:hidden">
        <ChevronDown size={14} aria-hidden className="transition group-open:rotate-180" />
        ¿De dónde sale cada cifra?
        <span className="font-normal text-muted">({lista.length})</span>
      </summary>
      <ul className="divide-y divide-line border-t border-line">
        {lista.map((fila, i) => {
          const Icono = ICONO[fila.icono];
          return (
            <li key={`${fila.literal}-${i}`} className="flex flex-col gap-0.5 px-3 py-2 text-[13px] sm:flex-row sm:items-baseline sm:gap-3">
              <span className="font-mono text-[13px] font-semibold tabular-nums text-ink sm:w-36 sm:flex-shrink-0">{fila.literal}</span>
              <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-1.5 text-ink-soft">
                <Icono size={13} aria-hidden className="relative top-0.5 flex-shrink-0 text-primary-dark" />
                <span className="font-medium">{fila.quien}</span>
                {fila.concepto && <span className="text-muted">· {fila.concepto}</span>}
                {fila.herramienta && <span className="text-muted">· cálculo «{fila.herramienta}»</span>}
                {fila.fragmentId && (
                  <button
                    type="button"
                    onClick={() => onCite?.(fila.fragmentId as string)}
                    className="rounded px-1 font-mono text-[12px] text-primary-dark underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                    aria-label={`Ver la fuente ${fila.fragmentId}${fila.fuente ? `: ${fila.fuente}` : ""}`}
                  >
                    [{fila.fragmentId}]
                  </button>
                )}
                {fila.fuente && <span className="truncate text-[12px] text-muted">{fila.fuente}</span>}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="border-t border-line px-3 py-2 text-[12px] text-muted">
        Cada cifra se comprueba antes de enseñártela: sale de un cálculo de un especialista, de una fuente citada o de lo que
        tú me dijiste. Nunca sumo cifras de especialistas distintos si ninguna herramienta las ha combinado.
      </p>
    </details>
  );
}

type EstadoEncargo = "pendiente" | "en_curso" | "listo" | "fallo";

interface LineaPlan {
  id: string;
  clave: string;
  nombre: string;
  estado: EstadoEncargo;
  espera: string[];
}

function plan(eventos: ProgressEvent[]): { resumen: string; lineas: LineaPlan[]; fase: string | null } {
  const lineas = new Map<string, LineaPlan>();
  let resumen = "";
  let fase: string | null = null;
  for (const evento of eventos) {
    if (evento.type === "plan") {
      if (!evento.replan) resumen = evento.intencion.resumen;
      for (const e of evento.intencion.encargos) {
        if (!lineas.has(e.id)) lineas.set(e.id, { id: e.id, clave: e.especialista, nombre: e.especialista, estado: "pendiente", espera: e.depende_de });
      }
      if (evento.replan) fase = "Consulto otra vez a los especialistas…";
    } else if (evento.type === "especialista") {
      const linea = lineas.get(evento.encargo_id);
      if (!linea) continue;
      linea.nombre = evento.nombre || linea.nombre;
      if (evento.estado === "start") linea.estado = "en_curso";
      else if (evento.estado === "done") linea.estado = evento.resultado && evento.resultado !== "ok" ? "fallo" : "listo";
      else linea.estado = "fallo";
    } else if (evento.type === "sintesis") {
      fase = "Uno los informes en una sola respuesta…";
    } else if (evento.type === "puerta") {
      fase = evento.violaciones > 0 ? "Una cifra no cuadra: corrijo la respuesta…" : "Cifras comprobadas";
    }
  }
  return { resumen, lineas: [...lineas.values()], fase };
}

const ICONO_ESTADO = { pendiente: CircleDashed, en_curso: Loader2, listo: CheckCircle2, fallo: TriangleAlert } as const;
const TEXTO_ESTADO: Record<EstadoEncargo, string> = {
  pendiente: "en espera",
  en_curso: "consultando…",
  listo: "listo",
  fallo: "sin respuesta",
};

/** The live plan while the turn runs (nothing until the brain has one with specialists). */
export function PlanEnVivo({ eventos }: { eventos: ProgressEvent[] }) {
  const { resumen, lineas, fase } = plan(eventos);
  if (lineas.length === 0) return null;
  return (
    <div className="mb-3 rounded-xl border border-line bg-surface px-4 py-3 shadow-card" aria-live="polite">
      <p className="flex items-center gap-2 text-[13.5px] font-semibold text-ink">
        <Network size={15} aria-hidden className="text-primary-dark" />
        {resumen || "Consulto a los especialistas"}
      </p>
      <ul className="mt-2 flex flex-wrap gap-2">
        {lineas.map((linea) => {
          const Icono = ICONO_ESTADO[linea.estado];
          return (
            <li
              key={linea.id}
              className={clsx(
                "inline-flex items-center gap-1.5 rounded-pill border px-2.5 py-1 text-[12.5px]",
                linea.estado === "listo" && "border-grounding-cited-bd bg-grounding-cited-bg text-grounding-cited-fg",
                linea.estado === "fallo" && "border-risk-high-fg/30 bg-risk-high-bg text-risk-high-fg",
                (linea.estado === "pendiente" || linea.estado === "en_curso") && "border-line bg-paper text-ink-soft",
              )}
            >
              <Icono size={13} aria-hidden className={clsx(linea.estado === "en_curso" && "animate-spin")} />
              <span className="font-medium">{linea.nombre}</span>
              <span className="text-[11.5px] opacity-80">
                {TEXTO_ESTADO[linea.estado]}
                {linea.estado === "pendiente" && linea.espera.length > 0 && " (espera a otro informe)"}
              </span>
            </li>
          );
        })}
      </ul>
      {fase && <p className="mt-2 text-[12px] text-muted">{fase}</p>}
    </div>
  );
}
