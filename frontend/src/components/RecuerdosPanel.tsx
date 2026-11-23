// «Lo que sé de ti»: what the assistant remembers from earlier conversations, grouped by kind, each item with
// where it came from («Me lo dijiste el 12/11/2026: “…”»). The user can correct any item (their wording wins),
// forget one or everything, pause the memory, and see or delete the earlier conversations with their summaries.
//
// Shown in a side sheet (registro.ts: presentacion "dialogo"); `open` only triggers reading it all again.

import { useCallback, useEffect, useId, useState } from "react";

import {
  TIPOS_RECUERDO,
  TIPO_RECUERDO_LABEL,
  borrarConversacion,
  cambiarInterruptor,
  corregirRecuerdo,
  getConversaciones,
  getRecuerdos,
  olvidarRecuerdo,
  olvidarTodo,
  type ConversacionesResponse,
  type Recuerdo,
  type RecuerdosResponse,
  type TipoRecuerdo,
} from "../apiRecuerdos";
import { errorMessage } from "../lib/memoria";
import { formatDate } from "../lib/presentation";
import { Blankslate, Button, Caja, CargandoLista, CounterLabel, Details, Flash, FormControl, Label, SegmentedControl, Select, Textarea, ToggleSwitch } from "../ui";
import { BookmarkIcon, CommentDiscussionIcon, HistoryIcon, PencilIcon, ShieldIcon, TrashIcon } from "../ui/iconos";
import { FilaPeligro, ZonaPeligro } from "./ZonaPeligro";
import { useConfirmar } from "./useConfirmar";

interface RecuerdosPanelProps {
  open: boolean;
  caseId: string | null;
}

type Vista = "recuerdos" | "conversaciones";

function origenTexto(r: Recuerdo): string {
  const fecha = formatDate(r.origen.fecha) ?? r.origen.fecha;
  return `Me lo dijiste el ${fecha}`;
}

function Edicion({
  recuerdo,
  onGuardar,
  onCancelar,
}: {
  recuerdo: Recuerdo;
  onGuardar: (texto: string, tipo: TipoRecuerdo) => Promise<void>;
  onCancelar: () => void;
}) {
  const [texto, setTexto] = useState(recuerdo.texto);
  const [tipo, setTipo] = useState<TipoRecuerdo>(recuerdo.tipo);
  const [enviando, setEnviando] = useState(false);
  return (
    <form
      aria-label={`Corregir: ${recuerdo.texto}`}
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        setEnviando(true);
        void onGuardar(texto.trim(), tipo).finally(() => setEnviando(false));
      }}
    >
      <FormControl required>
        <FormControl.Label>Cómo quieres que lo recuerde</FormControl.Label>
        <Textarea block maxLength={600} rows={3} value={texto} onChange={(e) => setTexto(e.target.value)} />
        <FormControl.Caption>Tu versión sustituye a la mía. No escribas contraseñas ni claves: no las guardo.</FormControl.Caption>
      </FormControl>
      <FormControl>
        <FormControl.Label>Tipo</FormControl.Label>
        <Select block value={tipo} onChange={(e) => setTipo(e.target.value as TipoRecuerdo)}>
          {TIPOS_RECUERDO.map((t) => (
            <Select.Option key={t} value={t}>
              {TIPO_RECUERDO_LABEL[t]}
            </Select.Option>
          ))}
        </Select>
      </FormControl>
      <div className="flex gap-2">
        <Button type="submit" size="small" variant="primary" disabled={enviando || !texto.trim()} loading={enviando}>
          Guardar
        </Button>
        <Button type="button" size="small" onClick={onCancelar}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}

export function RecuerdosPanel({ open, caseId }: RecuerdosPanelProps) {
  const [datos, setDatos] = useState<RecuerdosResponse | null>(null);
  const [conversaciones, setConversaciones] = useState<ConversacionesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editando, setEditando] = useState<string | null>(null);
  const [vista, setVista] = useState<Vista>("recuerdos");
  const [ocupado, setOcupado] = useState(false);
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
        setError(null);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setError(`No he podido cargar lo que recuerdo. ${errorMessage(err)}`);
      },
    );
    return () => controller.abort();
  }, [open, caseId, tick]);

  async function hecho(promesa: Promise<unknown>, fallo = "No se ha podido guardar.") {
    setOcupado(true);
    try {
      await promesa;
      setEditando(null);
      setError(null);
      recargar();
    } catch (err) {
      setError(`${fallo} ${errorMessage(err)}`);
    } finally {
      setOcupado(false);
    }
  }

  /** Ask first; run `accion` only on «yes». */
  async function siConfirma(titulo: string, texto: string, boton: string, accion: () => Promise<unknown>, fallo: string) {
    if (await confirmar(titulo, texto, boton)) await hecho(accion(), fallo);
  }

  const recuerdos = datos?.recuerdos ?? [];
  const historial = datos?.historial ?? [];
  const lista = conversaciones?.conversaciones ?? [];
  const activa = datos?.activa ?? false;
  const sinCuenta = datos?.sin_cuenta ?? false;
  const textosPorId = new Map(recuerdos.map((r) => [r.id, r.texto]));

  function fila(r: Recuerdo) {
    if (editando === r.id) {
      return (
        <Caja.Fila key={r.id}>
          <Edicion recuerdo={r} onCancelar={() => setEditando(null)} onGuardar={(texto, tipo) => hecho(corregirRecuerdo(caseId, r.id, { texto, tipo }))} />
        </Caja.Fila>
      );
    }
    const veces = new Set(r.fuentes.map((f) => `${f.conversacion_id}:${f.cita}`)).size;
    return (
      <Caja.Fila key={r.id} className="flex flex-col gap-1">
        <p className="m-0 font-semibold">{r.texto}</p>
        <p className="m-0 text-xs">
          {origenTexto(r)}: <q className="italic">{r.origen.cita}</q>
          {r.origen.titulo_conversacion && <span className="text-fg-muted"> (en «{r.origen.titulo_conversacion}»)</span>}
        </p>
        {(r.corregido || veces > 1 || r.usos > 0) && (
          <p className="m-0 flex flex-wrap gap-x-2 gap-y-1 text-xs text-fg-muted">
            {r.corregido && <span>Lo corregiste el {formatDate(r.actualizado)}</span>}
            {veces > 1 && <span>Me lo has dicho {veces} veces</span>}
            {r.usos > 0 && <span>Lo he tenido en cuenta {r.usos === 1 ? "una vez" : `${r.usos} veces`}</span>}
          </p>
        )}
        {r.sensible && (
          <p className="m-0">
            <Label variant="danger">
              <ShieldIcon size={12} className="mr-1" />
              Dato sensible: lo guardo porque me pediste que lo recordara
            </Label>
          </p>
        )}
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
                "Se borran tus palabras y no lo volveré a aprender de esa conversación.",
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

  return (
    <div className="flex flex-col gap-4">
      {error && <Flash variant="danger">{error}</Flash>}
      {!datos && !error && <CargandoLista filas={3} etiqueta="Cargando lo que recuerdo" />}

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
                    ? "Recuerdo lo importante de cada conversación para no preguntarte dos veces."
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
                <summary className="cursor-pointer font-semibold text-fg-accent">¿Qué guardo y durante cuánto tiempo?</summary>
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
                    Cuando me cuentes algo que convenga tener en cuenta (tu hipoteca, cómo prefieres que te explique las cosas…), lo verás
                    aquí con tus palabras y la fecha.
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
              {historial.length > 0 && (
                <Caja as="section" aria-label="Lo que ha cambiado" condensada>
                  <Caja.Cabecera
                    titulo={
                      <span className="inline-flex items-center gap-2">
                        <HistoryIcon />
                        Lo que ha cambiado
                      </span>
                    }
                    acciones={<CounterLabel>{historial.length}</CounterLabel>}
                  />
                  <Caja.Lista>
                    {historial.map((h) => (
                      <Caja.Fila key={h.id} className="flex flex-wrap items-start gap-2">
                        <div className="min-w-0 flex-1 text-xs">
                          <p className="m-0">
                            <span className="text-fg-muted">Antes ({formatDate(h.origen.fecha)}):</span> {h.texto}
                          </p>
                          {h.sustituido_por && textosPorId.has(h.sustituido_por) && (
                            <p className="m-0 text-fg-muted">Ahora: {textosPorId.get(h.sustituido_por)}</p>
                          )}
                        </div>
                        <Button
                          size="small"
                          variant="invisible"
                          leadingVisual={TrashIcon}
                          disabled={ocupado}
                          aria-label={`Olvidar la versión anterior: ${h.texto}`}
                          onClick={() => void hecho(olvidarRecuerdo(caseId, h.id), "No se ha podido olvidar.")}
                        >
                          Olvidar
                        </Button>
                      </Caja.Fila>
                    ))}
                  </Caja.Lista>
                </Caja>
              )}
            </div>
          )}

          {vista === "conversaciones" && (
            <Caja as="section" aria-label="Conversaciones anteriores">
              <Caja.Cabecera titulo="Conversaciones anteriores" acciones={<CounterLabel>{lista.length}</CounterLabel>} />
              {lista.length === 0 ? (
                <Caja.Cuerpo className="text-fg-muted">No hay conversaciones guardadas.</Caja.Cuerpo>
              ) : (
                <Caja.Lista>
                  {lista.map((c) => (
                    <Caja.Fila key={c.id} className="flex flex-col gap-1">
                      <h4 className="m-0 text-sm font-semibold">{c.titulo}</h4>
                      <p className="m-0 text-xs text-fg-muted">
                        {formatDate(c.actualizada)}
                        {c.recuerdos > 0 && ` · ${c.recuerdos === 1 ? "aprendí una cosa" : `aprendí ${c.recuerdos} cosas`}`}
                        {c.retenidos > 0 ? ` · guardo sus ${c.retenidos} mensajes unos días` : " · ya no guardo sus mensajes"}
                      </p>
                      <p className="m-0">{c.resumen ?? "Aún sin resumir."}</p>
                      <div className="mt-1">
                        <Button
                          size="small"
                          variant="danger"
                          leadingVisual={TrashIcon}
                          disabled={ocupado}
                          aria-label={`Borrar la conversación «${c.titulo}»`}
                          onClick={() =>
                            void siConfirma(
                              "¿Borro esta conversación?",
                              "Se borran sus mensajes, su resumen y lo que aprendí solo de ella.",
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
                  ))}
                </Caja.Lista>
              )}
            </Caja>
          )}

          {(recuerdos.length > 0 || lista.length > 0) && (
            <ZonaPeligro>
              <FilaPeligro
                titulo="Olvidar todo"
                descripcion="Se borran lo que sé de ti, los resúmenes y las conversaciones guardadas. No se puede deshacer."
                accion={
                  <Button
                    variant="danger"
                    disabled={ocupado}
                    onClick={() =>
                      void siConfirma(
                        "¿Olvido todo?",
                        "Se borran lo que sé de ti, los resúmenes y las conversaciones guardadas. No se puede deshacer.",
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
        Se guarda en el servidor, se incluye en la exportación de tu cuenta y se borra con ella.
        {datos ? ` Los mensajes tal cual se borran a los ${datos.retencion_dias} días.` : ""}
      </p>
    </div>
  );
}
