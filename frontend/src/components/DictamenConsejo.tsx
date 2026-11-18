// The council's ruling («Dictamen del consejo») and its live progress.
//
//   · the recommendation and the council's confidence in plain words, the consensus, the agreement;
//   · «En qué coinciden / En qué discrepan» (each option's strongest arguments), the minority's case, the decisive
//     assumptions, what would change it and the datum that would settle it;
//   · every figure with where it comes from (the specialists' calculations; the same rows as «¿De dónde sale cada
//     cifra?» under a chat answer);
//   · «Cómo deliberó el consejo»: the proposals as the members saw them (anonymous), the critiques and rankings, and
//     only on request the reveal of who was who (seat and model);
//   · what the council knew about the user and what was anonymised; the cost spent.
// Data: GET /cases/{case_id}/consejo/{id} (asistente/plataforma/api/routers/consejo.py).

import clsx from "clsx";
import {
  Calculator,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  Eye,
  EyeOff,
  Loader2,
  Scale,
  ScrollText,
  ShieldCheck,
  TriangleAlert,
  UsersRound,
} from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";

import type { AdvisorNotification, InformeCerebro } from "../api";
import {
  CONSENSO_LABEL,
  ESTADO_LABEL,
  EN_MARCHA,
  confianzaEnPalabras,
  listarSesiones,
  usd,
  useSesionConsejo,
  type CifraConsejo,
  type Dictamen,
  type PasoProgreso,
  type PropuestaConsejo,
  type SesionConsejo,
} from "../apiConsejo";
import { errorMessage } from "../lib/memoria";
import { relativeTime } from "../lib/presentation";
import { ProcedenciaRespuesta } from "./ProcedenciaRespuesta";
import { RichText } from "./RichText";

const FASES: { id: string; nombre: string; detalle: string }[] = [
  { id: "instruccion", nombre: "Expediente", detalle: "Los especialistas calculan las cifras de cada opción" },
  { id: "propuestas", nombre: "Propuestas", detalle: "Cada miembro analiza por su cuenta, sin ver a los demás" },
  { id: "criticas", nombre: "Críticas anónimas", detalle: "Se critican sin saber de quién es cada propuesta" },
  { id: "revision", nombre: "Revisión", detalle: "Cada miembro lee las críticas y revisa su propuesta" },
  { id: "sintesis", nombre: "Dictamen", detalle: "El presidente reúne el consenso y el desacuerdo" },
];

type EstadoFase = "pendiente" | "en_curso" | "hecha" | "fallo";

const INICIO = new Set(["inicio", "start"]);
const FIN = new Set(["fin", "done", "terminada"]);

/** The state of each phase from the session's progress events. */
function estadoDeFases(progreso: PasoProgreso[], estado: SesionConsejo["estado"]): Record<string, EstadoFase> {
  const salida: Record<string, EstadoFase> = Object.fromEntries(FASES.map((f) => [f.id, "pendiente" as EstadoFase]));
  for (const paso of progreso) {
    if (paso.tipo !== "fase" || !paso.fase || !(paso.fase in salida)) continue;
    if (INICIO.has(paso.estado)) salida[paso.fase] = "en_curso";
    else if (FIN.has(paso.estado)) salida[paso.fase] = "hecha";
    else if (paso.estado === "fallida") salida[paso.fase] = "fallo";
  }
  if (estado === "terminada") for (const f of FASES) if (salida[f.id] !== "fallo") salida[f.id] = "hecha";
  return salida;
}

const ICONO_FASE = { pendiente: CircleDashed, en_curso: Loader2, hecha: CheckCircle2, fallo: TriangleAlert } as const;

/** The live phases of a running session (also shown, finished, at the end of the ruling). */
export function ProgresoConsejo({ sesion }: { sesion: SesionConsejo }) {
  const fases = estadoDeFases(sesion.progreso, sesion.estado);
  const trabajando = EN_MARCHA.has(sesion.estado);
  return (
    <ol className="mt-2 space-y-1.5" aria-label="Fases del consejo" aria-live={trabajando ? "polite" : undefined}>
      {FASES.map((fase) => {
        const estado = fases[fase.id];
        const Icono = ICONO_FASE[estado];
        return (
          <li key={fase.id} className="flex items-start gap-2 text-[13px]">
            <Icono
              size={15}
              aria-hidden
              className={clsx(
                "mt-0.5 flex-shrink-0",
                estado === "en_curso" && "animate-spin text-primary-dark",
                estado === "hecha" && "text-grounding-cited-fg",
                estado === "fallo" && "text-risk-high-fg",
                estado === "pendiente" && "text-muted",
              )}
            />
            <span>
              <span className={clsx("font-semibold", estado === "pendiente" ? "text-muted" : "text-ink")}>{fase.nombre}</span>
              <span className="sr-only">: {estado === "en_curso" ? "en curso" : estado === "hecha" ? "hecho" : estado === "fallo" ? "falló" : "pendiente"}</span>
              <span className="block text-[12px] text-muted">{fase.detalle}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function opcionTexto(id: string, sesion: SesionConsejo, condicion?: string | null): string {
  if (id === "depende") return condicion ? `Depende: ${condicion}` : "Depende";
  return sesion.expediente?.opciones.find((o) => o.id === id)?.descripcion ?? id;
}

function acuerdoEnPalabras(acuerdo: number | null | undefined): string | null {
  if (acuerdo === null || acuerdo === undefined) return null;
  if (acuerdo >= 0.7) return "acuerdo alto entre los miembros";
  if (acuerdo >= 0.4) return "acuerdo moderado entre los miembros";
  return "poco acuerdo entre los miembros";
}

function Lista({ titulo, items, icono }: { titulo: string; items: string[]; icono?: ReactNode }) {
  if (items.length === 0) return null;
  return (
    <section className="mt-4">
      <h4 className="flex items-center gap-1.5 text-[13px] font-bold uppercase tracking-[0.06em] text-ink-soft">
        {icono}
        {titulo}
      </h4>
      <ul className="mt-1.5 list-disc space-y-1 pl-5 text-[13.5px] leading-snug text-ink">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

function nombreDeAsiento(dictamen: Dictamen, asiento: string): string {
  return dictamen.miembros.find((m) => m.rol.id === asiento)?.rol.nombre ?? asiento;
}

/** The ruling's figures gate in the shape of the brain's report, so the chat's «¿De dónde sale cada cifra?» renders it
 * unchanged (figure → specialist → calculation → source). Null when the gate attributed nothing. */
function comoInformeCerebro(sesion: SesionConsejo, dictamen: Dictamen): InformeCerebro | null {
  const atribuciones = dictamen.puerta?.atribuciones ?? [];
  if (atribuciones.length === 0) return null;
  const porEspecialista = new Map<string, CifraConsejo[]>();
  for (const cifra of sesion.expediente?.cifras ?? []) {
    porEspecialista.set(cifra.especialista, [...(porEspecialista.get(cifra.especialista) ?? []), cifra]);
  }
  return {
    intencion: { tipo: "cruzada", encargos: [], necesita_memoria: false, confianza: 1, origen: "reglas", resumen: "" },
    ruta: "sintesis",
    informes: [...porEspecialista.entries()].map(([especialista, cifras], i) => ({
      encargo_id: `e${i + 1}`,
      especialista,
      nombre: especialista.charAt(0).toUpperCase() + especialista.slice(1),
      estado: "ok" as const,
      cifras,
      fragment_ids: [],
      duracion_ms: 0,
    })),
    peticiones: [],
    puerta: { violaciones: [], atribuciones, ignorados: [] },
    recuerdos_usados: [],
    revisiones_sintesis: 0,
    duracion_ms: 0,
  };
}

/** Figure → specialist → tool / source: the dossier's figures (the only ones the council may use). */
function CifrasConProcedencia({ cifras, usadas }: { cifras: CifraConsejo[]; usadas: Set<string> }) {
  if (cifras.length === 0) return null;
  const ordenadas = [...cifras].sort((a, b) => Number(usadas.has(b.literal)) - Number(usadas.has(a.literal)));
  return (
    <details className="group mt-4 rounded-lg border border-line bg-surface">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-[13px] font-semibold text-ink-soft hover:text-ink [&::-webkit-details-marker]:hidden">
        <ChevronDown size={14} aria-hidden className="transition group-open:rotate-180" />
        Todas las cifras del expediente
        <span className="font-normal text-muted">({cifras.length})</span>
      </summary>
      <ul className="divide-y divide-line border-t border-line">
        {ordenadas.map((c) => (
          <li key={c.id} className="flex flex-col gap-0.5 px-3 py-2 text-[13px] sm:flex-row sm:items-baseline sm:gap-3">
            <span className="font-mono text-[13px] font-semibold tabular-nums text-ink sm:w-36 sm:flex-shrink-0">{c.literal || c.valor}</span>
            <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-1.5 text-ink-soft">
              <Calculator size={13} aria-hidden className="relative top-0.5 flex-shrink-0 text-primary-dark" />
              <span className="font-medium capitalize">{c.especialista}</span>
              <span className="text-muted">· {c.concepto}</span>
              {c.herramienta && <span className="text-muted">· cálculo «{c.herramienta}»</span>}
              {c.fragment_id && <span className="font-mono text-[12px] text-primary-dark">[{c.fragment_id}]</span>}
              {usadas.has(c.literal) && <span className="rounded-pill bg-grounding-cited-bg px-1.5 text-[11px] font-semibold text-grounding-cited-fg">en el dictamen</span>}
            </span>
          </li>
        ))}
      </ul>
      <p className="border-t border-line px-3 py-2 text-[12px] text-muted">
        El consejo no calcula cifras: todas salen de los cálculos de los especialistas, y un control automático retira del
        dictamen cualquier número que no salga de ellos.
      </p>
    </details>
  );
}

function PropuestaVista({ propuesta, etiqueta, sesion, autor }: { propuesta: PropuestaConsejo; etiqueta: string; sesion: SesionConsejo; autor?: string }) {
  return (
    <article className="rounded-lg border border-line bg-paper px-3 py-2.5">
      <p className="flex flex-wrap items-baseline gap-x-2 text-[13px]">
        <span className="font-bold text-ink">Propuesta {etiqueta}</span>
        {autor && <span className="rounded-pill bg-primary-soft px-2 text-[11.5px] font-semibold text-primary-dark">{autor}</span>}
        <span className="text-muted">
          recomienda «{opcionTexto(propuesta.recomendacion, sesion, propuesta.condicion)}» · {confianzaEnPalabras(propuesta.confianza).toLowerCase()}
        </span>
      </p>
      <p className="mt-1 whitespace-pre-line text-[13px] leading-snug text-ink-soft">{propuesta.razonamiento}</p>
      {propuesta.cambios && <p className="mt-1 text-[12.5px] italic text-muted">Tras las críticas: {propuesta.cambios}</p>}
    </article>
  );
}

/** «Cómo deliberó el consejo»: anonymous first; who was who only when the user asks. */
function Deliberacion({ sesion, dictamen }: { sesion: SesionConsejo; dictamen: Dictamen }) {
  const [revelar, setRevelar] = useState(false);
  const etiquetaDe = new Map(Object.entries(dictamen.autoria).map(([etiqueta, asiento]) => [asiento, etiqueta]));
  const etiqueta = (asiento: string) => etiquetaDe.get(asiento) ?? "·";
  const ronda1 = sesion.propuestas.filter((p) => p.ronda === 1);
  const ronda2 = sesion.propuestas.filter((p) => p.ronda === 2);
  const criticasPor = new Map<string, typeof sesion.criticas>();
  for (const critica of sesion.criticas) criticasPor.set(critica.etiqueta, [...(criticasPor.get(critica.etiqueta) ?? []), critica]);
  const orden = (lista: PropuestaConsejo[]) => [...lista].sort((a, b) => etiqueta(a.miembro_id).localeCompare(etiqueta(b.miembro_id)));
  return (
    <details className="group mt-4 rounded-lg border border-line bg-surface">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-[13px] font-semibold text-ink-soft hover:text-ink [&::-webkit-details-marker]:hidden">
        <ChevronDown size={14} aria-hidden className="transition group-open:rotate-180" />
        <UsersRound size={14} aria-hidden className="text-primary-dark" />
        Cómo deliberó el consejo
      </summary>
      <div className="space-y-4 border-t border-line px-3 py-3">
        <p className="text-[12.5px] leading-snug text-muted">
          Cada miembro trabajó por su cuenta con el mismo expediente. Después criticó las propuestas de los demás sin saber de
          quién eran (letras en orden distinto para cada uno), y nadie se evaluó a sí mismo. Tu preferencia no la vieron hasta
          la revisión.
        </p>
        <section>
          <h5 className="text-[12.5px] font-bold uppercase tracking-[0.06em] text-ink-soft">1. Propuestas independientes</h5>
          <div className="mt-1.5 space-y-2">
            {orden(ronda1).map((p) => (
              <PropuestaVista key={`1-${p.miembro_id}`} propuesta={p} etiqueta={etiqueta(p.miembro_id)} sesion={sesion} autor={revelar ? nombreDeAsiento(dictamen, p.miembro_id) : undefined} />
            ))}
          </div>
        </section>
        {criticasPor.size > 0 && (
          <section>
            <h5 className="text-[12.5px] font-bold uppercase tracking-[0.06em] text-ink-soft">2. Críticas anónimas</h5>
            <ul className="mt-1.5 space-y-2">
              {[...criticasPor.entries()]
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([destino, criticas]) => (
                  <li key={destino} className="rounded-lg border border-line bg-paper px-3 py-2 text-[13px]">
                    <p className="font-semibold text-ink">Sobre la propuesta {destino}</p>
                    <ul className="mt-1 space-y-1.5">
                      {criticas.map((c, i) => {
                        const nota = Object.values(c.puntuacion).reduce((s, v) => s + v, 0) / 5;
                        return (
                          <li key={i} className="text-ink-soft">
                            <span className="font-medium text-ink">{revelar ? nombreDeAsiento(dictamen, c.evaluador_id) : `Revisor ${i + 1}`}</span>{" "}
                            <span className="text-muted">(nota media {nota.toLocaleString("es-ES", { maximumFractionDigits: 1 })} de 5)</span>
                            {c.fortalezas.length > 0 && <span className="block">A favor: {c.fortalezas.join("; ")}</span>}
                            {c.debilidades.length > 0 && <span className="block">En contra: {c.debilidades.join("; ")}</span>}
                            {c.errores.map((e, j) => (
                              <span key={j} className="block text-risk-high-fg">Error señalado ({e.tipo}): {e.texto}</span>
                            ))}
                          </li>
                        );
                      })}
                    </ul>
                  </li>
                ))}
            </ul>
          </section>
        )}
        {sesion.rankings.length > 0 && (
          <section>
            <h5 className="text-[12.5px] font-bold uppercase tracking-[0.06em] text-ink-soft">Clasificaciones</h5>
            <ul className="mt-1 space-y-0.5 text-[13px] text-ink-soft">
              {sesion.rankings.map((r, i) => (
                <li key={i}>
                  {revelar ? nombreDeAsiento(dictamen, r.evaluador_id) : `Revisor ${i + 1}`}: {r.orden.join(" › ")}
                </li>
              ))}
            </ul>
          </section>
        )}
        {ronda2.length > 0 && (
          <section>
            <h5 className="text-[12.5px] font-bold uppercase tracking-[0.06em] text-ink-soft">3. Propuestas revisadas</h5>
            <div className="mt-1.5 space-y-2">
              {orden(ronda2).map((p) => (
                <PropuestaVista key={`2-${p.miembro_id}`} propuesta={p} etiqueta={etiqueta(p.miembro_id)} sesion={sesion} autor={revelar ? nombreDeAsiento(dictamen, p.miembro_id) : undefined} />
              ))}
            </div>
          </section>
        )}
        <section>
          <button
            type="button"
            onClick={() => setRevelar((v) => !v)}
            aria-pressed={revelar}
            className="inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1 text-[12.5px] font-semibold text-ink-soft hover:border-primary hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
          >
            {revelar ? <EyeOff size={13} aria-hidden /> : <Eye size={13} aria-hidden />}
            {revelar ? "Ocultar quién era quién" : "Revelar quién era quién"}
          </button>
          {revelar && (
            <ul className="mt-2 space-y-1 text-[13px] text-ink-soft">
              {dictamen.miembros.map((m) => (
                <li key={m.rol.id}>
                  {etiquetaDe.has(m.rol.id) ? <span className="font-semibold text-ink">Propuesta {etiquetaDe.get(m.rol.id)}: </span> : <span className="font-semibold text-ink">Presidencia: </span>}
                  {m.rol.nombre} <span className="text-muted">({m.proveedor} · {m.modelo})</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </details>
  );
}

/** What the council knew about the user, and what was left out. */
function Anonimizado({ sesion }: { sesion: SesionConsejo }) {
  const expediente = sesion.expediente;
  if (!expediente) return null;
  const perfil = Object.entries(expediente.perfil_minimo);
  return (
    <details className="group mt-4 rounded-lg border border-line bg-surface">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-[13px] font-semibold text-ink-soft hover:text-ink [&::-webkit-details-marker]:hidden">
        <ChevronDown size={14} aria-hidden className="transition group-open:rotate-180" />
        <ShieldCheck size={14} aria-hidden className="text-primary-dark" />
        Qué supo el consejo de ti y qué se anonimizó
      </summary>
      <div className="space-y-3 border-t border-line px-3 py-3 text-[13px] text-ink-soft">
        <p>
          <span className="font-semibold text-ink">La pregunta, en neutro: </span>
          {expediente.pregunta}
        </p>
        {perfil.length > 0 && (
          <div>
            <p className="font-semibold text-ink">Solo estos datos tuyos:</p>
            <ul className="mt-1 grid gap-x-4 gap-y-0.5 sm:grid-cols-2">
              {perfil.map(([clave, valor]) => (
                <li key={clave}>
                  <span className="text-muted">{clave}:</span> {valor}
                </li>
              ))}
            </ul>
          </div>
        )}
        {expediente.anonimizado.length > 0 && (
          <div>
            <p className="font-semibold text-ink">Lo que se quitó o se enmascaró:</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">
              {expediente.anonimizado.map((nota, i) => (
                <li key={i}>{nota}</li>
              ))}
            </ul>
          </div>
        )}
        {expediente.preferencia_usuario && (
          <p>
            <span className="font-semibold text-ink">Tu preferencia</span> («{expediente.preferencia_usuario}») se mantuvo sellada: los miembros
            no la vieron en su primera propuesta, solo al revisarla.
          </p>
        )}
      </div>
    </details>
  );
}

function Coste({ sesion }: { sesion: SesionConsejo }) {
  return (
    <p className="mt-4 text-[12px] text-muted">
      Coste: {usd(sesion.coste.usd)} (estimado {usd(sesion.estimacion_usd)}, tope {usd(sesion.tope_usd)}) · {sesion.coste.llamadas}{" "}
      llamadas al modelo
    </p>
  );
}

/** The full ruling of a session (or its state while it is not finished). */
export function DictamenConsejo({ sesion }: { sesion: SesionConsejo }) {
  const dictamen = sesion.dictamen;
  if (EN_MARCHA.has(sesion.estado)) {
    return (
      <div>
        <p className="text-[13.5px] text-ink-soft">{ESTADO_LABEL[sesion.estado]}… Te aviso cuando el dictamen esté listo.</p>
        <ProgresoConsejo sesion={sesion} />
      </div>
    );
  }
  if (sesion.estado !== "terminada" || !dictamen) {
    return (
      <div>
        <p className="text-[13.5px] text-ink-soft">
          {ESTADO_LABEL[sesion.estado]}.{sesion.error ? ` ${sesion.error}` : ""}
        </p>
        {sesion.estado === "fallida" && <Coste sesion={sesion} />}
      </div>
    );
  }
  const usadas = new Set((dictamen.puerta?.atribuciones ?? []).map((a) => a.literal));
  const acuerdo = acuerdoEnPalabras(dictamen.acuerdo);
  const porcentaje = Math.round(dictamen.confianza * 100);
  return (
    <div>
      <section aria-label="Recomendación del consejo" className="rounded-xl border border-primary/40 bg-primary-soft/40 px-4 py-3">
        <p className="text-[11.5px] font-semibold uppercase tracking-[0.08em] text-primary-dark">Recomendación</p>
        <p className="mt-1 text-[16px] font-bold leading-snug text-ink">{opcionTexto(dictamen.recomendacion, sesion, dictamen.condicion)}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[12.5px]">
          <span className="rounded-pill bg-surface px-2 py-0.5 font-semibold text-ink">{CONSENSO_LABEL[dictamen.consenso]}</span>
          <span className="text-ink-soft">
            {confianzaEnPalabras(dictamen.confianza)} ({porcentaje} %){acuerdo ? ` · ${acuerdo}` : ""}
          </span>
        </div>
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-pill bg-line" role="img" aria-label={`Confianza del consejo: ${porcentaje} %`}>
          <div className="h-full rounded-pill bg-primary" style={{ width: `${porcentaje}%` }} />
        </div>
      </section>

      <div className="mt-3 text-[14px] leading-relaxed">
        <RichText text={dictamen.resumen} />
      </div>

      {dictamen.posturas.length > 0 && (
        <section className="mt-4">
          <h4 className="flex items-center gap-1.5 text-[13px] font-bold uppercase tracking-[0.06em] text-ink-soft">
            <Scale size={14} aria-hidden className="text-primary-dark" />
            En qué coinciden y en qué discrepan
          </h4>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            {dictamen.posturas.map((p) => (
              <article key={p.opcion_id} className={clsx("rounded-lg border px-3 py-2", p.opcion_id === dictamen.recomendacion ? "border-primary/50 bg-surface" : "border-line bg-paper")}>
                <p className="text-[13.5px] font-semibold text-ink">{opcionTexto(p.opcion_id, sesion)}</p>
                {p.miembros.length > 0 && (
                  <p className="text-[12px] text-muted">
                    La recomienda{p.miembros.length > 1 ? "n" : ""}: {p.miembros.map((m) => nombreDeAsiento(dictamen, m)).join(", ")}
                  </p>
                )}
                {p.a_favor.length > 0 && (
                  <ul className="mt-1 list-disc pl-5 text-[13px] text-ink-soft">
                    {p.a_favor.map((a, i) => (
                      <li key={i}>
                        <span className="sr-only">A favor: </span>
                        {a}
                      </li>
                    ))}
                  </ul>
                )}
                {p.en_contra.length > 0 && (
                  <ul className="mt-1 list-disc pl-5 text-[13px] text-muted">
                    {p.en_contra.map((a, i) => (
                      <li key={i}>
                        <span className="sr-only">En contra: </span>
                        {a}
                      </li>
                    ))}
                  </ul>
                )}
              </article>
            ))}
          </div>
        </section>
      )}

      {dictamen.disenso && (
        <section className="mt-4 rounded-lg border border-line bg-surface px-3 py-2">
          <h4 className="text-[13px] font-bold uppercase tracking-[0.06em] text-ink-soft">El argumento de la minoría</h4>
          <p className="mt-1 text-[13.5px] leading-snug text-ink">{dictamen.disenso}</p>
        </section>
      )}
      <Lista titulo="Supuestos de los que depende" items={dictamen.supuestos_decisivos} />
      <Lista titulo="Qué la haría cambiar" items={dictamen.sensibilidad} />
      <Lista titulo="Qué dato lo aclararía" items={dictamen.datos_que_resolverian} icono={<ScrollText size={14} aria-hidden className="text-primary-dark" />} />
      {dictamen.avisos.length > 0 && (
        <ul className="mt-4 space-y-1 rounded-lg border border-grounding-abstain-bd bg-grounding-abstain-bg px-3 py-2 text-[12.5px] text-grounding-abstain-fg">
          {dictamen.avisos.map((a, i) => (
            <li key={i} className="flex gap-1.5">
              <TriangleAlert size={13} aria-hidden className="mt-0.5 flex-shrink-0" />
              {a}
            </li>
          ))}
        </ul>
      )}
      <ProcedenciaRespuesta cerebro={comoInformeCerebro(sesion, dictamen)} citations={[]} />
      <CifrasConProcedencia cifras={sesion.expediente?.cifras ?? []} usadas={usadas} />
      <Deliberacion sesion={sesion} dictamen={dictamen} />
      <Anonimizado sesion={sesion} />
      <Coste sesion={sesion} />
      <p className="mt-2 text-[11.5px] leading-snug text-muted">
        El dictamen es una ayuda para decidir, no una decisión: nada se hace sin que tú lo confirmes.
      </p>
    </div>
  );
}

/** The inbox card of a «Dictamen del consejo listo» notification: its text, and the ruling on demand. */
export function AvisoConsejo({ notification: n, caseId, onMarkRead }: { notification: AdvisorNotification; caseId: string; onMarkRead: (id: string) => void }) {
  const [abierto, setAbierto] = useState(false);
  const [sesionId, setSesionId] = useState<string | null>(null);
  const [buscando, setBuscando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { sesion, error: errorSesion } = useSesionConsejo(caseId, abierto ? sesionId : null);
  const unread = !n.read_at;

  function abrir() {
    if (abierto) {
      setAbierto(false);
      return;
    }
    setAbierto(true);
    if (unread) onMarkRead(n.id);
    if (sesionId) return;
    setBuscando(true);
    listarSesiones(caseId)
      .then((lista) => {
        const suya = lista.sesiones.find((s) => s.notificacion_id === n.id);
        if (suya) setSesionId(suya.id);
        else setError("Esta sesión del consejo ya no existe.");
      })
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBuscando(false));
  }

  return (
    <article className={clsx("rounded-xl border bg-surface px-4 py-3.5 shadow-card", unread ? "border-primary/50" : "border-line")}>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] font-semibold uppercase tracking-[0.08em] text-muted">
        <UsersRound size={14} aria-hidden className="text-primary-dark" />
        Consejo del hogar
        {unread && <span className="rounded-pill bg-primary px-2 py-px normal-case tracking-normal text-white">Nuevo</span>}
        <span className="ml-auto font-normal normal-case tracking-normal">{relativeTime(Date.parse(n.created_at))}</span>
      </p>
      <h4 className="mt-1.5 text-[15px] font-bold leading-snug text-ink">{n.title}</h4>
      <div className="mt-2 text-[13.5px] [&_div]:text-[13.5px] [&_div]:leading-snug">
        <RichText text={n.body} />
      </div>
      <button
        type="button"
        onClick={abrir}
        aria-expanded={abierto}
        className="mt-3 inline-flex items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-[12.5px] font-semibold text-primary-dark transition hover:border-primary"
      >
        <ChevronDown size={13} aria-hidden className={clsx("transition", abierto && "rotate-180")} />
        {abierto ? "Ocultar el dictamen" : "Ver el dictamen"}
      </button>
      {abierto && (
        <div className="mt-3 border-t border-line pt-3">
          {(buscando || (!sesion && !error && !errorSesion)) && (
            <p role="status" className="text-[13px] text-muted">
              Cargando el dictamen…
            </p>
          )}
          {(error || errorSesion) && (
            <p role="alert" className="text-[13px] text-risk-high-fg">
              {error ?? errorSesion}
            </p>
          )}
          {sesion && <DictamenConsejo sesion={sesion} />}
        </div>
      )}
    </article>
  );
}
