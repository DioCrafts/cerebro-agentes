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

//
// Design system: the phases are a condensed Timeline (like a workflow run's steps); the recommendation is an accent
// Box with a ProgressBar; positions, minority and lists are Boxes and headings; the long parts (every figure, the
// deliberation, what was anonymised) are folding Boxes; warnings are Flash.

import type { ReactNode } from "react";
import { useState } from "react";

import type { AdvisorNotification, InformeCerebro } from "../api";
import {
  CONSENSO_LABEL,
  ESTADO_LABEL,
  EN_MARCHA,
  PREFIJO_PARCIAL,
  confianzaEnPalabras,
  listarSesiones,
  usd,
  type CifraConsejo,
  type Dictamen,
  type PasoProgreso,
  type PropuestaConsejo,
  type SesionConsejo,
} from "../apiConsejo";
import { errorMessage } from "../lib/memoria";
import { relativeTime } from "../lib/presentation";
import { useSesionConsejo } from "../shell/useSesionConsejo";
import { Button, Caja, CargandoTexto, Flash, Label, ProgressBar, Spinner, Timeline, VisuallyHidden } from "../ui";
import type { Icon } from "../ui/iconos";
import { AlertIcon, CheckIcon, ChevronDownIcon, ChevronUpIcon, CircleIcon, EyeClosedIcon, EyeIcon, LawIcon, NumberIcon, PeopleIcon, ShieldCheckIcon } from "../ui/iconos";
import { DetallesCaja } from "./conversacion/DetallesCaja";
import { ListaTimeline, PasoTimeline } from "./conversacion/ListaTimeline";
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
  const salida: Record<string, EstadoFase> = Object.fromEntries(FASES.map((f) => [f.id, "pendiente"]));
  for (const paso of progreso) {
    if (paso.tipo !== "fase" || !paso.fase || !(paso.fase in salida)) continue;
    if (INICIO.has(paso.estado)) salida[paso.fase] = "en_curso";
    else if (FIN.has(paso.estado)) salida[paso.fase] = "hecha";
    else if (paso.estado === "fallida") salida[paso.fase] = "fallo";
  }
  if (estado === "terminada") for (const f of FASES) if (salida[f.id] !== "fallo") salida[f.id] = "hecha";
  return salida;
}

const FASE_VISUAL: Record<EstadoFase, { icono: Icon | null; variante?: "success" | "danger" | "accent"; texto: string }> = {
  pendiente: { icono: CircleIcon, texto: "pendiente" },
  en_curso: { icono: null, variante: "accent", texto: "en curso" },
  hecha: { icono: CheckIcon, variante: "success", texto: "hecho" },
  fallo: { icono: AlertIcon, variante: "danger", texto: "falló" },
};

/** The live phases of a running session (also shown, finished, at the end of the ruling). */
export function ProgresoConsejo({ sesion }: { sesion: SesionConsejo }) {
  const fases = estadoDeFases(sesion.progreso, sesion.estado);
  const trabajando = EN_MARCHA.has(sesion.estado);
  return (
    <ListaTimeline etiqueta="Fases del consejo" className="mt-2" vivo={trabajando}>
      {FASES.map((fase) => {
        const estado = fases[fase.id] ?? "pendiente";
        const visual = FASE_VISUAL[estado];
        const Icono = visual.icono;
        return (
          <PasoTimeline key={fase.id}>
            <Timeline.Badge variant={visual.variante}>{Icono ? <Icono /> : <Spinner size="small" srText={null} />}</Timeline.Badge>
            <Timeline.Body>
              <span className={estado === "pendiente" ? "font-semibold text-fg-muted" : "font-semibold text-fg-default"}>{fase.nombre}</span>
              <VisuallyHidden>: {visual.texto}</VisuallyHidden>
              <span className="block text-xs text-fg-muted">{fase.detalle}</span>
            </Timeline.Body>
          </PasoTimeline>
        );
      })}
    </ListaTimeline>
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
    <section className="mt-4" aria-label={titulo}>
      <h5 className="m-0 flex items-center gap-2 text-sm font-semibold text-fg-default">
        {icono}
        {titulo}
      </h5>
      <ul className="m-0 mt-1 list-disc pl-5">
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
    <DetallesCaja className="mt-4" resumen="Todas las cifras del expediente" contador={cifras.length}>
      <ul className="m-0 list-none divide-y divide-border-muted p-0">
        {ordenadas.map((c) => (
          <li key={c.id} className="flex flex-col gap-1 px-3 py-2 sm:flex-row sm:items-baseline sm:gap-3">
            <span className="font-mono font-semibold tabular-nums text-fg-default sm:w-36 sm:shrink-0">{c.literal || c.valor}</span>
            <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
              <NumberIcon size={12} className="shrink-0 text-fg-muted" />
              <span className="font-semibold capitalize">{c.especialista}</span>
              <span className="text-fg-muted">· {c.concepto}</span>
              {c.herramienta && <span className="text-fg-muted">· cálculo «{c.herramienta}»</span>}
              {c.fragment_id && <span className="font-mono text-xs text-fg-accent">[{c.fragment_id}]</span>}
              {usadas.has(c.literal) && <Label variant="success">en el dictamen</Label>}
            </span>
          </li>
        ))}
      </ul>
      <p className="m-0 border-t border-border-muted px-3 py-2 text-xs text-fg-muted">
        El consejo no calcula cifras: todas salen de los cálculos de los especialistas, y un control automático retira del
        dictamen cualquier número que no salga de ellos.
      </p>
    </DetallesCaja>
  );
}

function PropuestaVista({ propuesta, etiqueta, sesion, autor }: { propuesta: PropuestaConsejo; etiqueta: string; sesion: SesionConsejo; autor?: string }) {
  return (
    <Caja as="article" condensada aria-label={`Propuesta ${etiqueta}`}>
      <Caja.Cabecera>
        <span className="font-semibold text-fg-default">Propuesta {etiqueta}</span>
        {autor && <Label variant="accent">{autor}</Label>}
        <span className="min-w-0 flex-1 text-xs text-fg-muted">
          recomienda «{opcionTexto(propuesta.recomendacion, sesion, propuesta.condicion)}» · {confianzaEnPalabras(propuesta.confianza).toLowerCase()}
        </span>
      </Caja.Cabecera>
      <Caja.Cuerpo>
        <p className="m-0 whitespace-pre-line">{propuesta.razonamiento}</p>
        {propuesta.cambios && <p className="m-0 mt-1 text-xs text-fg-muted">Tras las críticas: {propuesta.cambios}</p>}
      </Caja.Cuerpo>
    </Caja>
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
    <DetallesCaja className="mt-4" resumen="Cómo deliberó el consejo" icono={<PeopleIcon className="shrink-0 text-fg-muted" />}>
      <div className="flex flex-col gap-4 p-3">
        <p className="m-0 text-xs text-fg-muted">
          Cada miembro trabajó por su cuenta con el mismo expediente. Después criticó las propuestas de los demás sin saber de
          quién eran (letras en orden distinto para cada uno), y nadie se evaluó a sí mismo. Tu preferencia no la vieron hasta
          la revisión.
        </p>
        <section aria-label="Propuestas independientes" className="flex flex-col gap-2">
          <h6 className="m-0 text-sm font-semibold">1. Propuestas independientes</h6>
          {orden(ronda1).map((p) => (
            <PropuestaVista key={`1-${p.miembro_id}`} propuesta={p} etiqueta={etiqueta(p.miembro_id)} sesion={sesion} autor={revelar ? nombreDeAsiento(dictamen, p.miembro_id) : undefined} />
          ))}
        </section>
        {criticasPor.size > 0 && (
          <section aria-label="Críticas anónimas" className="flex flex-col gap-2">
            <h6 className="m-0 text-sm font-semibold">2. Críticas anónimas</h6>
            <Caja condensada>
              <Caja.Lista>
                {[...criticasPor.entries()]
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([destino, criticas]) => (
                    <Caja.Fila key={destino}>
                      <p className="m-0 font-semibold">Sobre la propuesta {destino}</p>
                      <ul className="m-0 mt-1 flex list-none flex-col gap-1 p-0">
                        {criticas.map((c, i) => {
                          const nota = Object.values(c.puntuacion).reduce((s, v) => s + v, 0) / 5;
                          return (
                            <li key={i}>
                              <span className="font-semibold">{revelar ? nombreDeAsiento(dictamen, c.evaluador_id) : `Revisor ${i + 1}`}</span>{" "}
                              <span className="text-fg-muted">(nota media {nota.toLocaleString("es-ES", { maximumFractionDigits: 1 })} de 5)</span>
                              {c.fortalezas.length > 0 && <span className="block">A favor: {c.fortalezas.join("; ")}</span>}
                              {c.debilidades.length > 0 && <span className="block">En contra: {c.debilidades.join("; ")}</span>}
                              {c.errores.map((e, j) => (
                                <span key={j} className="block text-fg-danger">
                                  Error señalado ({e.tipo}): {e.texto}
                                </span>
                              ))}
                            </li>
                          );
                        })}
                      </ul>
                    </Caja.Fila>
                  ))}
              </Caja.Lista>
            </Caja>
          </section>
        )}
        {sesion.rankings.length > 0 && (
          <section aria-label="Clasificaciones" className="flex flex-col gap-1">
            <h6 className="m-0 text-sm font-semibold">Clasificaciones</h6>
            <ul className="m-0 list-none p-0">
              {sesion.rankings.map((r, i) => (
                <li key={i}>
                  {revelar ? nombreDeAsiento(dictamen, r.evaluador_id) : `Revisor ${i + 1}`}: {r.orden.join(" › ")}
                </li>
              ))}
            </ul>
          </section>
        )}
        {ronda2.length > 0 && (
          <section aria-label="Propuestas revisadas" className="flex flex-col gap-2">
            <h6 className="m-0 text-sm font-semibold">3. Propuestas revisadas</h6>
            {orden(ronda2).map((p) => (
              <PropuestaVista key={`2-${p.miembro_id}`} propuesta={p} etiqueta={etiqueta(p.miembro_id)} sesion={sesion} autor={revelar ? nombreDeAsiento(dictamen, p.miembro_id) : undefined} />
            ))}
          </section>
        )}
        <div className="flex flex-col gap-2">
          <div>
            <Button size="small" onClick={() => setRevelar((v) => !v)} aria-pressed={revelar} leadingVisual={revelar ? EyeClosedIcon : EyeIcon}>
              {revelar ? "Ocultar quién era quién" : "Revelar quién era quién"}
            </Button>
          </div>
          {revelar && (
            <ul className="m-0 list-none p-0" aria-label="Quién era quién">
              {dictamen.miembros.map((m) => (
                <li key={m.rol.id}>
                  <span className="font-semibold">{etiquetaDe.has(m.rol.id) ? `Propuesta ${etiquetaDe.get(m.rol.id)}: ` : "Presidencia: "}</span>
                  {m.rol.nombre}{" "}
                  <span className="text-fg-muted">
                    ({m.proveedor} · {m.modelo})
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </DetallesCaja>
  );
}

/** What the council knew about the user, and what was left out. */
function Anonimizado({ sesion }: { sesion: SesionConsejo }) {
  const expediente = sesion.expediente;
  if (!expediente) return null;
  const perfil = Object.entries(expediente.perfil_minimo);
  return (
    <DetallesCaja className="mt-4" resumen="Qué supo el consejo de ti y qué se anonimizó" icono={<ShieldCheckIcon className="shrink-0 text-fg-muted" />}>
      <div className="flex flex-col gap-3 p-3">
        <p className="m-0">
          <span className="font-semibold">La pregunta, en neutro: </span>
          {expediente.pregunta}
        </p>
        {perfil.length > 0 && (
          <div>
            <p className="m-0 font-semibold">Solo estos datos tuyos:</p>
            <ul className="m-0 mt-1 grid list-none gap-x-4 gap-y-0.5 p-0 sm:grid-cols-2">
              {perfil.map(([clave, valor]) => (
                <li key={clave}>
                  <span className="text-fg-muted">{clave}:</span> {valor}
                </li>
              ))}
            </ul>
          </div>
        )}
        {expediente.anonimizado.length > 0 && (
          <div>
            <p className="m-0 font-semibold">Lo que se quitó o se enmascaró:</p>
            <ul className="m-0 mt-1 list-disc pl-5">
              {expediente.anonimizado.map((nota, i) => (
                <li key={i}>{nota}</li>
              ))}
            </ul>
          </div>
        )}
        {expediente.preferencia_usuario && (
          <p className="m-0">
            <span className="font-semibold">Tu preferencia</span> («{expediente.preferencia_usuario}») se mantuvo sellada: los miembros no la
            vieron en su primera propuesta, solo al revisarla.
          </p>
        )}
      </div>
    </DetallesCaja>
  );
}

function Coste({ sesion }: { sesion: SesionConsejo }) {
  return (
    <p className="m-0 mt-4 text-xs text-fg-muted">
      Coste: {usd(sesion.coste.usd)} (estimado {usd(sesion.estimacion_usd)}, tope {usd(sesion.tope_usd)}) · {sesion.coste.llamadas} llamadas al
      modelo
    </p>
  );
}

/** The full ruling of a session (or its state while it is not finished). */
export function DictamenConsejo({ sesion }: { sesion: SesionConsejo }) {
  const dictamen = sesion.dictamen;
  if (EN_MARCHA.has(sesion.estado)) {
    return (
      <div>
        <p className="m-0">{ESTADO_LABEL[sesion.estado]}… Te aviso cuando el dictamen esté listo.</p>
        <ProgresoConsejo sesion={sesion} />
      </div>
    );
  }
  if (sesion.estado !== "terminada" || !dictamen) {
    return (
      <div>
        <p className="m-0">
          {ESTADO_LABEL[sesion.estado]}.{sesion.error ? ` ${sesion.error}` : ""}
        </p>
        {sesion.estado === "fallida" && <Coste sesion={sesion} />}
      </div>
    );
  }
  const usadas = new Set((dictamen.puerta?.atribuciones ?? []).map((a) => a.literal));
  const avisoParcial = dictamen.avisos.find((a) => a.startsWith(PREFIJO_PARCIAL));
  const avisos = dictamen.avisos.filter((a) => a !== avisoParcial);
  const acuerdo = acuerdoEnPalabras(dictamen.acuerdo);
  const porcentaje = Math.round(dictamen.confianza * 100);
  return (
    <div>
      {avisoParcial && (
        <Flash variant="warning" role="note" aria-label="Dictamen parcial" className="mb-3">
          <p className="m-0 font-semibold">Dictamen parcial</p>
          <p className="m-0">
            {avisoParcial.slice(PREFIJO_PARCIAL.length).trim() || "La sesión se detuvo antes de terminar."} Lo que sigue lo ha resumido el
            código con lo que el consejo llegó a hacer; tómalo con más cautela.
          </p>
        </Flash>
      )}
      <Caja as="section" variante="acento" aria-label="Recomendación del consejo">
        <Caja.Cuerpo className="flex flex-col gap-2">
          <p className="m-0 text-xs font-semibold text-fg-accent">Recomendación</p>
          <p className="m-0 text-base font-semibold text-fg-default">{opcionTexto(dictamen.recomendacion, sesion, dictamen.condicion)}</p>
          <p className="m-0 flex flex-wrap items-center gap-2 text-xs">
            <Label variant="accent">{CONSENSO_LABEL[dictamen.consenso]}</Label>
            <span className="text-fg-muted">
              {confianzaEnPalabras(dictamen.confianza)} ({porcentaje} %){acuerdo ? ` · ${acuerdo}` : ""}
            </span>
          </p>
          <ProgressBar progress={porcentaje} barSize="small" aria-label={`Confianza del consejo: ${porcentaje} %`} />
        </Caja.Cuerpo>
      </Caja>

      <div className="mt-3">
        <RichText text={dictamen.resumen} />
      </div>

      {dictamen.posturas.length > 0 && (
        <section className="mt-4" aria-label="En qué coinciden y en qué discrepan">
          <h5 className="m-0 flex items-center gap-2 text-sm font-semibold text-fg-default">
            <LawIcon className="text-fg-muted" />
            En qué coinciden y en qué discrepan
          </h5>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            {dictamen.posturas.map((p) => {
              const recomendada = p.opcion_id === dictamen.recomendacion;
              return (
                <Caja key={p.opcion_id} as="article" condensada variante={recomendada ? "acento" : "normal"} aria-label={opcionTexto(p.opcion_id, sesion)}>
                  <Caja.Cabecera>
                    <span className="min-w-0 flex-1 font-semibold">{opcionTexto(p.opcion_id, sesion)}</span>
                    {recomendada && <Label variant="accent">Recomendada</Label>}
                  </Caja.Cabecera>
                  <Caja.Cuerpo>
                    {p.miembros.length > 0 && (
                      <p className="m-0 text-xs text-fg-muted">
                        La recomienda{p.miembros.length > 1 ? "n" : ""}: {p.miembros.map((m) => nombreDeAsiento(dictamen, m)).join(", ")}
                      </p>
                    )}
                    {p.a_favor.length > 0 && (
                      <ul className="m-0 mt-1 list-disc pl-5">
                        {p.a_favor.map((a, i) => (
                          <li key={i}>
                            <VisuallyHidden>A favor: </VisuallyHidden>
                            {a}
                          </li>
                        ))}
                      </ul>
                    )}
                    {p.en_contra.length > 0 && (
                      <ul className="m-0 mt-1 list-disc pl-5 text-fg-muted">
                        {p.en_contra.map((a, i) => (
                          <li key={i}>
                            <VisuallyHidden>En contra: </VisuallyHidden>
                            {a}
                          </li>
                        ))}
                      </ul>
                    )}
                  </Caja.Cuerpo>
                </Caja>
              );
            })}
          </div>
        </section>
      )}

      {dictamen.disenso && (
        <Caja as="section" condensada className="mt-4" aria-label="El argumento de la minoría">
          <Caja.Cabecera>
            <h5 className="m-0 text-sm font-semibold">El argumento de la minoría</h5>
          </Caja.Cabecera>
          <Caja.Cuerpo>
            <p className="m-0">{dictamen.disenso}</p>
          </Caja.Cuerpo>
        </Caja>
      )}
      <Lista titulo="Supuestos de los que depende" items={dictamen.supuestos_decisivos} />
      <Lista titulo="Qué la haría cambiar" items={dictamen.sensibilidad} />
      <Lista titulo="Qué dato lo aclararía" items={dictamen.datos_que_resolverian} />
      {avisos.length > 0 && (
        <Flash variant="warning" className="mt-4">
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {avisos.map((a, i) => (
              <li key={i} className="flex gap-2">
                <AlertIcon className="mt-0.5 shrink-0" />
                {a}
              </li>
            ))}
          </ul>
        </Flash>
      )}
      <ProcedenciaRespuesta cerebro={comoInformeCerebro(sesion, dictamen)} citations={[]} />
      <CifrasConProcedencia cifras={sesion.expediente?.cifras ?? []} usadas={usadas} />
      <Deliberacion sesion={sesion} dictamen={dictamen} />
      <Anonimizado sesion={sesion} />
      <Coste sesion={sesion} />
      <p className="m-0 mt-2 text-xs text-fg-muted">El dictamen es una ayuda para decidir, no una decisión: nada se hace sin que tú lo confirmes.</p>
    </div>
  );
}

/** The inbox card of a «Dictamen del consejo listo» notification: its text, and the ruling on demand. Like the other
 * notices, it is a row of the inbox's Box, not a box of its own. */
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

  const fallo = error ?? errorSesion;
  return (
    <article aria-label={n.title} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
        <PeopleIcon />
        <span>Consejo del hogar</span>
        {unread && <Label variant="accent">Nuevo</Label>}
        <span className="ml-auto">{relativeTime(Date.parse(n.created_at))}</span>
      </div>
      <h4 className="m-0 text-base font-semibold">{n.title}</h4>
      <RichText text={n.body} />
      <div>
        <Button size="small" onClick={abrir} aria-expanded={abierto} trailingVisual={abierto ? ChevronUpIcon : ChevronDownIcon}>
          {abierto ? "Ocultar el dictamen" : "Ver el dictamen"}
        </Button>
      </div>
      {abierto && (
        <div className="border-t border-border-muted pt-3">
          {(buscando || (!sesion && !fallo)) && <CargandoTexto lineas={3} etiqueta="Cargando el dictamen…" />}
          {fallo && (
            <Flash variant="danger" role="alert">
              {fallo}
            </Flash>
          )}
          {sesion && <DictamenConsejo sesion={sesion} />}
        </div>
      )}
    </article>
  );
}
