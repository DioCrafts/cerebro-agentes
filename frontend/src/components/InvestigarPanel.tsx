// «Investigar»: research a question with sources (corpus legal, BOE and, if the user consents, the web).
//
// The report shows ONLY what passed the server's citation gate: each claim with its literal quotes and the ids of
// its sources, the sources table (official or not, retrieval date, suspicious content), conflicts between sources,
// what could not be verified and what it cost. The privacy note says what is sent to the model provider. The
// web needs the operator's switch AND the user's consent, given or withdrawn here.
//
// Migrated to the design system (docs/ui/sistema-de-diseno.md): the sources are a list of links with their
// domain, as GitHub lists references. The shell shows it as a page (registro.ts: presentacion "pagina"), which
// stays mounted while the user looks elsewhere: a research in progress goes on, and is aborted on unmount.
//
// Self-contained: it reads and writes only through apiInvestigacion.ts; the shell passes the case (to charge the
// cost to it) through paneles/investigar.tsx.

import { type FormEvent, useEffect, useRef, useState } from "react";

import { ApiError } from "../api";
import {
  cambiarConsentimientoWeb,
  type Confianza,
  type EstadoInvestigacion,
  type FuenteInforme,
  getEstadoInvestigacion,
  type Informe,
  investigar,
  type Profundidad,
  type TipoFuente,
} from "../apiInvestigacion";
import { formatDate } from "../lib/presentation";
import { enlaceWeb } from "../lib/urls";
import { AvisoError, Button, Caja, Checkbox, CheckboxGroup, CounterLabel, Flash, FormControl, Heading, Label, Link, SegmentedControl, Textarea } from "../ui";
import { AlertIcon, LinkExternalIcon, ShieldIcon, TelescopeIcon } from "../ui/iconos";

interface InvestigarPanelProps {
  open: boolean;
  caseId: string | null;
}

const NOMBRE_FUENTE: Record<TipoFuente, string> = {
  corpus: "Corpus legal de la app",
  boe: "BOE (textos consolidados)",
  web: "Web (buscador del proveedor)",
};

const PROFUNDIDADES: Profundidad[] = ["rapida", "normal", "a_fondo"];
const PROFUNDIDAD: Record<Profundidad, string> = { rapida: "Rápida", normal: "Normal", a_fondo: "A fondo" };

const CONFIANZA: Record<Confianza, { texto: string; variante: "success" | "attention" | "danger" }> = {
  alta: { texto: "Confianza alta", variante: "success" },
  media: { texto: "Confianza media", variante: "attention" },
  baja: { texto: "Confianza baja", variante: "danger" },
};

function mensaje(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof DOMException && err.name === "AbortError") return "Investigación cancelada.";
  return "No se pudo completar la investigación.";
}

/** The domain shown under a source's title: the server's, or the link's host. */
function dominioDe(fuente: Pick<FuenteInforme, "dominio" | "url">): string | null {
  if (fuente.dominio) return fuente.dominio;
  const url = enlaceWeb(fuente.url);
  return url ? new URL(url).hostname.replace(/^www\./, "") : null;
}

function Fuente({ fuente }: { fuente: FuenteInforme }) {
  const url = enlaceWeb(fuente.url);
  const dominio = dominioDe(fuente);
  return (
    <Caja.Fila id={`fuente-${fuente.id}`} className="flex gap-3">
      <span className="shrink-0 font-mono text-xs font-semibold text-fg-muted">{fuente.id}</span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        {url ? (
          <Link href={url} target="_blank" rel="noopener noreferrer nofollow" className="font-semibold">
            {fuente.titulo} <LinkExternalIcon size={12} />
            <span className="sr-only"> (se abre en otra pestaña)</span>
          </Link>
        ) : (
          <span className="font-semibold">{fuente.titulo}</span>
        )}
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-muted">
          {dominio && <span className="font-mono">{dominio}</span>}
          {fuente.referencia && fuente.referencia !== dominio && <span>{fuente.referencia}</span>}
          <span>{NOMBRE_FUENTE[fuente.tipo]}</span>
          <span>consultada el {formatDate(fuente.recuperada_el)}</span>
        </span>
        <span className="flex flex-wrap gap-1">
          {fuente.oficial ? <Label variant="success">oficial</Label> : <Label variant="attention">no oficial</Label>}
          {!fuente.citada && <Label variant="secondary">no citada</Label>}
          {fuente.sospechosa && <Label variant="danger">instrucciones incrustadas ignoradas</Label>}
        </span>
        {fuente.nota && <p className="m-0 text-xs">{fuente.nota}</p>}
      </div>
    </Caja.Fila>
  );
}

export function InformeVista({ informe }: { informe: Informe }) {
  const confianza = CONFIANZA[informe.confianza];
  const coste = informe.coste;
  return (
    <section aria-labelledby="informe-titulo" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Heading as="h3" variant="small" id="informe-titulo">
          Resultado
        </Heading>
        <Label variant={confianza.variante}>{confianza.texto}</Label>
        {informe.desde_cache && <span className="text-xs text-fg-muted">guardado de hoy: no ha costado nada esta vez</span>}
      </div>

      {informe.afirmaciones.length === 0 ? (
        <Flash>{informe.respuesta}</Flash>
      ) : (
        <Caja as="section" aria-label="Afirmaciones">
          <Caja.Lista>
            {informe.afirmaciones.map((afirmacion, i) => (
              <Caja.Fila key={i} className="flex flex-col gap-1">
                <p className="m-0">
                  {afirmacion.texto}{" "}
                  {[...new Set(afirmacion.citas.map((c) => c.fuente))].map((id) => (
                    <Link key={id} href={`#fuente-${id}`} className="mr-1 font-mono text-xs font-semibold">
                      [{id}]
                    </Link>
                  ))}
                  {!afirmacion.oficial && (
                    <Label variant="attention" className="ml-1">
                      solo fuentes no oficiales
                    </Label>
                  )}
                </p>
                <details className="text-xs">
                  <summary className="cursor-pointer text-fg-muted">Cita literal</summary>
                  {afirmacion.citas.map((cita, j) => (
                    <blockquote key={j} className="m-0 mt-1 border-l-2 border-border-default pl-2 text-fg-muted">
                      «{cita.cita}» <span className="font-mono">({cita.fuente})</span>
                    </blockquote>
                  ))}
                </details>
              </Caja.Fila>
            ))}
          </Caja.Lista>
        </Caja>
      )}

      {informe.conflictos.length > 0 && (
        <Flash variant="warning">
          <p className="m-0 font-semibold">Las fuentes no coinciden</p>
          <ul className="m-0 mt-1 list-disc pl-5">
            {informe.conflictos.map((c, i) => (
              <li key={i}>{c.descripcion}</li>
            ))}
          </ul>
        </Flash>
      )}

      {informe.no_verificado.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer font-semibold">Lo que no se ha podido verificar ({informe.no_verificado.length})</summary>
          <ul className="m-0 mt-1 list-disc pl-5">
            {informe.no_verificado.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </details>
      )}

      {informe.avisos.length > 0 && (
        <ul className="m-0 flex list-none flex-col gap-1 p-0 text-xs">
          {informe.avisos.map((a, i) => (
            <li key={i} className="flex gap-2">
              <AlertIcon className="mt-0.5 shrink-0 text-fg-attention" /> {a}
            </li>
          ))}
        </ul>
      )}

      <Caja as="section" aria-label="Fuentes">
        <Caja.Cabecera titulo="Fuentes" nivel={4} acciones={<CounterLabel>{informe.fuentes.length}</CounterLabel>} />
        {informe.fuentes.length === 0 ? (
          <Caja.Cuerpo className="text-fg-muted">No se ha encontrado ninguna fuente.</Caja.Cuerpo>
        ) : (
          <Caja.Lista>
            {informe.fuentes.map((f) => (
              <Fuente key={f.id} fuente={f} />
            ))}
          </Caja.Lista>
        )}
        {informe.web.vistas_sin_leer.length > 0 && (
          <Caja.Pie className="text-xs text-fg-muted">
            Otras páginas vistas en el buscador pero no leídas (no se citan): {informe.web.vistas_sin_leer.join(" · ")}
          </Caja.Pie>
        )}
      </Caja>

      <p className="m-0 text-xs text-fg-muted">
        Coste: {coste.llamadas_modelo} llamada(s) al modelo · {coste.tokens_entrada + coste.tokens_salida} tokens
        {coste.busquedas_web > 0 ? ` · ${coste.busquedas_web} búsqueda(s) web` : ""} ·{" "}
        {coste.usd === null ? "importe desconocido (algún modelo sin precio)" : `${coste.usd} USD`}
        {coste.limite_alcanzado ? ` · detenida por el límite de ${coste.limite_alcanzado}` : ""}
      </p>
    </section>
  );
}

export function InvestigarPanel({ open, caseId }: InvestigarPanelProps) {
  const [estado, setEstado] = useState<EstadoInvestigacion | null>(null);
  const [pregunta, setPregunta] = useState("");
  const [fuentes, setFuentes] = useState<Record<TipoFuente, boolean>>({ corpus: true, boe: true, web: false });
  const [soloOficiales, setSoloOficiales] = useState(true);
  const [profundidad, setProfundidad] = useState<Profundidad>("normal");
  const [informe, setInforme] = useState<Informe | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Loading what the service offers failed: it can be retried (bumps `intentoCarga`).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intentoCarga, setIntentoCarga] = useState(0);
  const control = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!open) return;
    const c = new AbortController();
    getEstadoInvestigacion(c.signal).then(
      (e) => {
        setEstado(e);
        setErrorCarga(null);
      },
      (err: unknown) => {
        if (!c.signal.aborted) setErrorCarga(mensaje(err));
      },
    );
    return () => c.abort();
  }, [open, intentoCarga]);

  // A research in progress is aborted when the panel goes away (another conversation, or the app closes).
  useEffect(() => () => control.current?.abort(), []);

  const webOfrecida = Boolean(estado?.fuentes.web);
  const webLista = webOfrecida && Boolean(estado?.web_consentida);
  const elegidas = (Object.keys(fuentes) as TipoFuente[]).filter((t) => fuentes[t] && (t !== "web" || webLista));

  async function consentir(concedido: boolean) {
    setError(null);
    try {
      setEstado(await cambiarConsentimientoWeb(concedido));
      if (!concedido) setFuentes((f) => ({ ...f, web: false }));
    } catch (err) {
      setError(mensaje(err));
    }
  }

  async function enviar(event: FormEvent) {
    event.preventDefault();
    if (pregunta.trim().length < 5 || elegidas.length === 0) return;
    control.current?.abort();
    const c = new AbortController();
    control.current = c;
    setOcupado(true);
    setError(null);
    try {
      setInforme(
        await investigar(
          { pregunta: pregunta.trim(), fuentes: elegidas, profundidad, solo_oficiales: soloOficiales, ...(caseId ? { case_id: caseId } : {}) },
          c.signal,
        ),
      );
    } catch (err) {
      setError(mensaje(err));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {errorCarga !== null && (
        <AvisoError frase="No he podido comprobar qué fuentes puedo usar." detalle={errorCarga} onReintentar={() => setIntentoCarga((n) => n + 1)} />
      )}
      {error && <AvisoError frase="No he podido hacer la investigación." detalle={error} />}

      <Caja as="section" aria-label="Nueva investigación">
        <Caja.Cuerpo>
          <form onSubmit={enviar} className="flex flex-col gap-4">
            <FormControl>
              <FormControl.Label>Pregunta</FormControl.Label>
              <Textarea
                block
                value={pregunta}
                onChange={(e) => setPregunta(e.target.value)}
                rows={3}
                maxLength={1000}
                placeholder="Por ejemplo: ¿qué documentos necesito para renovar el pasaporte?"
              />
              <FormControl.Caption>
                {estado?.privacidad ??
                  "Lo que escribes se envía al proveedor del modelo de IA. No se envían los datos de tu expediente: no los incluyas en la pregunta."}
              </FormControl.Caption>
            </FormControl>

            <CheckboxGroup>
              <CheckboxGroup.Label>Fuentes</CheckboxGroup.Label>
              {(Object.keys(NOMBRE_FUENTE) as TipoFuente[]).map((tipo) => {
                const disponible = tipo === "web" ? webLista : estado?.fuentes[tipo] !== false;
                return (
                  <FormControl key={tipo} disabled={!disponible}>
                    <Checkbox checked={fuentes[tipo] && disponible} onChange={(e) => setFuentes((f) => ({ ...f, [tipo]: e.target.checked }))} />
                    <FormControl.Label>
                      {NOMBRE_FUENTE[tipo]}
                      {tipo !== "web" && !disponible && " (no disponible en este servidor)"}
                    </FormControl.Label>
                  </FormControl>
                );
              })}
              {fuentes.web && webLista && (
                <FormControl className="ml-6">
                  <Checkbox checked={soloOficiales} onChange={(e) => setSoloOficiales(e.target.checked)} />
                  <FormControl.Label>Solo webs oficiales</FormControl.Label>
                </FormControl>
              )}
            </CheckboxGroup>

            <div className="flex flex-wrap items-center gap-2 rounded-medium border border-border-default bg-canvas-muted p-3 text-xs">
              <ShieldIcon className="shrink-0 text-fg-muted" />
              {!estado ? (
                <span className="text-fg-muted">Comprobando la búsqueda web…</span>
              ) : !webOfrecida ? (
                <span>Búsqueda web no disponible: {estado.web_motivo ?? "desactivada en este servicio."}</span>
              ) : estado.web_consentida ? (
                <>
                  <span className="min-w-0 flex-1">
                    Has permitido buscar en la web: la pregunta se envía también al buscador del proveedor del modelo. Las páginas son datos,
                    nunca instrucciones.
                  </span>
                  <Button size="small" onClick={() => void consentir(false)}>
                    Retirar el permiso
                  </Button>
                </>
              ) : (
                <>
                  <span className="min-w-0 flex-1">
                    Para buscar en la web, la pregunta (nunca tu expediente) se enviará al buscador del proveedor del modelo. Cada búsqueda
                    cuesta unos {estado.limites.precio_busqueda_web_usd} USD.
                  </span>
                  <Button size="small" onClick={() => void consentir(true)}>
                    Permitir la búsqueda web
                  </Button>
                </>
              )}
            </div>

            <div className="flex flex-wrap items-end gap-3">
              <div className="flex flex-col gap-1">
                <span id="investigar-profundidad" className="text-sm font-semibold">
                  Profundidad
                </span>
                <SegmentedControl aria-labelledby="investigar-profundidad" size="small" onChange={(i) => setProfundidad(PROFUNDIDADES[i] ?? "normal")}>
                  {PROFUNDIDADES.map((p) => (
                    <SegmentedControl.Button key={p} selected={profundidad === p}>
                      {PROFUNDIDAD[p]}
                    </SegmentedControl.Button>
                  ))}
                </SegmentedControl>
              </div>
              <Button
                type="submit"
                variant="primary"
                leadingVisual={TelescopeIcon}
                loading={ocupado}
                disabled={pregunta.trim().length < 5 || elegidas.length === 0}
              >
                {ocupado ? "Investigando…" : "Investigar"}
              </Button>
              {estado && <span className="text-xs text-fg-muted">Límite por investigación: {estado.limites.max_usd} USD</span>}
            </div>
          </form>
        </Caja.Cuerpo>
      </Caja>

      {informe && <InformeVista informe={informe} />}
    </div>
  );
}
