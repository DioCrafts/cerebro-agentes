// «Módulos»: the household areas the assistant can help with beyond taxes (electricity, expiries,
// subscriptions…). The user switches each one on or off, fills in its data with a form built from the
// module's own fact declarations, reads a document with it when it can, and decides on the actions its
// specialist prepared: nothing that acts outside the app runs until the user confirms it here.
//
// Shown as a page (registro.ts: presentacion "pagina") with the layout of GitHub's settings pages: a NavList
// of sections on the left (general, one per module, capabilities, history) and the section on the right.

import type { MouseEvent, ReactNode } from "react";
import { useEffect, useId, useRef, useState } from "react";

import type { CaseState, Fact } from "../api";
import { ApiError, deleteCaseFact, getCase, updateCaseFact } from "../api";
import type { AccionPendiente, CampoPropuesto, HechoInfo, HerramientaInfo, ModuloInfo, ModulosResponse, PropuestaHecho } from "../apiModulos";
import { getPropuestas, readModuleDocument, runModuleTool } from "../apiModulos";
import { textoDeValor } from "../lib/guards";
import { fileToBase64, relativeTime } from "../lib/presentation";
import {
  AvisoError,
  Blankslate,
  Button,
  Caja,
  CargandoLista,
  CounterLabel,
  Flash,
  FormControl,
  Heading,
  Label,
  Link,
  NavList,
  Select,
  TextInput,
  Textarea,
  ToggleSwitch,
  VisuallyHidden,
} from "../ui";
import { CheckIcon, GearIcon, HistoryIcon, HomeIcon, PlugIcon, ShieldIcon, TrashIcon, UploadIcon } from "../ui/iconos";
import { CapacidadesSeccion } from "./CapacidadesSeccion";
import { CorreoSeccion } from "./CorreoSeccion";
import { DialogoEleccion, FilaPeligro, ZonaPeligro } from "./ZonaPeligro";
import { useConfirmar } from "./useConfirmar";

function message(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "No hay conexión con el servicio.";
}

interface ModulosPanelProps {
  /** The page is on screen (capabilities and mail are read again each time it becomes so). */
  open?: boolean;
  modulos: ModulosResponse | null;
  loading: boolean;
  error: string | null;
  // Switch a module; `borrar` also erases its data. Resolves true when saved.
  onToggle: (clave: string, activo: boolean, borrar: boolean) => Promise<boolean>;
  caseId: string | null;
  facts: Fact[];
  // The case after a module fact was saved or erased here.
  onCaseUpdated: (updated: CaseState) => void;
  acciones: AccionPendiente[];
  onDecide: (id: string, confirmar: boolean) => Promise<void>;
  disabled: boolean;
  /** Read the modules and the actions again (the «Reintentar» of an error). */
  onRetry?: () => void;
}

const ESTADO: Record<string, string> = {
  ejecutada: "Hecha",
  fallida: "Falló",
  rechazada: "Rechazada",
  ejecutando: "En curso",
};

const DESGLOSE: Record<string, string> = {
  capital: "Capital",
  intereses: "Intereses",
  amortizaciones_anticipadas: "Amortizaciones anticipadas",
  comisiones_amortizacion_anticipada: "Comisiones de amortización anticipada",
};

type Seccion = "general" | "capacidades" | "historial" | `modulo:${string}`;

/** Back from Google's consent screen (`?correo=`): open on the mail module. */
function seccionInicial(): Seccion {
  try {
    return new URLSearchParams(window.location.search).has("correo") ? "modulo:correo" : "general";
  } catch {
    return "general";
  }
}

export function ModulosPanel(props: ModulosPanelProps) {
  const { open = true, modulos, loading, error, acciones } = props;
  const [seccion, setSeccion] = useState<Seccion>(seccionInicial);
  const nombres = Object.fromEntries((modulos?.modulos ?? []).map((m) => [m.clave, m.nombre]));
  const pendientes = acciones.filter((a) => a.estado === "pendiente");
  const decididas = acciones.filter((a) => a.estado !== "pendiente");
  const lista = modulos?.modulos ?? [];
  const moduloAbierto = seccion.startsWith("modulo:") ? lista.find((m) => `modulo:${m.clave}` === seccion) : undefined;
  // A section that no longer exists (a module the server stopped offering, an empty history) falls back to «General».
  const actual: Seccion =
    (seccion.startsWith("modulo:") && !moduloAbierto && modulos !== null) || (seccion === "historial" && decididas.length === 0) ? "general" : seccion;

  const ir = (s: Seccion) => (e: MouseEvent) => {
    e.preventDefault();
    setSeccion(s);
  };

  return (
    <div className="flex flex-col gap-6 md:flex-row md:items-start">
      <div className="md:w-64 md:shrink-0">
        <NavList aria-label="Secciones de los módulos">
          <NavList.Item href="#modulos-general" aria-current={actual === "general" ? "page" : undefined} onClick={ir("general")}>
            <NavList.LeadingVisual>
              <GearIcon />
            </NavList.LeadingVisual>
            {/* Counters and states inside the label, not as TrailingVisual: Primer would name the item with a
                text axe does not match against what is shown (WCAG 2.5.3). */}
            General
            {pendientes.length > 0 && (
              <span className="ml-1">
                {" "}
                <span aria-hidden>
                  <CounterLabel>{pendientes.length}</CounterLabel>
                </span>
                <VisuallyHidden>{`${pendientes.length} pendientes de tu confirmación`}</VisuallyHidden>
              </span>
            )}
          </NavList.Item>
          {lista.length > 0 && (
            <NavList.Group title="Tus módulos">
              {lista.map((m) => (
                <NavList.Item
                  key={m.clave}
                  href={`#modulo-${m.clave}`}
                  aria-current={actual === `modulo:${m.clave}` ? "page" : undefined}
                  onClick={ir(`modulo:${m.clave}`)}
                >
                  <NavList.LeadingVisual>
                    <HomeIcon />
                  </NavList.LeadingVisual>
                  {m.nombre}
                  {!m.activo && <span className="text-fg-muted"> · desactivado</span>}
                </NavList.Item>
              ))}
            </NavList.Group>
          )}
          {modulos && (
            <NavList.Group title="Más">
              <NavList.Item href="#modulos-capacidades" aria-current={actual === "capacidades" ? "page" : undefined} onClick={ir("capacidades")}>
                <NavList.LeadingVisual>
                  <PlugIcon />
                </NavList.LeadingVisual>
                Capacidades
              </NavList.Item>
              {decididas.length > 0 && (
                <NavList.Item href="#modulos-historial" aria-current={actual === "historial" ? "page" : undefined} onClick={ir("historial")}>
                  <NavList.LeadingVisual>
                    <HistoryIcon />
                  </NavList.LeadingVisual>
                  Acciones ya decididas{" "}
                  <span className="ml-1">
                    <span aria-hidden>
                      <CounterLabel>{decididas.length}</CounterLabel>
                    </span>
                    <VisuallyHidden>{String(decididas.length)}</VisuallyHidden>
                  </span>
                </NavList.Item>
              )}
            </NavList.Group>
          )}
        </NavList>
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-6">
        {error && <AvisoError frase={error} onReintentar={props.onRetry} />}
        {loading && !modulos && <CargandoLista filas={3} etiqueta="Cargando los módulos" />}

        {actual === "general" && (
          <SeccionGeneral {...props} nombres={nombres} pendientes={pendientes} onAbrir={(clave) => setSeccion(`modulo:${clave}`)} />
        )}
        {moduloAbierto && modulos && (
          <SeccionModulo key={moduloAbierto.clave} {...props} open={open} modulo={moduloAbierto} configurable={modulos.configurable} />
        )}
        {actual === "capacidades" && modulos && (
          <section aria-labelledby="modulos-capacidades" className="flex flex-col gap-4">
            <Subcabecera id="modulos-capacidades" titulo="Capacidades" />
            <CapacidadesSeccion nombres={nombres} disabled={props.disabled} activa={open} />
          </section>
        )}
        {actual === "historial" && (
          <section aria-labelledby="modulos-historial" className="flex flex-col gap-4">
            <Subcabecera id="modulos-historial" titulo="Acciones ya decididas" />
            <Caja>
              <Caja.Lista>
                {decididas.map((a) => (
                  <Caja.Fila key={a.id} className="flex flex-col gap-1">
                    <p className="m-0 flex flex-wrap items-center gap-2">
                      <Label variant={a.estado === "ejecutada" ? "success" : a.estado === "fallida" ? "danger" : "secondary"}>
                        {ESTADO[a.estado] ?? a.estado}
                      </Label>
                      <span>{a.resumen}</span>
                    </p>
                    <p className="m-0 text-xs text-fg-muted">
                      {nombres[a.modulo] ?? a.modulo} · {relativeTime(Date.parse(a.created_at))}
                    </p>
                    {a.resultado && <p className={a.es_error ? "m-0 text-fg-danger" : "m-0 text-fg-muted"}>{a.resultado}</p>}
                  </Caja.Fila>
                ))}
              </Caja.Lista>
            </Caja>
          </section>
        )}

        <p className="m-0 text-xs text-fg-muted">
          Cada módulo calcula con datos oficiales y sus propias fuentes. Cerebro nunca suma cifras de módulos distintos si ningún
          módulo ha calculado esa combinación.
        </p>
      </div>
    </div>
  );
}

/** GitHub's «Subhead»: the section title with a rule under it. */
function Subcabecera({ id, titulo, descripcion, acciones }: { id: string; titulo: string; descripcion?: string; acciones?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end gap-3 border-b border-border-default pb-2">
      <div className="min-w-0 flex-1">
        <Heading as="h3" variant="medium" id={id}>
          {titulo}
        </Heading>
        {descripcion && <p className="m-0 mt-1 text-fg-muted">{descripcion}</p>}
      </div>
      {acciones}
    </div>
  );
}

function SeccionGeneral(
  props: ModulosPanelProps & { nombres: Record<string, string>; pendientes: AccionPendiente[]; onAbrir: (clave: string) => void },
) {
  const { modulos, nombres, pendientes, onAbrir } = props;
  return (
    <section aria-labelledby="modulos-general" className="flex flex-col gap-4">
      <Subcabecera id="modulos-general" titulo="General" descripcion="Activa lo que quieres que Cerebro lleve por ti. Cada módulo es una parte de un área: la luz y la hipoteca son de Casa y suministros." />

      {pendientes.length > 0 && (
        <Caja as="section" variante="atencion" aria-label="Pendiente de tu confirmación">
          <Caja.Cabecera
            titulo={
              <span className="inline-flex items-center gap-2">
                <ShieldIcon className="text-fg-attention" />
                Pendiente de tu confirmación
              </span>
            }
            nivel={4}
            acciones={<CounterLabel>{pendientes.length}</CounterLabel>}
          />
          <Caja.Cuerpo className="text-fg-muted">
            Estas acciones actúan fuera de la aplicación. No se ha hecho nada todavía: solo se ejecutan si las confirmas.
          </Caja.Cuerpo>
          <Caja.Lista>
            {pendientes.map((a) => (
              <AccionItem key={a.id} accion={a} modulo={nombres[a.modulo] ?? a.modulo} onDecide={props.onDecide} disabled={props.disabled} />
            ))}
          </Caja.Lista>
        </Caja>
      )}

      {props.caseId && <Propuestas caseId={props.caseId} facts={props.facts} onCaseUpdated={props.onCaseUpdated} disabled={props.disabled} nombres={nombres} />}

      {modulos && !modulos.configurable && <Flash>Sin cuentas de usuario, los módulos los activa el operador del servicio.</Flash>}

      {modulos && modulos.modulos.length > 0 && (
        <Caja as="section" aria-label="Módulos">
          <Caja.Cabecera titulo="Módulos" nivel={4} />
          <Caja.Lista>
            {modulos.modulos.map((m) => (
              <Caja.Fila key={m.clave} className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <h5 className="m-0 text-sm font-semibold">
                    <Link
                      href={`#modulo-${m.clave}`}
                      id={`modulo-nombre-${m.clave}`}
                      onClick={(e: MouseEvent) => {
                        e.preventDefault();
                        onAbrir(m.clave);
                      }}
                    >
                      {m.nombre}
                    </Link>
                  </h5>
                  <p className="m-0 text-fg-muted">{m.descripcion}</p>
                </div>
                <InterruptorModulo modulo={m} configurable={modulos.configurable} etiquetaId={`modulo-nombre-${m.clave}`} {...props} />
              </Caja.Fila>
            ))}
          </Caja.Lista>
        </Caja>
      )}
      {modulos && modulos.modulos.length === 0 && (
        <Blankslate narrow>
          <Blankslate.Visual>
            <HomeIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading as="h4">No hay módulos</Blankslate.Heading>
          <Blankslate.Description>Este servicio todavía no ofrece módulos del hogar.</Blankslate.Description>
        </Blankslate>
      )}
    </section>
  );
}

/**
 * The on/off switch of a module. Switching on is immediate; switching off asks first (its notices and pending
 * actions go away) and, when the module has data in the case, whether to keep it or erase it.
 */
function InterruptorModulo({
  modulo,
  configurable,
  etiquetaId,
  onToggle,
  facts,
  disabled,
}: ModulosPanelProps & { modulo: ModuloInfo; configurable: boolean; etiquetaId: string }) {
  const [preguntando, setPreguntando] = useState(false);
  const [busy, setBusy] = useState(false);
  const conDatos = facts.some((f) => modulo.hechos.some((h) => h.nombre === f.name));

  if (modulo.obligatorio) return <Label variant="secondary">Siempre activo</Label>;

  async function cambiar(activo: boolean, borrar = false) {
    setPreguntando(false);
    setBusy(true);
    await onToggle(modulo.clave, activo, borrar);
    setBusy(false);
  }

  return (
    <>
      <ToggleSwitch
        aria-labelledby={etiquetaId}
        size="small"
        checked={modulo.activo}
        disabled={!configurable || disabled}
        loading={busy}
        loadingLabel="Guardando"
        buttonLabelOn="Activado"
        buttonLabelOff="Desactivado"
        onClick={() => (modulo.activo ? setPreguntando(true) : void cambiar(true))}
      />
      {preguntando && (
        <DialogoEleccion
          titulo={`Desactivar ${modulo.nombre}`}
          onCerrar={() => setPreguntando(false)}
          botones={
            conDatos
              ? [
                  { contenido: "Desactivar y conservar los datos", onClick: () => void cambiar(false) },
                  { contenido: "Desactivar y borrar sus datos", tipo: "danger", onClick: () => void cambiar(false, true) },
                ]
              : [{ contenido: "Desactivar", tipo: "danger", onClick: () => void cambiar(false) }]
          }
        >
          Dejará de usarse en tus consultas y se quitarán sus avisos y sus acciones pendientes.
          {conDatos ? " ¿Quieres borrar también sus datos de tu expediente?" : ""}
        </DialogoEleccion>
      )}
    </>
  );
}

/** Tax facts a module can fill in (e.g. the mortgage payments for the pre-2013 home deduction). */
function Propuestas({
  caseId,
  facts,
  onCaseUpdated,
  disabled,
  nombres,
}: {
  caseId: string;
  facts: Fact[];
  onCaseUpdated: (updated: CaseState) => void;
  disabled: boolean;
  nombres: Record<string, string>;
}) {
  const [items, setItems] = useState<PropuestaHecho[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const factsKey = facts.map((f) => `${f.name}=${textoDeValor(f.value)}`).join("&");
  useEffect(() => {
    const controller = new AbortController();
    getPropuestas(caseId, controller.signal).then(setItems, () => setItems([]));
    return () => controller.abort();
  }, [caseId, factsKey]);

  if (items.length === 0) return null;
  async function accept(p: PropuestaHecho) {
    setBusy(p.nombre);
    setError(null);
    try {
      onCaseUpdated(await updateCaseFact({ caseId, factName: p.nombre, value: p.valor }));
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(null);
    }
  }
  return (
    <Caja as="section" variante="acento" aria-label="Para tu declaración de la renta">
      <Caja.Cabecera titulo="Para tu declaración de la renta" nivel={4} />
      {error && (
        <Caja.Cuerpo>
          <Flash variant="danger">{error}</Flash>
        </Caja.Cuerpo>
      )}
      <Caja.Lista>
        {items.map((p) => (
          <Caja.Fila key={p.nombre} className="flex flex-col gap-1">
            <p className="m-0 text-xs text-fg-muted">Desde {nombres[p.modulo] ?? p.modulo}</p>
            <p className="m-0 font-semibold">{p.titulo}</p>
            <p className="m-0">{p.explicacion}</p>
            {Object.keys(p.desglose).length > 0 && (
              <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 text-xs">
                {Object.entries(p.desglose).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-fg-muted">{DESGLOSE[k] ?? k.replace(/_/g, " ")}</dt>
                    <dd className="m-0 text-right font-semibold">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
            <div className="mt-1">
              <Button size="small" variant="primary" leadingVisual={CheckIcon} disabled={disabled || busy !== null} loading={busy === p.nombre} onClick={() => void accept(p)}>
                Usar esta cifra
              </Button>
            </div>
          </Caja.Fila>
        ))}
      </Caja.Lista>
    </Caja>
  );
}

function AccionItem({ accion, modulo, onDecide, disabled }: { accion: AccionPendiente; modulo: string; onDecide: ModulosPanelProps["onDecide"]; disabled: boolean }) {
  const [busy, setBusy] = useState(false);
  const confirmar = useConfirmar();
  async function decide(si: boolean) {
    setBusy(true);
    await onDecide(accion.id, si);
    setBusy(false);
  }
  return (
    <Caja.Fila className="flex flex-col gap-1">
      <p className="m-0 text-xs text-fg-muted">
        {modulo} · {relativeTime(Date.parse(accion.created_at))}
      </p>
      <p className="m-0">{accion.resumen}</p>
      <div className="mt-1 flex flex-wrap gap-2">
        <Button
          size="small"
          variant="primary"
          disabled={disabled || busy}
          loading={busy}
          onClick={() =>
            void confirmar("¿Ejecutar esta acción ahora?", accion.resumen, "Sí, ejecutarla ahora", "primary").then((si) => {
              if (si) void decide(true);
            })
          }
        >
          Confirmar
        </Button>
        <Button size="small" disabled={disabled || busy} onClick={() => void decide(false)}>
          Rechazar
        </Button>
      </div>
    </Caja.Fila>
  );
}

function SeccionModulo({
  modulo,
  configurable,
  open,
  ...props
}: ModulosPanelProps & { modulo: ModuloInfo; configurable: boolean; open: boolean }) {
  const { onToggle, caseId, facts, onCaseUpdated, disabled } = props;
  const confirmar = useConfirmar();
  const [busy, setBusy] = useState(false);
  const idTitulo = `modulo-${modulo.clave}`;
  const ownFacts = facts.filter((f) => modulo.hechos.some((h) => h.nombre === f.name));
  const actua = modulo.herramientas.some((h) => h.efecto === "actua");
  const editable = configurable && !modulo.obligatorio;

  async function desactivar(borrar: boolean) {
    const si = borrar
      ? await confirmar(
          `¿Desactivar ${modulo.nombre} y borrar sus datos?`,
          "Se borrarán de tu expediente los datos de este módulo, sus avisos y sus acciones pendientes. No se puede deshacer.",
          "Desactivar y borrar",
        )
      : await confirmar(
          `¿Desactivar ${modulo.nombre}?`,
          "Dejará de usarse en tus consultas y se quitarán sus avisos y sus acciones pendientes. Sus datos se conservan.",
          "Desactivar",
        );
    if (!si) return;
    setBusy(true);
    await onToggle(modulo.clave, false, borrar);
    setBusy(false);
  }

  return (
    <section aria-labelledby={idTitulo} className="flex flex-col gap-4">
      <Subcabecera
        id={idTitulo}
        titulo={modulo.nombre}
        descripcion={modulo.descripcion}
        acciones={modulo.obligatorio ? <Label variant="secondary">Siempre activo</Label> : <Label variant={modulo.activo ? "success" : "secondary"}>{modulo.activo ? "Activado" : "Desactivado"}</Label>}
      />

      {!modulo.activo && (
        <Flash className="flex flex-wrap items-center gap-3">
          <span className="flex-1">Este módulo está desactivado: no se usa en tus consultas.</span>
          {editable && (
            <Button size="small" variant="primary" disabled={disabled || busy} loading={busy} onClick={() => void onToggle(modulo.clave, true, false)}>
              Activar
            </Button>
          )}
        </Flash>
      )}

      {modulo.activo && modulo.clave === "fiscal" && <p className="m-0 text-fg-muted">Sus datos se editan en «Tus datos».</p>}
      {modulo.activo && actua && (
        <Flash variant="warning">Este módulo puede preparar acciones fuera de la aplicación; ninguna se ejecuta sin tu confirmación.</Flash>
      )}

      {modulo.activo && modulo.hechos.length > 0 && (
        <Caja as="section" aria-label={`Tus datos de ${modulo.nombre}`}>
          <Caja.Cabecera
            titulo="Tus datos de este módulo"
            nivel={4}
            acciones={
              <CounterLabel aria-label={`${ownFacts.length} de ${modulo.hechos.length} datos`}>
                {ownFacts.length}/{modulo.hechos.length}
              </CounterLabel>
            }
          />
          {!caseId ? (
            <Caja.Cuerpo className="text-fg-muted">Haz una primera consulta para crear tu expediente y poder guardar datos.</Caja.Cuerpo>
          ) : (
            <Caja.Lista>
              {modulo.hechos.map((h) => (
                <Caja.Fila key={h.nombre}>
                  <HechoField hecho={h} caseId={caseId} fact={facts.find((f) => f.name === h.nombre)} onCaseUpdated={onCaseUpdated} disabled={disabled} />
                </Caja.Fila>
              ))}
            </Caja.Lista>
          )}
        </Caja>
      )}

      {modulo.activo &&
        caseId &&
        modulo.herramientas
          .filter((h) => h.acepta_fichero && h.efecto !== "actua")
          .map((h) => <FileTool key={h.nombre} modulo={modulo} herramienta={h} caseId={caseId} onCaseUpdated={onCaseUpdated} disabled={disabled} />)}

      {modulo.activo && modulo.lee_documentos && caseId && <DocumentReader modulo={modulo} caseId={caseId} onCaseUpdated={onCaseUpdated} disabled={disabled} />}

      {modulo.activo && modulo.clave === "correo" && caseId && <CorreoSeccion caseId={caseId} onCaseUpdated={onCaseUpdated} disabled={disabled} activa={open} />}

      {modulo.fuentes.length > 0 && (
        <Caja as="section" aria-label={`Fuentes de ${modulo.nombre}`} condensada>
          <Caja.Cabecera titulo="Fuentes" nivel={4} acciones={<CounterLabel>{modulo.fuentes.length}</CounterLabel>} />
          <Caja.Lista>
            {modulo.fuentes.map((f) => (
              <Caja.Fila key={f.identificador} className="text-xs">
                {f.url.startsWith("https://") ? (
                  <Link href={f.url} target="_blank" rel="noreferrer">
                    {f.descripcion}
                  </Link>
                ) : (
                  f.descripcion
                )}{" "}
                <span className="text-fg-muted">({f.identificador})</span>
              </Caja.Fila>
            ))}
          </Caja.Lista>
        </Caja>
      )}

      {modulo.activo && editable && (
        <ZonaPeligro>
          <FilaPeligro
            titulo="Desactivar este módulo"
            descripcion="Deja de usarse en tus consultas y se quitan sus avisos y sus acciones pendientes. Sus datos se conservan."
            accion={
              <Button variant="danger" disabled={disabled || busy} onClick={() => void desactivar(false)}>
                Desactivar
              </Button>
            }
          />
          {ownFacts.length > 0 && (
            <FilaPeligro
              titulo="Desactivar y borrar sus datos"
              descripcion={`Además, borra de tu expediente ${ownFacts.length === 1 ? "el dato" : `los ${ownFacts.length} datos`} de este módulo. No se puede deshacer.`}
              accion={
                <Button variant="danger" leadingVisual={TrashIcon} disabled={disabled || busy} onClick={() => void desactivar(true)}>
                  Desactivar y borrar
                </Button>
              }
            />
          )}
        </ZonaPeligro>
      )}
    </section>
  );
}

function initialText(hecho: HechoInfo, fact: Fact | undefined): string {
  if (!fact) return "";
  if (hecho.tipo === "json" && typeof fact.value === "string") {
    try {
      return JSON.stringify(JSON.parse(fact.value), null, 2);
    } catch {
      return fact.value;
    }
  }
  return textoDeValor(fact.value);
}

/** One editable row of a module's data: label, explanation, the field, save and delete. */
function HechoField({
  hecho,
  caseId,
  fact,
  onCaseUpdated,
  disabled,
}: {
  hecho: HechoInfo;
  caseId: string;
  fact: Fact | undefined;
  onCaseUpdated: (updated: CaseState) => void;
  disabled: boolean;
}) {
  const [draft, setDraft] = useState(() => initialText(hecho, fact));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const stored = initialText(hecho, fact);

  async function save() {
    setError(null);
    setSaved(false);
    let value: string | number | boolean = draft.trim();
    if (hecho.tipo === "boolean") value = draft === "true";
    if (hecho.tipo === "json") {
      try {
        JSON.parse(draft);
      } catch (err) {
        setError(`No es JSON válido: ${err instanceof Error ? err.message : "revisa la sintaxis"}.`);
        return;
      }
    }
    if (value === "") {
      setError("Escribe un valor o usa «Borrar».");
      return;
    }
    setBusy(true);
    try {
      onCaseUpdated(await updateCaseFact({ caseId, factName: hecho.nombre, value }));
      setSaved(true);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setError(null);
    setBusy(true);
    try {
      onCaseUpdated(await deleteCaseFact({ caseId, factName: hecho.nombre }));
      setDraft("");
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <FormControl>
        <FormControl.Label>
          <span className="inline-flex flex-wrap items-center gap-2">
            {hecho.etiqueta}
            {hecho.sensible && <Label variant="danger">Dato sensible</Label>}
          </span>
        </FormControl.Label>
        {hecho.tipo === "boolean" ? (
          <Select value={draft} onChange={(e) => setDraft(e.target.value)} block validationStatus={error ? "error" : undefined}>
            <Select.Option value="">— sin indicar —</Select.Option>
            <Select.Option value="true">Sí</Select.Option>
            <Select.Option value="false">No</Select.Option>
          </Select>
        ) : hecho.tipo === "json" ? (
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={5}
            spellCheck={false}
            block
            className="font-mono"
            validationStatus={error ? "error" : undefined}
          />
        ) : (
          <TextInput
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            inputMode={hecho.tipo === "number" ? "decimal" : undefined}
            block
            validationStatus={error ? "error" : undefined}
          />
        )}
        <FormControl.Caption>
          {hecho.descripcion}
          {hecho.tipo === "json" ? " (en formato JSON)" : ""}
        </FormControl.Caption>
        {error && <FormControl.Validation variant="error">{error}</FormControl.Validation>}
      </FormControl>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" size="small" variant="primary" leadingVisual={CheckIcon} disabled={busy || disabled || draft === stored} loading={busy}>
          Guardar
        </Button>
        {fact && (
          <Button type="button" size="small" variant="invisible" leadingVisual={TrashIcon} onClick={() => void remove()} disabled={busy || disabled}>
            Borrar
          </Button>
        )}
        {saved && (
          <span role="status" className="text-xs text-fg-success">
            Guardado
          </span>
        )}
      </div>
    </form>
  );
}

// Binary formats travel as base64; everything else as text.
const BINARY_FILE = /\.(xlsx|xls|ods|zip)$/i;

/** A hidden file input opened by a button (the button is what the keyboard and the screen reader reach). */
function SelectorFichero({ etiqueta, accept, disabled, cargando, onFichero }: { etiqueta: string; accept?: string; disabled: boolean; cargando: boolean; onFichero: (f: File) => void }) {
  const ref = useRef<HTMLInputElement | null>(null);
  const id = useId();
  return (
    <>
      <input
        ref={ref}
        id={id}
        type="file"
        accept={accept}
        className="sr-only"
        tabIndex={-1}
        aria-label={etiqueta}
        disabled={disabled}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) onFichero(file);
        }}
      />
      <Button leadingVisual={UploadIcon} disabled={disabled} loading={cargando} onClick={() => ref.current?.click()}>
        {etiqueta}
      </Button>
    </>
  );
}

/** Upload a file straight to a module tool (a bank statement): it never goes to the model or the audit trail. */
function FileTool({
  modulo,
  herramienta,
  caseId,
  onCaseUpdated,
  disabled,
}: {
  modulo: ModuloInfo;
  herramienta: HerramientaInfo;
  caseId: string;
  onCaseUpdated: (updated: CaseState) => void;
  disabled: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resultado, setResultado] = useState<string | null>(null);

  async function send(file: File) {
    setError(null);
    setResultado(null);
    setBusy(true);
    try {
      const argumentos = BINARY_FILE.test(file.name) ? { contenido_base64: await fileToBase64(file) } : { contenido: await file.text() };
      const resp = await runModuleTool({ clave: modulo.clave, herramienta: herramienta.nombre, caseId, argumentos });
      if (resp.resultado?.es_error) setError(resp.resultado.contenido);
      else setResultado(resp.resultado?.contenido ?? null);
      if (resp.case_updated) onCaseUpdated(await getCase({ caseId }));
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Caja as="section" aria-label={`Analizar un fichero: ${herramienta.nombre}`}>
      <Caja.Cabecera titulo="Analizar un fichero" nivel={4} />
      <Caja.Cuerpo className="flex flex-col gap-3">
        <p className="m-0 text-fg-muted">
          {herramienta.descripcion.split(". ")[0]}. El fichero se analiza en el servidor sin pasar por el modelo de lenguaje y no se
          guarda: solo lo que se deduce de él.
        </p>
        <div>
          <SelectorFichero etiqueta={busy ? "Analizando el fichero…" : "Elegir un fichero"} disabled={busy || disabled} cargando={busy} onFichero={(f) => void send(f)} />
        </div>
        {error && (
          <Flash variant="danger" className="whitespace-pre-line">
            {error}
          </Flash>
        )}
        {resultado && (
          <p role="status" className="m-0 max-h-64 overflow-y-auto whitespace-pre-line">
            {resultado}
          </p>
        )}
      </Caja.Cuerpo>
    </Caja>
  );
}

function DocumentReader({
  modulo,
  caseId,
  onCaseUpdated,
  disabled,
}: {
  modulo: ModuloInfo;
  caseId: string;
  onCaseUpdated: (updated: CaseState) => void;
  disabled: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [campos, setCampos] = useState<CampoPropuesto[] | null>(null);
  const [avisos, setAvisos] = useState<string[]>([]);
  const [resultado, setResultado] = useState<string | null>(null);

  async function read(file: File) {
    setError(null);
    setResultado(null);
    setBusy(true);
    try {
      const resp = await readModuleDocument({ clave: modulo.clave, caseId, filename: file.name, contentBase64: await fileToBase64(file) });
      setCampos(resp.propuesta.campos ?? []);
      setAvisos([...(resp.propuesta.avisos ?? []), ...(resp.propuesta.descartados ?? []).map((d) => `Descartado: ${d}`)]);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  async function use() {
    if (!campos || !modulo.herramienta_de_documento) return;
    setError(null);
    setBusy(true);
    try {
      const resp = await runModuleTool({
        clave: modulo.clave,
        herramienta: modulo.herramienta_de_documento,
        caseId,
        argumentos: Object.fromEntries(campos.filter((c) => c.valor.trim()).map((c) => [c.campo, c.valor.trim()])),
      });
      if (resp.resultado) {
        setResultado(resp.resultado.contenido);
        if (resp.resultado.es_error) setError(resp.resultado.contenido);
        else setCampos(null);
      } else if (resp.accion) {
        setResultado("Ha quedado como acción pendiente de tu confirmación.");
      }
      // Facts the tool saved (the panel's case refreshes through the parent).
      if (resp.case_updated) onCaseUpdated(await getCase({ caseId }));
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Caja as="section" aria-label={`Leer un documento con ${modulo.nombre}`}>
      <Caja.Cabecera titulo="Leer un documento" nivel={4} />
      <Caja.Cuerpo className="flex flex-col gap-3">
        <p className="m-0 text-fg-muted">Tique, factura… Se envía al proveedor del modelo de lenguaje para leerlo y no se guarda. Revisa los datos antes de usarlos.</p>
        <div>
          <SelectorFichero
            etiqueta={busy && !campos ? "Leyendo el documento…" : "Elegir un documento"}
            accept=".pdf,.txt,image/*"
            disabled={busy || disabled}
            cargando={busy && !campos}
            onFichero={(f) => void read(f)}
          />
        </div>
        {error && <Flash variant="danger">{error}</Flash>}
        {campos && campos.length === 0 && <p className="m-0 text-fg-muted">No he encontrado datos que pueda respaldar con el texto del documento.</p>}
      </Caja.Cuerpo>
      {campos && campos.length > 0 && (
        <Caja.Lista>
          {campos.map((c, i) => (
            <Caja.Fila key={c.campo}>
              <FormControl>
                <FormControl.Label>{c.campo.replace(/_/g, " ")}</FormControl.Label>
                <TextInput block value={c.valor} onChange={(e) => setCampos((prev) => prev?.map((p, j) => (j === i ? { ...p, valor: e.target.value } : p)) ?? prev)} />
                <FormControl.Caption>En el documento: «{c.evidencia}»</FormControl.Caption>
              </FormControl>
            </Caja.Fila>
          ))}
        </Caja.Lista>
      )}
      {campos && (avisos.length > 0 || (campos.length > 0 && modulo.herramienta_de_documento)) && (
        <Caja.Pie className="flex flex-col gap-2">
          {avisos.length > 0 && (
            <ul className="m-0 list-disc pl-5 text-xs text-fg-muted">
              {avisos.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          )}
          {campos.length > 0 && modulo.herramienta_de_documento && (
            <div>
              <Button variant="primary" leadingVisual={CheckIcon} disabled={busy || disabled} loading={busy} onClick={() => void use()}>
                Usar estos datos
              </Button>
            </div>
          )}
        </Caja.Pie>
      )}
      {resultado && !error && (
        <Caja.Pie>
          <p role="status" className="m-0 whitespace-pre-line">
            {resultado}
          </p>
        </Caja.Pie>
      )}
    </Caja>
  );
}
