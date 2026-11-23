// "Navegador": the browser agent. The user picks a task (download the Datadis consumption, prepare a
// procedure in a sede), fills in its data, reads and accepts what the agent will do, and starts it. The
// user logs in THEMSELVES in the browser window (or hands over a session for one run); then the agent's
// steps appear here live, with the latest screenshot. A procedure stops before «Firmar y enviar» and shows
// what would be sent; nothing is ever submitted, signed or paid. «Detener» stops at once; «Cerrar»
// discards the screenshot and the summary.

import { AlertTriangle, CheckCircle2, Circle, Globe, Loader2, Octagon, ShieldCheck, X, XCircle } from "lucide-react";
import clsx from "clsx";
import type { ReactNode } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api";
import type { EstadoNavegador, GuionInfo, ModoNavegador, TareaInfo } from "../apiNavegador";
import {
  ESTADOS_ACTIVOS,
  cerrarTarea,
  crearTarea,
  detenerTarea,
  getCaptura,
  getEstadoNavegador,
  getTareas,
  iniciarTarea,
  isTarea,
  rutaEventosTarea,
} from "../apiNavegador";
import { useFlujoSse } from "../shell/useFlujoSse";

const FIELD =
  "w-full rounded-lg border border-line bg-paper px-2.5 py-1.5 text-[16px] text-ink focus:border-primary focus:outline-hidden sm:text-[14px]";
const PRIMARY =
  "inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[13px] font-semibold text-white transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50";
const QUIET =
  "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[13px] font-semibold text-ink-soft hover:bg-line/60 disabled:cursor-not-allowed disabled:opacity-50";

const ESTADO: Record<string, string> = {
  preparada: "Preparada",
  en_curso: "Abriendo el navegador",
  esperando_login: "Esperando a que inicies sesión",
  trabajando: "Trabajando",
  descargado: "Descargado",
  importado: "Importado",
  detenido_antes_de_enviar: "Preparado: detenido antes de enviar",
  fallida: "No se ha completado",
  cancelada: "Detenida",
  login_no_completado: "Sin inicio de sesión",
  cerrada: "Cerrada",
};

async function cargar(caseId: string | null, signal?: AbortSignal): Promise<{ estado: EstadoNavegador; tareas: TareaInfo[] }> {
  const estado = await getEstadoNavegador(signal);
  const tareas = estado.activo && caseId ? await getTareas(caseId, signal) : [];
  return { estado, tareas };
}

function mensaje(err: unknown): string {
  return err instanceof ApiError ? err.message : "No hay conexión con el servicio.";
}

interface NavegadorPanelProps {
  open: boolean;
  onClose: () => void;
  caseId: string | null;
  // Something changed outside the panel (facts imported, a pending action created).
  onChanged?: () => void;
}

export function NavegadorPanel({ open, onClose, caseId, onChanged }: NavegadorPanelProps) {
  const [estado, setEstado] = useState<EstadoNavegador | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tareas, setTareas] = useState<TareaInfo[]>([]);
  const [elegida, setElegida] = useState<string | null>(null);
  const [actual, setActual] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const aplicar = useCallback((r: { estado: EstadoNavegador; tareas: TareaInfo[] }) => {
    setEstado(r.estado);
    setTareas(r.tareas);
    setError(null);
  }, []);

  const recargar = useCallback(() => {
    cargar(caseId).then(aplicar, (err: unknown) => setError(mensaje(err)));
  }, [caseId, aplicar]);

  useEffect(() => {
    if (!open) return;
    const ctrl = new AbortController();
    cargar(caseId, ctrl.signal).then(aplicar, (err: unknown) => {
      if (!ctrl.signal.aborted) setError(mensaje(err));
    });
    return () => ctrl.abort();
  }, [open, caseId, aplicar]);

  if (!open) return null;
  const guion = estado?.tareas.find((t) => t.clave === elegida) ?? null;
  const seguida = tareas.find((t) => t.id === actual) ?? null;
  const preparadas = tareas.filter((t) => t.estado === "preparada");
  const anteriores = tareas.filter((t) => t.estado !== "preparada" && t.id !== actual);
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button type="button" aria-label="Cerrar el navegador" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-label="Agente con navegador" className="relative flex h-full w-full max-w-[560px] flex-col border-l border-line bg-paper shadow-pop">
        <header className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <h2 className="flex items-center gap-2 font-serif text-[19px] font-bold text-ink">
              <Globe size={18} aria-hidden className="text-primary-dark" />
              Agente con navegador
            </h2>
            <p className="text-[12.5px] text-muted">Actúa por ti en webs oficiales, con tu sesión y bajo tu control</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Cerrar" className="rounded-lg p-2 text-muted hover:bg-line/60 hover:text-ink">
            <X size={18} />
          </button>
        </header>
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {error && (
            <p role="alert" className="rounded-lg border border-risk-high-fg/30 bg-risk-high-bg px-3 py-2 text-[13px] text-ink-soft">
              {error}
            </p>
          )}
          {!estado && !error && <p className="text-[13.5px] text-muted">Cargando…</p>}
          {estado && !estado.activo && (
            <p className="rounded-lg bg-line/50 px-3 py-2 text-[13px] leading-snug text-ink-soft">
              El agente con navegador está desactivado en este servicio. Lo activa el operador (NAVEGADOR_ACTIVO).
            </p>
          )}
          {estado?.activo && !caseId && (
            <p className="rounded-lg bg-line/50 px-3 py-2 text-[13px] text-ink-soft">Abre o empieza una consulta para tener un expediente.</p>
          )}
          {estado?.activo && caseId && (
            <>
              {!estado.consentimiento && (
                <p className="rounded-lg border border-risk-high-fg/30 bg-risk-high-bg px-3 py-2 text-[13px] leading-snug text-ink-soft">
                  Para que actúe por ti, activa antes «Agente con navegador» en Módulos: es tu consentimiento, y puedes retirarlo cuando quieras.
                </p>
              )}
              {seguida && (
                <TareaEnVivo
                  key={seguida.id}
                  tarea={seguida}
                  onActualizada={(t) => {
                    setTareas((prev) => prev.map((x) => (x.id === t.id ? t : x)));
                    if (!ESTADOS_ACTIVOS.has(t.estado)) onChanged?.();
                  }}
                  onCerrar={() => {
                    setActual(null);
                    recargar();
                  }}
                />
              )}
              {!seguida && preparadas.map((t) => (
                <Inicio
                  key={t.id}
                  estado={estado}
                  titulo={t.titulo}
                  detalle={Object.entries(t.parametros).map(([k, v]) => `${k}: ${v}`).join(" · ")}
                  onEmpezar={async (inicio) => {
                    const empezada = await iniciarTarea(t.id, inicio);
                    setTareas((prev) => prev.map((x) => (x.id === t.id ? empezada : x)));
                    setActual(t.id);
                  }}
                  onDescartar={async () => {
                    await cerrarTarea(t.id);
                    recargar();
                  }}
                />
              ))}
              {!seguida && (
                <section aria-labelledby="nav-tareas" className="space-y-3">
                  <h3 id="nav-tareas" className="text-[14px] font-bold text-ink">¿Qué quieres que haga?</h3>
                  {estado.tareas.map((g) => (
                    <button
                      key={g.clave}
                      type="button"
                      onClick={() => setElegida(g.clave === elegida ? null : g.clave)}
                      aria-pressed={g.clave === elegida}
                      className={clsx(
                        "w-full rounded-xl border bg-surface px-4 py-3 text-left shadow-card transition",
                        g.clave === elegida ? "border-primary" : "border-line hover:border-primary/60",
                      )}
                    >
                      <span className="block text-[14px] font-semibold text-ink">{g.titulo}</span>
                      <span className="mt-0.5 block text-[12.5px] leading-snug text-muted">{g.descripcion}</span>
                      <span className="mt-1.5 flex flex-wrap gap-1.5 text-[11.5px]">
                        {g.sitios.map((s) => (
                          <span key={s} className="rounded-pill bg-line/60 px-2 py-0.5 text-ink-soft">{s}</span>
                        ))}
                        {g.demostracion && <span className="rounded-pill bg-primary-soft px-2 py-0.5 text-primary-dark">Demostración</span>}
                        {!g.verificado_contra_sitio_real && (
                          <span className="rounded-pill bg-risk-high-bg px-2 py-0.5 text-risk-high-fg">Sin probar en el sitio real</span>
                        )}
                      </span>
                    </button>
                  ))}
                  {guion && (
                    <NuevaTarea
                      key={guion.clave}
                      guion={guion}
                      estado={estado}
                      onEmpezar={async (parametros, inicio) => {
                        const t = await crearTarea(caseId, guion.clave, parametros, inicio);
                        setTareas((prev) => [t, ...prev]);
                        setActual(t.id);
                        setElegida(null);
                      }}
                    />
                  )}
                </section>
              )}
              {anteriores.length > 0 && (
                <details className="rounded-xl border border-line bg-surface px-4 py-3">
                  <summary className="cursor-pointer text-[13.5px] font-semibold text-ink-soft">Tareas recientes ({anteriores.length})</summary>
                  <ul className="mt-2 space-y-1.5 text-[13px]">
                    {anteriores.map((t) => (
                      <li key={t.id}>
                        <button type="button" className="text-left text-ink underline-offset-2 hover:underline" onClick={() => setActual(t.id)}>
                          {t.titulo}
                        </button>{" "}
                        <span className="text-muted">· {ESTADO[t.estado] ?? t.estado}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <p className="text-[11.5px] text-muted">
                Tareas usadas en 24 h: {estado.usadas_hoy} de {estado.max_tareas_dia}.
              </p>
            </>
          )}
        </div>
        <footer className="border-t border-line px-5 py-3 text-[11.5px] leading-relaxed text-muted">
          <ShieldCheck size={13} aria-hidden className="mr-1 inline text-primary-dark" />
          Tu contraseña la escribes tú en la ventana del navegador: HaciendaGPT no la ve ni la guarda. El agente solo visita los sitios de la
          tarea, no envía formularios, no firma y no paga.
        </footer>
      </aside>
    </div>
  );
}

interface InicioProps {
  estado: EstadoNavegador;
  onEmpezar: (inicio: { modo: ModoNavegador; acepto: boolean; texto_version: string; sesion?: Record<string, unknown> | null }) => Promise<void>;
}

function Acuse({ estado, onEmpezar, children }: InicioProps & { children?: ReactNode }) {
  const [modo, setModo] = useState<ModoNavegador>(estado.modos[0] ?? "ventana");
  const [sesion, setSesion] = useState("");
  const [acepto, setAcepto] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const empezar = async () => {
    setError(null);
    let sesionJson: Record<string, unknown> | null = null;
    if (modo === "sesion") {
      try {
        sesionJson = JSON.parse(sesion) as Record<string, unknown>;
      } catch {
        setError("La sesión debe ser el JSON «storage_state» de tu navegador.");
        return;
      }
    }
    setBusy(true);
    try {
      await onEmpezar({ modo, acepto, texto_version: estado.texto_version, sesion: sesionJson });
      setSesion("");
    } catch (err) {
      setError(mensaje(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-3">
      {children}
      {estado.modos.length > 1 && (
        <fieldset className="space-y-1 text-[13px] text-ink-soft">
          <legend className="mb-1 font-semibold text-ink">Cómo inicias sesión</legend>
          <label className="flex items-start gap-2">
            <input type="radio" name="modo" checked={modo === "ventana"} onChange={() => setModo("ventana")} />
            <span>En una ventana del navegador que se abrirá en este equipo (recomendado).</span>
          </label>
          <label className="flex items-start gap-2">
            <input type="radio" name="modo" checked={modo === "sesion"} onChange={() => setModo("sesion")} />
            <span>Entregando una sesión ya abierta, solo para esta tarea (avanzado).</span>
          </label>
        </fieldset>
      )}
      {modo === "sesion" && (
        <label className="block text-[12.5px] text-ink-soft">
          Sesión («storage_state» de Playwright). Se usa solo en esta tarea, se filtra a los sitios de la tarea y no se guarda. Nunca pegues aquí tu contraseña.
          <textarea className={clsx(FIELD, "mt-1 h-20 font-mono text-[12px]")} value={sesion} onChange={(e) => setSesion(e.target.value)} spellCheck={false} />
        </label>
      )}
      <div className="rounded-lg border border-line bg-paper px-3 py-2 text-[12.5px] leading-snug text-ink-soft">
        <p>{estado.texto_consentimiento}</p>
        <label className="mt-2 flex items-start gap-2 font-semibold text-ink">
          <input type="checkbox" checked={acepto} onChange={(e) => setAcepto(e.target.checked)} />
          <span>Lo he leído y quiero empezar</span>
        </label>
      </div>
      {error && <p role="alert" className="text-[12.5px] text-risk-high-fg">{error}</p>}
      <button type="button" className={PRIMARY} disabled={!acepto || busy || !estado.consentimiento} onClick={() => void empezar()}>
        {busy ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <Globe size={14} aria-hidden />}
        Empezar
      </button>
    </div>
  );
}

function Inicio({ estado, titulo, detalle, onEmpezar, onDescartar }: InicioProps & { titulo: string; detalle: string; onDescartar: () => Promise<void> }) {
  return (
    <section className="rounded-xl border border-primary/50 bg-surface px-4 py-3.5 shadow-card">
      <h3 className="text-[14px] font-bold text-ink">Preparada en el chat: {titulo}</h3>
      <p className="mb-3 text-[12.5px] text-muted">{detalle}</p>
      <Acuse estado={estado} onEmpezar={onEmpezar} />
      <button type="button" className={clsx(QUIET, "mt-2")} onClick={() => void onDescartar()}>
        Descartar
      </button>
    </section>
  );
}

function NuevaTarea({
  guion,
  estado,
  onEmpezar,
}: {
  guion: GuionInfo;
  estado: EstadoNavegador;
  onEmpezar: (parametros: Record<string, string>, inicio: Parameters<InicioProps["onEmpezar"]>[0]) => Promise<void>;
}) {
  const [valores, setValores] = useState<Record<string, string>>({});
  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-3.5 shadow-card">
      {!guion.verificado_contra_sitio_real && (
        <p className="mb-3 flex gap-2 rounded-lg bg-risk-high-bg px-3 py-2 text-[12px] leading-snug text-ink-soft">
          <AlertTriangle size={14} aria-hidden className="mt-0.5 shrink-0 text-risk-high-fg" />
          {guion.nota_verificacion}
        </p>
      )}
      <Acuse estado={estado} onEmpezar={(inicio) => onEmpezar(valores, inicio)}>
        <div className="grid gap-2 sm:grid-cols-2">
          {guion.parametros.map((p) => (
            <label key={p.nombre} className={clsx("block text-[12.5px] font-semibold text-ink-soft", p.tipo === "texto" && p.nombre === "expone" && "sm:col-span-2")}>
              {p.etiqueta}
              {!p.obligatorio && <span className="font-normal text-muted"> (opcional)</span>}
              {p.tipo === "opcion" ? (
                <select className={clsx(FIELD, "mt-1")} value={valores[p.nombre] ?? ""} onChange={(e) => setValores({ ...valores, [p.nombre]: e.target.value })}>
                  <option value="">Elige…</option>
                  {p.opciones.map((o) => (
                    <option key={o} value={o}>{o}</option>
                  ))}
                </select>
              ) : (
                <input
                  className={clsx(FIELD, "mt-1")}
                  type={p.tipo === "fecha" ? "date" : "text"}
                  autoComplete="off"
                  value={valores[p.nombre] ?? ""}
                  onChange={(e) => setValores({ ...valores, [p.nombre]: e.target.value })}
                />
              )}
            </label>
          ))}
        </div>
      </Acuse>
    </div>
  );
}

function TareaEnVivo({ tarea, onActualizada, onCerrar }: { tarea: TareaInfo; onActualizada: (t: TareaInfo) => void; onCerrar: () => void }) {
  const [captura, setCaptura] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const activa = ESTADOS_ACTIVOS.has(tarea.estado);
  // While it runs, the server pushes each change of the task (no polling); the stream closes once it
  // stops, and on unmount.
  const flujo = useFlujoSse(activa ? rutaEventosTarea(tarea.id) : null, "tarea", isTarea);
  // The parent's callback changes on every render; the stream reads the latest one through a ref.
  const avisar = useRef(onActualizada);
  useEffect(() => {
    avisar.current = onActualizada;
  }, [onActualizada]);
  useEffect(() => {
    if (flujo.datos) avisar.current(flujo.datos);
  }, [flujo.datos]);

  useEffect(() => {
    if (!tarea.tiene_captura) return;
    const ctrl = new AbortController();
    getCaptura(tarea.id, ctrl.signal).then(setCaptura, () => undefined);
    return () => ctrl.abort();
  }, [tarea.id, tarea.tiene_captura, tarea.actualizada]);
  const imagen = tarea.tiene_captura ? captura : null;

  const ultimos = tarea.eventos.slice(-6).reverse();
  return (
    <section aria-label="Tarea en curso" className="space-y-3 rounded-xl border border-line bg-surface px-4 py-3.5 shadow-card">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-[14.5px] font-bold text-ink">{tarea.titulo}</h3>
          <p className={clsx("text-[12.5px] font-semibold", tarea.estado === "fallida" ? "text-risk-high-fg" : "text-primary-dark")} role="status">
            {activa && <Loader2 size={12} className="mr-1 inline animate-spin" aria-hidden />}
            {ESTADO[tarea.estado] ?? tarea.estado}
          </p>
        </div>
        {activa ? (
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg bg-risk-high-fg px-3 py-1.5 text-[13px] font-semibold text-white hover:opacity-90"
            onClick={() => detenerTarea(tarea.id).then(onActualizada, (err) => setError(mensaje(err)))}
          >
            <Octagon size={14} aria-hidden /> Detener
          </button>
        ) : (
          <button type="button" className={QUIET} onClick={() => cerrarTarea(tarea.id).then(onCerrar, (err) => setError(mensaje(err)))}>
            Cerrar y descartar
          </button>
        )}
      </div>
      {tarea.estado === "esperando_login" && (
        <p className="rounded-lg bg-primary-soft px-3 py-2 text-[13px] leading-snug text-primary-dark">
          {tarea.modo === "ventana"
            ? "Inicia sesión tú en la ventana del navegador que se ha abierto. El agente no ve lo que escribes y no hará nada hasta que hayas entrado."
            : "Comprobando la sesión que has entregado…"}
        </p>
      )}
      {(error ?? flujo.error) && <p role="alert" className="text-[12.5px] text-risk-high-fg">{error ?? flujo.error}</p>}
      {tarea.pasos.length > 0 && (
        <ol className="space-y-1 text-[13px]">
          {tarea.pasos.map((p) => (
            <li key={p.id} className="flex items-center gap-2">
              {p.estado === "ok" || p.estado === "reparado" ? (
                <CheckCircle2 size={14} aria-hidden className="text-primary-dark" />
              ) : p.estado === "en_curso" ? (
                activa ? <Loader2 size={14} aria-hidden className="animate-spin text-muted" /> : <Circle size={14} aria-hidden className="text-muted" />
              ) : (
                <XCircle size={14} aria-hidden className="text-risk-high-fg" />
              )}
              <span className="text-ink-soft">{p.descripcion}</span>
              {p.estado === "reparado" && <span className="text-[11px] text-muted">(selector revisado)</span>}
            </li>
          ))}
        </ol>
      )}
      {tarea.mensaje && <p className="text-[13px] leading-snug text-ink">{tarea.mensaje}</p>}
      {tarea.importacion && (
        <p className={clsx("whitespace-pre-line rounded-lg px-3 py-2 text-[12.5px] leading-snug", tarea.importacion_error ? "bg-risk-high-bg text-ink-soft" : "bg-line/50 text-ink-soft")}>
          {tarea.importacion}
        </p>
      )}
      {tarea.resumen_envio.length > 0 && (
        <div>
          <h4 className="text-[13px] font-bold text-ink">Lo que se enviaría (no se ha enviado)</h4>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[12.5px]">
            {tarea.resumen_envio.map((f, i) => (
              <div key={i} className="contents">
                <dt className="text-muted">{f.etiqueta}</dt>
                <dd className="text-ink">{f.valor}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-[12px] leading-snug text-muted">
            HaciendaGPT no firma ni presenta. Queda una acción pendiente en Módulos con este resumen; para presentarlo, hazlo tú en la sede.
          </p>
        </div>
      )}
      {imagen && (
        <figure>
          <img src={imagen} alt="Captura de la página en el navegador del agente" className="w-full rounded-lg border border-line" />
          <figcaption className="mt-1 text-[11px] text-muted">Captura solo para esta tarea: se descarta al cerrarla.</figcaption>
        </figure>
      )}
      {tarea.origenes_bloqueados.length > 0 && (
        <p className="text-[11.5px] text-muted">Bloqueadas peticiones a sitios fuera de la tarea: {tarea.origenes_bloqueados.join(", ")}.</p>
      )}
      {ultimos.length > 0 && (
        <details className="text-[12px] text-muted">
          <summary className="cursor-pointer">Registro de la tarea</summary>
          <ul className="mt-1 space-y-0.5">
            {ultimos.map((e, i) => (
              <li key={i}>{new Date(e.momento).toLocaleTimeString("es-ES")} · {e.texto}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
