// "Navegador": the browser agent. The user picks a task (download the Datadis consumption, prepare a
// procedure in a sede), fills in its data, reads and accepts what the agent will do, and starts it. The
// user logs in THEMSELVES in the browser window (or hands over a session for one run); then the agent's
// steps appear here live, with the latest screenshot. A procedure stops before «Firmar y enviar» and shows
// what would be sent; nothing is ever submitted, signed or paid. «Detener» stops at once; «Cerrar»
// discards the screenshot and the summary.
//
// Migrated to the design system (docs/ui/sistema-de-diseno.md). A running task looks like a GitHub Actions
// run: its state, one row per step with its status icon, and the log as a Timeline. The shell shows it as a
// page (registro.ts: presentacion "pagina"). The live updates come over SSE (useFlujoSse), closed on unmount.

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
import {
  Blankslate,
  Button,
  Caja,
  CargandoLista,
  Checkbox,
  CounterLabel,
  Flash,
  FormControl,
  Label,
  Link,
  Radio,
  RadioGroup,
  Select,
  Spinner,
  StateLabel,
  TextInput,
  Textarea,
  Timeline,
  VisuallyHidden,
} from "../ui";
import {
  AlertIcon,
  CheckCircleFillIcon,
  CircleIcon,
  GlobeIcon,
  ShieldCheckIcon,
  StopIcon,
  XCircleFillIcon,
} from "../ui/iconos";

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

type EstadoLabel = Parameters<typeof StateLabel>[0]["status"];

/** Each task state as a GitHub run: queued or running (amber), done (purple), failed (red), stopped (grey). */
function estadoVisual(estado: string): EstadoLabel {
  if (estado === "preparada") return "draft";
  if (ESTADOS_ACTIVOS.has(estado)) return "pullQueued";
  if (estado === "descargado" || estado === "importado" || estado === "detenido_antes_de_enviar") return "pullMerged";
  if (estado === "fallida") return "pullClosed";
  return "issueClosedNotPlanned";
}

function Estado({ estado, size = "small" }: { estado: string; size?: "small" | "medium" }) {
  return (
    <StateLabel status={estadoVisual(estado)} size={size}>
      {ESTADO[estado] ?? estado}
    </StateLabel>
  );
}

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
  caseId: string | null;
  // Something changed outside the panel (facts imported, a pending action created).
  onChanged?: () => void;
}

export function NavegadorPanel({ open, caseId, onChanged }: NavegadorPanelProps) {
  const [estado, setEstado] = useState<EstadoNavegador | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tareas, setTareas] = useState<TareaInfo[]>([]);
  const [elegida, setElegida] = useState<string | null>(null);
  const [actual, setActual] = useState<string | null>(null);

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

  const guion = estado?.tareas.find((t) => t.clave === elegida) ?? null;
  const seguida = tareas.find((t) => t.id === actual) ?? null;
  const preparadas = tareas.filter((t) => t.estado === "preparada");
  const anteriores = tareas.filter((t) => t.estado !== "preparada" && t.id !== actual);

  return (
    <div className="flex flex-col gap-4">
      {error && (
        <Flash variant="danger" role="alert">
          {error}
        </Flash>
      )}
      {!estado && !error && <CargandoLista filas={2} etiqueta="Cargando el agente con navegador" />}
      {estado && !estado.activo && (
        <Blankslate>
          <Blankslate.Visual>
            <GlobeIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading as="h3">El agente con navegador está desactivado</Blankslate.Heading>
          <Blankslate.Description>Lo activa el operador de este servicio (NAVEGADOR_ACTIVO).</Blankslate.Description>
        </Blankslate>
      )}
      {estado?.activo && !caseId && (
        <Blankslate>
          <Blankslate.Visual>
            <GlobeIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading as="h3">Aún no hay expediente</Blankslate.Heading>
          <Blankslate.Description>Abre o empieza una consulta para tener un expediente.</Blankslate.Description>
        </Blankslate>
      )}
      {estado?.activo && caseId && (
        <>
          {!estado.consentimiento && (
            <Flash variant="warning">
              Para que actúe por ti, activa antes «Agente con navegador» en Módulos: es tu consentimiento, y puedes retirarlo cuando quieras.
            </Flash>
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
          {!seguida &&
            preparadas.map((t) => (
              <Inicio
                key={t.id}
                estado={estado}
                titulo={t.titulo}
                detalle={Object.entries(t.parametros)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join(" · ")}
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
            <Caja as="section" aria-label="¿Qué quieres que haga?">
              <Caja.Cabecera titulo="¿Qué quieres que haga?" />
              <Caja.Lista>
                {estado.tareas.map((g) => (
                  <Caja.Fila key={g.clave} className="flex items-start gap-3">
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <span className="font-semibold">{g.titulo}</span>
                      <span className="text-xs text-fg-muted">{g.descripcion}</span>
                      <span className="flex flex-wrap gap-1">
                        {g.sitios.map((s) => (
                          <Label key={s} variant="secondary">
                            {s}
                          </Label>
                        ))}
                        {g.demostracion && <Label variant="accent">Demostración</Label>}
                        {!g.verificado_contra_sitio_real && <Label variant="attention">Sin probar en el sitio real</Label>}
                      </span>
                    </div>
                    <Button size="small" aria-pressed={g.clave === elegida} onClick={() => setElegida(g.clave === elegida ? null : g.clave)}>
                      {g.clave === elegida ? "Elegida" : "Elegir"}
                      <VisuallyHidden>: {g.titulo}</VisuallyHidden>
                    </Button>
                  </Caja.Fila>
                ))}
              </Caja.Lista>
            </Caja>
          )}
          {!seguida && guion && (
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
          {anteriores.length > 0 && (
            <Caja as="section" aria-label="Tareas recientes" condensada>
              <Caja.Cabecera titulo="Tareas recientes" acciones={<CounterLabel>{anteriores.length}</CounterLabel>} />
              <Caja.Lista>
                {anteriores.map((t) => (
                  <Caja.Fila key={t.id} className="flex flex-wrap items-center gap-2">
                    <Estado estado={t.estado} />
                    <Link as="button" type="button" className="text-left" onClick={() => setActual(t.id)}>
                      {t.titulo}
                    </Link>
                  </Caja.Fila>
                ))}
              </Caja.Lista>
            </Caja>
          )}
          <p className="m-0 text-xs text-fg-muted">
            Tareas usadas en 24 h: {estado.usadas_hoy} de {estado.max_tareas_dia}.
          </p>
        </>
      )}
      <p className="m-0 flex gap-2 text-xs text-fg-muted">
        <ShieldCheckIcon className="shrink-0" />
        <span>
          Tu contraseña la escribes tú en la ventana del navegador: HaciendaGPT no la ve ni la guarda. El agente solo visita los sitios de
          la tarea, no envía formularios, no firma y no paga.
        </span>
      </p>
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
    <div className="flex flex-col gap-3">
      {children}
      {estado.modos.length > 1 && (
        <RadioGroup name="modo">
          <RadioGroup.Label>Cómo inicias sesión</RadioGroup.Label>
          <FormControl>
            <Radio value="ventana" checked={modo === "ventana"} onChange={() => setModo("ventana")} />
            <FormControl.Label>En una ventana del navegador que se abrirá en este equipo (recomendado)</FormControl.Label>
          </FormControl>
          <FormControl>
            <Radio value="sesion" checked={modo === "sesion"} onChange={() => setModo("sesion")} />
            <FormControl.Label>Entregando una sesión ya abierta, solo para esta tarea (avanzado)</FormControl.Label>
          </FormControl>
        </RadioGroup>
      )}
      {modo === "sesion" && (
        <FormControl>
          <FormControl.Label>Sesión («storage_state» de Playwright)</FormControl.Label>
          <Textarea block rows={4} className="font-mono" value={sesion} onChange={(e) => setSesion(e.target.value)} spellCheck={false} />
          <FormControl.Caption>
            Se usa solo en esta tarea, se filtra a los sitios de la tarea y no se guarda. Nunca pegues aquí tu contraseña.
          </FormControl.Caption>
        </FormControl>
      )}
      <div className="flex flex-col gap-2 rounded-medium border border-border-default bg-canvas-muted p-3">
        <p className="m-0 text-xs">{estado.texto_consentimiento}</p>
        <FormControl>
          <Checkbox checked={acepto} onChange={(e) => setAcepto(e.target.checked)} />
          <FormControl.Label>Lo he leído y quiero empezar</FormControl.Label>
        </FormControl>
      </div>
      {error && (
        <Flash variant="danger" role="alert">
          {error}
        </Flash>
      )}
      <div>
        <Button variant="primary" leadingVisual={GlobeIcon} loading={busy} disabled={!acepto || !estado.consentimiento} onClick={() => void empezar()}>
          Empezar
        </Button>
      </div>
    </div>
  );
}

function Inicio({ estado, titulo, detalle, onEmpezar, onDescartar }: InicioProps & { titulo: string; detalle: string; onDescartar: () => Promise<void> }) {
  return (
    <Caja as="section" aria-label={`Preparada en el chat: ${titulo}`} variante="acento">
      <Caja.Cabecera
        titulo={`Preparada en el chat: ${titulo}`}
        acciones={
          <Button size="small" variant="invisible" onClick={() => void onDescartar()}>
            Descartar
          </Button>
        }
      />
      <Caja.Cuerpo className="flex flex-col gap-3">
        {detalle && <p className="m-0 text-xs text-fg-muted">{detalle}</p>}
        <Acuse estado={estado} onEmpezar={onEmpezar} />
      </Caja.Cuerpo>
    </Caja>
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
  const poner = (nombre: string, valor: string) => setValores((v) => ({ ...v, [nombre]: valor }));
  return (
    <Caja as="section" aria-label={guion.titulo}>
      <Caja.Cabecera titulo={guion.titulo} />
      <Caja.Cuerpo className="flex flex-col gap-3">
        {!guion.verificado_contra_sitio_real && (
          <Flash variant="warning">
            <span className="flex gap-2">
              <AlertIcon className="mt-0.5 shrink-0" />
              {guion.nota_verificacion}
            </span>
          </Flash>
        )}
        <Acuse estado={estado} onEmpezar={(inicio) => onEmpezar(valores, inicio)}>
          <div className="grid gap-3 sm:grid-cols-2">
            {guion.parametros.map((p) => (
              <FormControl key={p.nombre} required={p.obligatorio} className={p.tipo === "texto" && p.nombre === "expone" ? "sm:col-span-2" : undefined}>
                <FormControl.Label>{p.etiqueta}</FormControl.Label>
                {p.tipo === "opcion" ? (
                  <Select block value={valores[p.nombre] ?? ""} onChange={(e) => poner(p.nombre, e.target.value)}>
                    <Select.Option value="">Elige…</Select.Option>
                    {p.opciones.map((o) => (
                      <Select.Option key={o} value={o}>
                        {o}
                      </Select.Option>
                    ))}
                  </Select>
                ) : (
                  <TextInput
                    block
                    type={p.tipo === "fecha" ? "date" : "text"}
                    autoComplete="off"
                    value={valores[p.nombre] ?? ""}
                    onChange={(e) => poner(p.nombre, e.target.value)}
                  />
                )}
              </FormControl>
            ))}
          </div>
        </Acuse>
      </Caja.Cuerpo>
    </Caja>
  );
}

/** A step's status icon, as in an Actions run: a spinner while it runs. */
function IconoPaso({ estado, activa }: { estado: string; activa: boolean }) {
  if (estado === "ok" || estado === "reparado") return <CheckCircleFillIcon className="text-fg-success" />;
  if (estado === "en_curso") return activa ? <Spinner size="small" srText={null} /> : <CircleIcon className="text-fg-muted" />;
  return <XCircleFillIcon className="text-fg-danger" />;
}

export function TareaEnVivo({ tarea, onActualizada, onCerrar }: { tarea: TareaInfo; onActualizada: (t: TareaInfo) => void; onCerrar: () => void }) {
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
    <Caja as="section" aria-label="Tarea en curso">
      <Caja.Cabecera
        titulo={tarea.titulo}
        acciones={
          activa ? (
            <Button
              size="small"
              variant="danger"
              leadingVisual={StopIcon}
              onClick={() => detenerTarea(tarea.id).then(onActualizada, (err: unknown) => setError(mensaje(err)))}
            >
              Detener
            </Button>
          ) : (
            <Button size="small" onClick={() => cerrarTarea(tarea.id).then(onCerrar, (err: unknown) => setError(mensaje(err)))}>
              Cerrar y descartar
            </Button>
          )
        }
      />
      <Caja.Cuerpo className="flex flex-col gap-3">
        <div role="status" className="flex items-center gap-2">
          <Estado estado={tarea.estado} size="medium" />
        </div>
        {tarea.estado === "esperando_login" && (
          <Flash>
            {tarea.modo === "ventana"
              ? "Inicia sesión tú en la ventana del navegador que se ha abierto. El agente no ve lo que escribes y no hará nada hasta que hayas entrado."
              : "Comprobando la sesión que has entregado…"}
          </Flash>
        )}
        {(error ?? flujo.error) && (
          <Flash variant="danger" role="alert">
            {error ?? flujo.error}
          </Flash>
        )}
        {tarea.mensaje && <p className="m-0">{tarea.mensaje}</p>}
        {tarea.importacion && (
          <Flash variant={tarea.importacion_error ? "danger" : "success"}>
            <span className="whitespace-pre-line">{tarea.importacion}</span>
          </Flash>
        )}
      </Caja.Cuerpo>
      {tarea.pasos.length > 0 && (
        <Caja.Lista aria-label="Pasos">
          {tarea.pasos.map((p) => (
            <Caja.Fila key={p.id} className="flex items-center gap-2">
              <IconoPaso estado={p.estado} activa={activa} />
              <span>{p.descripcion}</span>
              {p.estado === "reparado" && <span className="text-xs text-fg-muted">(selector revisado)</span>}
            </Caja.Fila>
          ))}
        </Caja.Lista>
      )}
      {(tarea.resumen_envio.length > 0 || imagen || tarea.origenes_bloqueados.length > 0 || ultimos.length > 0) && (
        <Caja.Cuerpo className="flex flex-col gap-3 border-t border-border-default">
          {tarea.resumen_envio.length > 0 && (
            <div>
              <h4 className="m-0 text-sm font-semibold">Lo que se enviaría (no se ha enviado)</h4>
              <dl className="m-0 mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                {tarea.resumen_envio.map((f, i) => (
                  <div key={i} className="contents">
                    <dt className="text-fg-muted">{f.etiqueta}</dt>
                    <dd className="m-0">{f.valor}</dd>
                  </div>
                ))}
              </dl>
              <p className="m-0 mt-2 text-xs text-fg-muted">
                HaciendaGPT no firma ni presenta. Queda una acción pendiente en Módulos con este resumen; para presentarlo, hazlo tú en la sede.
              </p>
            </div>
          )}
          {imagen && (
            <figure className="m-0">
              <img src={imagen} alt="Captura de la página en el navegador del agente" className="w-full rounded-medium border border-border-default" />
              <figcaption className="mt-1 text-xs text-fg-muted">Captura solo para esta tarea: se descarta al cerrarla.</figcaption>
            </figure>
          )}
          {tarea.origenes_bloqueados.length > 0 && (
            <p className="m-0 text-xs text-fg-muted">Bloqueadas peticiones a sitios fuera de la tarea: {tarea.origenes_bloqueados.join(", ")}.</p>
          )}
          {ultimos.length > 0 && (
            <section aria-label="Registro de la tarea">
              <h4 className="m-0 mb-2 text-sm font-semibold">Registro de la tarea</h4>
              <Timeline clipSidebar>
                {ultimos.map((e, i) => (
                  <Timeline.Item key={i} condensed>
                    <Timeline.Badge>
                      <GlobeIcon />
                    </Timeline.Badge>
                    <Timeline.Body>
                      <span className="text-fg-muted">{new Date(e.momento).toLocaleTimeString("es-ES")}</span> · {e.texto}
                    </Timeline.Body>
                  </Timeline.Item>
                ))}
              </Timeline>
            </section>
          )}
        </Caja.Cuerpo>
      )}
    </Caja>
  );
}
