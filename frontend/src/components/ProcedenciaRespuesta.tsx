// What the household brain did for one answer, in the user's terms:
//   · «Consulté a: Hipoteca · Impuestos» — which specialists answered (and which could not), as Labels;
//   · «¿De dónde sale cada cifra?» — every figure of the answer, whose it is and the source it was read from
//     (the brain's figures gate attributed each one: a specialist's calculation, your own words, a cited source),
//     in a folding Box;
//   · the live plan while the turn runs («Consulto a Hipoteca e Impuestos en paralelo», each one's progress as a
//     StateLabel).
// Data: AgentResponse.cerebro (asistente/nucleo/cerebro/contrato.py -> InformeCerebro) and the brain's events.

import type { AtribucionCifraCerebro, Citation, InformeCerebro, ProgressEvent, ResumenInformeCerebro } from "../api";
import type { ChatTurn } from "../lib/storage";
import { Button, Label, Spinner } from "../ui";
import type { Icon } from "../ui/iconos";
import { AlertIcon, CheckCircleIcon, CircleIcon, CommentIcon, FileIcon, NumberIcon, PeopleIcon } from "../ui/iconos";
import { DetallesCaja } from "./conversacion/DetallesCaja";
import { EtiquetaEstado, type TonoEstado } from "./conversacion/EtiquetaEstado";

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
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-fg-muted">
      <PeopleIcon size={12} />
      <span className="font-semibold">Consulté a:</span>
      <ul className="m-0 flex list-none flex-wrap gap-1 p-0" aria-label="Especialistas consultados">
        {unicos.map((informe) => (
          <li key={informe.especialista}>
            <Label variant={informe.estado === "ok" ? "secondary" : "danger"}>
              {informe.nombre}
              {informe.estado !== "ok" && ` (${ESTADO_NO_OK[informe.estado] ?? informe.estado})`}
            </Label>
          </li>
        ))}
      </ul>
      {cerebro.revisiones_sintesis > 0 && (
        <span>
          · revisé la respuesta {cerebro.revisiones_sintesis === 1 ? "una vez" : `${cerebro.revisiones_sintesis} veces`} antes de enseñártela
        </span>
      )}
    </div>
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

const ICONO = { calculo: NumberIcon, usuario: CommentIcon, fuente: FileIcon } as const;

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
    <DetallesCaja className="mt-3" resumen="¿De dónde sale cada cifra?" contador={lista.length}>
      <ul className="m-0 list-none divide-y divide-border-muted p-0">
        {lista.map((fila, i) => {
          const Icono = ICONO[fila.icono];
          return (
            <li key={`${fila.literal}-${i}`} className="flex flex-col gap-1 px-3 py-2 sm:flex-row sm:items-baseline sm:gap-3">
              <span className="font-mono font-semibold tabular-nums text-fg-default sm:w-36 sm:shrink-0">{fila.literal}</span>
              <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
                <Icono size={12} className="shrink-0 text-fg-muted" />
                <span className="font-semibold">{fila.quien}</span>
                {fila.concepto && <span className="text-fg-muted">· {fila.concepto}</span>}
                {fila.herramienta && <span className="text-fg-muted">· cálculo «{fila.herramienta}»</span>}
                {fila.fragmentId &&
                  (onCite ? (
                    <Button
                      size="small"
                      variant="invisible"
                      className="font-mono"
                      onClick={() => onCite(fila.fragmentId as string)}
                      aria-label={`Ver la fuente ${fila.fragmentId}${fila.fuente ? `: ${fila.fuente}` : ""}`}
                    >
                      [{fila.fragmentId}]
                    </Button>
                  ) : (
                    <span className="font-mono text-xs text-fg-accent">[{fila.fragmentId}]</span>
                  ))}
                {fila.fuente && <span className="truncate text-xs text-fg-muted">{fila.fuente}</span>}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="m-0 border-t border-border-muted px-3 py-2 text-xs text-fg-muted">
        Cada cifra se comprueba antes de enseñártela: sale de un cálculo de un especialista, de una fuente citada o de lo que
        tú me dijiste. Nunca sumo cifras de especialistas distintos si ninguna herramienta las ha combinado.
      </p>
    </DetallesCaja>
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

const ESTADO_ENCARGO: Record<EstadoEncargo, { texto: string; tono: TonoEstado; icono: Icon | null }> = {
  pendiente: { texto: "en espera", tono: "neutro", icono: CircleIcon },
  en_curso: { texto: "consultando…", tono: "acento", icono: null },
  listo: { texto: "listo", tono: "exito", icono: CheckCircleIcon },
  fallo: { texto: "sin respuesta", tono: "peligro", icono: AlertIcon },
};

/** The live plan while the turn runs (nothing until the brain has one with specialists). */
export function PlanEnVivo({ eventos }: { eventos: ProgressEvent[] }) {
  const { resumen, lineas, fase } = plan(eventos);
  if (lineas.length === 0) return null;
  return (
    <div className="mb-3 flex flex-col gap-2 border-b border-border-muted pb-3" aria-live="polite">
      <p className="m-0 flex items-center gap-2 font-semibold text-fg-default">
        <PeopleIcon className="text-fg-muted" />
        {resumen || "Consulto a los especialistas"}
      </p>
      <ul className="m-0 flex list-none flex-wrap gap-2 p-0" aria-label="Especialistas">
        {lineas.map((linea) => {
          const estado = ESTADO_ENCARGO[linea.estado];
          const Icono = estado.icono;
          return (
            <li key={linea.id} className="flex items-center gap-1">
              <span className="font-semibold">{linea.nombre}</span>
              <EtiquetaEstado tono={estado.tono} visual={Icono ? <Icono size={12} /> : <Spinner size="small" srText={null} className="size-3" />}>
                {estado.texto}
              </EtiquetaEstado>
              {linea.estado === "pendiente" && linea.espera.length > 0 && <span className="text-xs text-fg-muted">(espera a otro informe)</span>}
            </li>
          );
        })}
      </ul>
      {fase && <p className="m-0 text-xs text-fg-muted">{fase}</p>}
    </div>
  );
}
