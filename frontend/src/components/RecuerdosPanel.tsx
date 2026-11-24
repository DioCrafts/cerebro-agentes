// «Lo que sé de ti»: what the assistant remembers from earlier conversations (kept by Hindsight), grouped by kind,
// each fact with the conversation and the date it came from. The user can correct a fact (their wording is kept as
// a message of theirs and the old fact stops being used), forget one (the message it came from goes, with everything
// learnt from it) or everything, pause the memory, and see or delete the earlier conversations with their messages.
//
// Shown in a side sheet (registro.ts: presentacion "dialogo"); `open` only triggers reading it all again.

import { useCallback, useEffect, useId, useState } from "react";

import {
  TIPOS_RECUERDO,
  TIPO_RECUERDO_LABEL,
  borrarConversacion,
  cambiarInterruptor,
  corregirRecuerdo,
  getConversacion,
  getConversaciones,
  getRecuerdos,
  olvidarRecuerdo,
  olvidarTodo,
  type ConversacionDetalle,
  type ConversacionResumen,
  type ConversacionesResponse,
  type Recuerdo,
  type RecuerdosResponse,
} from "../apiRecuerdos";
import { errorMessage } from "../lib/memoria";
import { formatDate } from "../lib/presentation";
import {
  AvisoError,
  Blankslate,
  Button,
  Caja,
  CargandoLista,
  CargandoTexto,
  CounterLabel,
  Details,
  Flash,
  FormControl,
  Label,
  SegmentedControl,
  Textarea,
  ToggleSwitch,
} from "../ui";
import { BookmarkIcon, ChevronDownIcon, ChevronRightIcon, CommentDiscussionIcon, PencilIcon, TrashIcon } from "../ui/iconos";
import { FilaPeligro, ZonaPeligro } from "./ZonaPeligro";
import { useConfirmar } from "./useConfirmar";

interface RecuerdosPanelProps {
  open: boolean;
  caseId: string | null;
}

type Vista = "recuerdos" | "conversaciones";

/** «Me lo dijiste el 12 nov 2026 en «Hipoteca»» (or «Lo corregiste el …» after a correction). */
function procedencia(r: Recuerdo): string {
  const fecha = formatDate(r.fecha);
  const cuando = fecha ? `${r.corregido ? "Lo corregiste" : "Me lo dijiste"} el ${fecha}` : r.corregido ? "Lo corregiste tú" : "Me lo dijiste";
  return r.titulo_conversacion ? `${cuando} en «${r.titulo_conversacion}»` : cuando;
}

function mensajesTexto(n: number): string {
  return n === 1 ? "guardo 1 mensaje tuyo" : `guardo ${n} mensajes tuyos`;
}

/** What a correction left in memory, for the line shown after saving it. */
function resultadoCorreccion(nuevos: Recuerdo[]): string {
  if (nuevos.length === 0) return "Corregido. Ya no uso lo anterior, y de tu texto no he sacado ningún dato.";
  return `Corregido. Ya no uso lo anterior; ahora recuerdo: ${nuevos.map((r) => `«${r.texto}»`).join(", ")}.`;
}

function Edicion({
  recuerdo,
  onGuardar,
  onCancelar,
}: {
  recuerdo: Recuerdo;
  onGuardar: (texto: string) => Promise<void>;
  onCancelar: () => void;
}) {
  const [texto, setTexto] = useState(recuerdo.texto);
  const [enviando, setEnviando] = useState(false);
  return (
    <form
      aria-label={`Corregir: ${recuerdo.texto}`}
      aria-busy={enviando}
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        setEnviando(true);
        void onGuardar(texto.trim()).finally(() => setEnviando(false));
      }}
    >
      <FormControl required disabled={enviando}>
        <FormControl.Label>Cómo es en realidad</FormControl.Label>
        <Textarea block maxLength={600} rows={3} value={texto} onChange={(e) => setTexto(e.target.value)} />
        <FormControl.Caption>
          Lo guardo como algo que me has dicho tú y dejo de usar lo anterior. No escribas contraseñas ni números de cuenta: no los guardo.
        </FormControl.Caption>
      </FormControl>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" size="small" variant="primary" disabled={enviando || !texto.trim()} loading={enviando}>
          Guardar
        </Button>
        <Button type="button" size="small" onClick={onCancelar} disabled={enviando}>
          Cancelar
        </Button>
        {enviando && (
          <span role="status" className="text-xs text-fg-muted">
            Leyendo tu corrección; tarda unos segundos.
          </span>
        )}
      </div>
    </form>
  );
}

/** The user's kept messages of one conversation, read when they open it. */
function MensajesConversacion({ caseId, conversacionId }: { caseId: string | null; conversacionId: string }) {
  const [detalle, setDetalle] = useState<ConversacionDetalle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getConversacion(caseId, conversacionId, controller.signal).then(
      (d) => {
        setDetalle(d);
        setError(null);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(err));
      },
    );
    return () => controller.abort();
  }, [caseId, conversacionId, tick]);

  if (error !== null) {
    return <AvisoError frase="No he podido abrir esta conversación." detalle={error} onReintentar={() => setTick((n) => n + 1)} />;
  }
  if (!detalle) return <CargandoTexto lineas={2} etiqueta="Cargando tus mensajes" />;
  return (
    <div className="flex flex-col gap-2 text-xs">
      {detalle.mensajes.length === 0 ? (
        <p className="m-0 text-fg-muted">No queda ningún mensaje tuyo de esta conversación.</p>
      ) : (
        <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Tus mensajes">
          {detalle.mensajes.map((m, i) => (
            <li key={i} className="border-l-2 border-border-default pl-2">
              {m.fecha && <p className="m-0 text-fg-muted">{formatDate(m.fecha)}</p>}
              <p className="m-0 whitespace-pre-wrap break-words">{m.texto}</p>
            </li>
          ))}
        </ol>
      )}
      {detalle.recuerdos.length > 0 && (
        <div>
          <p className="m-0 font-semibold">Lo que saqué de aquí</p>
          <ul className="m-0 pl-4">
            {detalle.recuerdos.map((r) => (
              <li key={r.id}>{r.texto}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function RecuerdosPanel({ open, caseId }: RecuerdosPanelProps) {
  const [datos, setDatos] = useState<RecuerdosResponse | null>(null);
  const [conversaciones, setConversaciones] = useState<ConversacionesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // What the last correction left in memory, shown until the next change.
  const [aviso, setAviso] = useState<string | null>(null);
  const [editando, setEditando] = useState<string | null>(null);
  const [abierta, setAbierta] = useState<string | null>(null);
  const [vista, setVista] = useState<Vista>("recuerdos");
  const [ocupado, setOcupado] = useState(false);
  // The last load failed: its error can be retried (an error changing a memory cannot).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const idMemoria = useId();
  const confirmar = useConfirmar();

  const recargar = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    Promise.all([getRecuerdos(caseId, controller.signal), getConversaciones(caseId, controller.signal)]).then(
      ([r, c]) => {
        setDatos(r);
        setConversaciones(c);
        setErrorCarga(null);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setErrorCarga(errorMessage(err));
      },
    );
    return () => controller.abort();
  }, [open, caseId, tick]);

  /** Runs a change, then reads everything again; on failure keeps the form open and says why. */
  async function hecho<T>(promesa: Promise<T>, fallo = "No se ha podido guardar."): Promise<T | undefined> {
    setOcupado(true);
    setAviso(null);
    try {
      const resultado = await promesa;
      setEditando(null);
      setError(null);
      recargar();
      return resultado;
    } catch (err) {
      setError(`${fallo} ${errorMessage(err)}`);
      return undefined;
    } finally {
      setOcupado(false);
    }
  }

  /** Ask first; run `accion` only on «yes». */
  async function siConfirma(titulo: string, texto: string, boton: string, accion: () => Promise<unknown>, fallo: string) {
    if (await confirmar(titulo, texto, boton)) await hecho(accion(), fallo);
  }

  async function corregir(r: Recuerdo, texto: string) {
    const resultado = await hecho(corregirRecuerdo(caseId, r.id, { texto }), "No se ha podido guardar la corrección.");
    if (resultado) setAviso(resultadoCorreccion(resultado.recuerdos));
  }

  const recuerdos = datos?.recuerdos ?? [];
  const lista = conversaciones?.conversaciones ?? [];
  const activa = datos?.activa ?? false;
  const sinCuenta = datos?.sin_cuenta ?? false;

  function fila(r: Recuerdo) {
    if (editando === r.id) {
      return (
        <Caja.Fila key={r.id}>
          <Edicion recuerdo={r} onCancelar={() => setEditando(null)} onGuardar={(texto) => corregir(r, texto)} />
        </Caja.Fila>
      );
    }
    return (
      <Caja.Fila key={r.id} className="flex flex-col gap-1">
        <p className="m-0 font-semibold">{r.texto}</p>
        <p className="m-0 flex flex-wrap items-center gap-2 text-xs text-fg-muted">
          <span>{procedencia(r)}</span>
          {r.corregido && <Label variant="secondary">Corregido por ti</Label>}
        </p>
        <div className="mt-1 flex gap-2">
          <Button size="small" leadingVisual={PencilIcon} onClick={() => setEditando(r.id)} disabled={ocupado} aria-label={`Corregir: ${r.texto}`}>
            Corregir
          </Button>
          <Button
            size="small"
            variant="danger"
            leadingVisual={TrashIcon}
            disabled={ocupado}
            aria-label={`Olvidar: ${r.texto}`}
            onClick={() =>
              void siConfirma(
                "¿Olvido esto?",
                "Borro el mensaje tuyo del que lo saqué y todo lo que aprendí de él, aunque sean otros datos de esta lista. No se puede deshacer.",
                "Olvidar",
                () => olvidarRecuerdo(caseId, r.id),
                "No se ha podido olvidar.",
              )
            }
          >
            Olvidar
          </Button>
        </div>
      </Caja.Fila>
    );
  }

  function filaConversacion(c: ConversacionResumen) {
    const desplegada = abierta === c.id;
    const fecha = formatDate(c.actualizada ?? c.creada);
    return (
      <Caja.Fila key={c.id} className="flex flex-col gap-1">
        <h4 className="m-0 text-sm font-semibold">{c.titulo}</h4>
        <p className="m-0 text-xs text-fg-muted">
          {fecha ? `${fecha} · ` : ""}
          {mensajesTexto(c.mensajes)}
        </p>
        {desplegada && <MensajesConversacion caseId={caseId} conversacionId={c.id} />}
        <div className="mt-1 flex flex-wrap gap-2">
          <Button
            size="small"
            leadingVisual={desplegada ? ChevronDownIcon : ChevronRightIcon}
            aria-expanded={desplegada}
            onClick={() => setAbierta(desplegada ? null : c.id)}
          >
            {desplegada ? "Ocultar mensajes" : "Ver mensajes"}
          </Button>
          <Button
            size="small"
            variant="danger"
            leadingVisual={TrashIcon}
            disabled={ocupado}
            aria-label={`Borrar la conversación «${c.titulo}»`}
            onClick={() =>
              void siConfirma(
                "¿Borro esta conversación?",
                "Borro los mensajes tuyos que guardo de ella y todo lo que aprendí de esos mensajes. No se puede deshacer.",
                "Borrar conversación",
                () => borrarConversacion(caseId, c.id),
                "No se ha podido borrar.",
              )
            }
          >
            Borrar conversación
          </Button>
        </div>
      </Caja.Fila>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {errorCarga !== null && <AvisoError frase="No he podido cargar lo que recuerdo." detalle={errorCarga} onReintentar={recargar} />}
      {error && <AvisoError frase={error} />}
      {aviso && (
        <Flash variant="success" role="status">
          {aviso}
        </Flash>
      )}
      {!datos && errorCarga === null && <CargandoLista filas={3} etiqueta="Cargando lo que recuerdo" />}

      {datos && sinCuenta && (
        <Blankslate narrow>
          <Blankslate.Visual>
            <BookmarkIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading as="h3">Aún no hay nada que recordar</Blankslate.Heading>
          <Blankslate.Description>Cuando inicies sesión o tengas un expediente, aquí verás lo que recuerdo de nuestras conversaciones.</Blankslate.Description>
        </Blankslate>
      )}

      {datos && !sinCuenta && (
        <>
          <Caja as="section" aria-label="Memoria de conversaciones">
            <Caja.Cuerpo className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <h3 id={idMemoria} className="m-0 text-sm font-semibold">
                  Memoria de conversaciones
                </h3>
                <p className="m-0 text-fg-muted">
                  {activa && datos.disponible
                    ? "Guardo lo que me escribes y saco de ahí lo que sé de ti, para no preguntarte dos veces."
                    : "En pausa: no guardo ni uso nada nuevo. Lo que ya está sigue aquí para que lo veas o lo borres."}
                </p>
              </div>
              <ToggleSwitch
                aria-labelledby={idMemoria}
                size="small"
                checked={activa}
                disabled={ocupado || !datos.disponible}
                buttonLabelOn="Activada"
                buttonLabelOff="En pausa"
                onClick={() => void hecho(cambiarInterruptor(caseId, !activa), "No se ha podido cambiar.")}
              />
            </Caja.Cuerpo>
            <Caja.Pie className="text-xs">
              <Details>
                <summary className="cursor-pointer font-semibold text-fg-accent">¿Qué guardo y qué no?</summary>
                <p className="m-0 mt-1">{datos.explicacion}</p>
              </Details>
            </Caja.Pie>
          </Caja>

          <SegmentedControl aria-label="Qué ver" fullWidth onChange={(i) => setVista(i === 0 ? "recuerdos" : "conversaciones")}>
            <SegmentedControl.Button selected={vista === "recuerdos"} leadingVisual={BookmarkIcon}>
              {`Lo que sé (${recuerdos.length})`}
            </SegmentedControl.Button>
            <SegmentedControl.Button selected={vista === "conversaciones"} leadingVisual={CommentDiscussionIcon}>
              {`Conversaciones (${lista.length})`}
            </SegmentedControl.Button>
          </SegmentedControl>

          {vista === "recuerdos" && (
            <div className="flex flex-col gap-4">
              {recuerdos.length === 0 && (
                <Blankslate narrow>
                  <Blankslate.Visual>
                    <BookmarkIcon size={24} />
                  </Blankslate.Visual>
                  <Blankslate.Heading as="h3">Todavía no recuerdo nada de ti</Blankslate.Heading>
                  <Blankslate.Description>
                    Cuando me cuentes algo que convenga tener en cuenta (que tienes hipoteca, que te has mudado…), lo verás aquí con la
                    conversación y la fecha en que me lo dijiste.
                  </Blankslate.Description>
                </Blankslate>
              )}
              {TIPOS_RECUERDO.map((tipo) => {
                const delTipo = recuerdos.filter((r) => r.tipo === tipo);
                if (delTipo.length === 0) return null;
                return (
                  <Caja key={tipo} as="section" aria-label={TIPO_RECUERDO_LABEL[tipo]}>
                    <Caja.Cabecera titulo={TIPO_RECUERDO_LABEL[tipo]} acciones={<CounterLabel>{delTipo.length}</CounterLabel>} />
                    <Caja.Lista>{delTipo.map(fila)}</Caja.Lista>
                  </Caja>
                );
              })}
            </div>
          )}

          {vista === "conversaciones" && (
            <Caja as="section" aria-label="Conversaciones anteriores">
              <Caja.Cabecera titulo="Conversaciones anteriores" acciones={<CounterLabel>{lista.length}</CounterLabel>} />
              {lista.length === 0 ? (
                <Caja.Cuerpo className="text-fg-muted">No guardo mensajes de ninguna conversación.</Caja.Cuerpo>
              ) : (
                <Caja.Lista>{lista.map(filaConversacion)}</Caja.Lista>
              )}
            </Caja>
          )}

          {(recuerdos.length > 0 || lista.length > 0) && (
            <ZonaPeligro>
              <FilaPeligro
                titulo="Olvidar todo"
                descripcion="Borro todo lo que sé de ti y los mensajes que guardo de cada conversación. No se puede deshacer."
                accion={
                  <Button
                    variant="danger"
                    disabled={ocupado}
                    onClick={() =>
                      void siConfirma(
                        "¿Olvido todo?",
                        "Borro todo lo que sé de ti y los mensajes que guardo de cada conversación. No se puede deshacer.",
                        "Olvidar todo",
                        () => olvidarTodo(caseId),
                        "No se ha podido borrar.",
                      )
                    }
                  >
                    Olvidar todo
                  </Button>
                }
              />
            </ZonaPeligro>
          )}
        </>
      )}

      <p className="m-0 text-xs text-fg-muted">
        Solo guardo tus mensajes, nunca mis respuestas. Se incluyen en la exportación de tu cuenta y se borran con ella.
      </p>
    </div>
  );
}
