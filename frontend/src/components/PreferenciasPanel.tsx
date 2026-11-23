// «Lo que sé de tus preferencias»: every decision the assistant remembers about the user (what they
// rejected, cannot do, prefer or already did), with their words, the date and until when it holds.
// The user can add, edit or forget any of them; forgetting erases their words (only a trace that
// something was deleted remains).
//
// Shown in a side sheet (registro.ts: presentacion "dialogo"); `open` only triggers reading the decisions again.

import { useCallback, useEffect, useState } from "react";

import {
  ESTADO_LABEL,
  TIPO_LABEL,
  borrarDecision,
  crearDecision,
  editarDecision,
  getDecisiones,
  type Decision,
  type DecisionesResponse,
  type TipoDecision,
} from "../apiMemoria";
import { errorMessage } from "../lib/memoria";
import { formatDate } from "../lib/presentation";
import { Blankslate, Button, Caja, CargandoLista, Checkbox, CounterLabel, Flash, FormControl, Label, Select, TextInput } from "../ui";
import { PencilIcon, PlusIcon, SlidersIcon, TrashIcon } from "../ui/iconos";
import { useConfirmar } from "./useConfirmar";

interface PreferenciasPanelProps {
  open: boolean;
  caseId: string | null;
  /** Called after any change (the inbox may have changed: notices removed, the year-end plan rebuilt). */
  onChanged?: () => void;
}

const TIPOS: TipoDecision[] = ["rechazo", "restriccion", "preferencia", "aceptacion"];

function condicionTexto(d: Decision): string | null {
  const partes = d.condiciones_de_reapertura.map((c) => {
    if (c.descripcion) return c.descripcion;
    if (c.tipo === "fecha") return `a partir del ${formatDate(c.fecha ?? null) ?? c.fecha}`;
    if (c.tipo === "hecho") return `si cambia «${c.hecho}»`;
    return "si aparece una propuesta mucho mejor";
  });
  return partes.length ? `Te lo volveré a proponer ${partes.join("; ")}.` : null;
}

function fuenteTexto(fuente: Decision["fuente"]): string {
  if (fuente === "chat") return "Me lo dijiste";
  if (fuente === "boton") return "Lo marcaste";
  if (fuente === "accion") return "Lo decidiste en una acción pendiente";
  return "Lo escribiste aquí";
}

function Formulario({
  inicial,
  catalogo,
  onGuardar,
  onCancelar,
}: {
  inicial?: Decision;
  catalogo: DecisionesResponse["catalogo"];
  onGuardar: (datos: { ambito: string; tipo: TipoDecision; razon: string; caducidad: string | null }) => Promise<void>;
  onCancelar: () => void;
}) {
  const [ambito, setAmbito] = useState(inicial?.ambito ?? catalogo[0]?.ambito ?? "fiscal");
  const [tipo, setTipo] = useState<TipoDecision>(inicial?.tipo ?? "rechazo");
  const [razon, setRazon] = useState(inicial?.razon ?? "");
  const [caducidad, setCaducidad] = useState(inicial?.caducidad ?? "");
  const [sinFecha, setSinFecha] = useState(inicial ? !inicial.caducidad : false);
  const [enviando, setEnviando] = useState(false);
  return (
    <form
      aria-label={inicial ? `Cambiar: ${inicial.etiqueta}` : "Nueva preferencia"}
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        setEnviando(true);
        void onGuardar({ ambito, tipo, razon: razon.trim(), caducidad: sinFecha ? null : caducidad || "" }).finally(() => setEnviando(false));
      }}
    >
      {!inicial && (
        <FormControl>
          <FormControl.Label>Sobre qué</FormControl.Label>
          <Select block value={ambito} onChange={(e) => setAmbito(e.target.value)}>
            {catalogo.map((c) => (
              <Select.Option key={c.ambito} value={c.ambito}>
                {c.etiqueta}
              </Select.Option>
            ))}
          </Select>
        </FormControl>
      )}
      <FormControl>
        <FormControl.Label>Qué decides</FormControl.Label>
        <Select block value={tipo} onChange={(e) => setTipo(e.target.value as TipoDecision)}>
          {TIPOS.map((t) => (
            <Select.Option key={t} value={t}>
              {TIPO_LABEL[t]}
            </Select.Option>
          ))}
        </Select>
      </FormControl>
      <FormControl required>
        <FormControl.Label>Con tus palabras, y si quieres por qué</FormControl.Label>
        <TextInput
          block
          maxLength={500}
          value={razon}
          onChange={(e) => setRazon(e.target.value)}
          placeholder="p. ej. no quiero planes de pensiones porque necesito liquidez"
        />
      </FormControl>
      <div className="flex flex-wrap items-end gap-4">
        <FormControl disabled={sinFecha}>
          <FormControl.Label>Hasta</FormControl.Label>
          <TextInput type="date" value={caducidad} onChange={(e) => setCaducidad(e.target.value)} />
        </FormControl>
        <FormControl>
          <Checkbox checked={sinFecha} onChange={(e) => setSinFecha(e.target.checked)} />
          <FormControl.Label>Sin fecha (hasta que lo cambie)</FormControl.Label>
        </FormControl>
      </div>
      {!inicial && !sinFecha && !caducidad && (
        <p className="m-0 text-xs text-fg-muted">Sin fecha, vale un año (seis meses si ya lo hiciste; las preferencias, hasta que las cambies).</p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="small" variant="primary" disabled={enviando || !razon.trim()} loading={enviando}>
          Guardar
        </Button>
        <Button type="button" size="small" onClick={onCancelar}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}

export function PreferenciasPanel({ open, caseId, onChanged }: PreferenciasPanelProps) {
  const [datos, setDatos] = useState<DecisionesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editando, setEditando] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const confirmar = useConfirmar();

  const recargar = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!open || !caseId) return;
    const controller = new AbortController();
    getDecisiones(caseId, controller.signal).then(
      (resp) => {
        setDatos(resp);
        setError(null);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setError(`No se han podido cargar tus preferencias. ${errorMessage(err)}`);
      },
    );
    return () => controller.abort();
  }, [open, caseId, tick]);

  async function hecho(promesa: Promise<unknown>) {
    try {
      await promesa;
      setEditando(null);
      setError(null);
      recargar();
      onChanged?.();
    } catch (err) {
      setError(`No se ha podido guardar. ${errorMessage(err)}`);
    }
  }

  async function olvidar(id: string) {
    if (!caseId) return;
    const si = await confirmar("¿Olvidar esta decisión?", "Se borrarán tus palabras y podré volver a proponértelo.", "Olvidar");
    if (si) await hecho(borrarDecision(caseId, id));
  }

  const decisiones = datos?.decisiones ?? [];
  const grupos = [
    { clave: "activas", titulo: "En vigor", items: decisiones.filter((d) => d.estado === "vigente") },
    { clave: "pueden", titulo: "Ha cambiado algo: te lo podré proponer una vez más", items: decisiones.filter((d) => d.estado === "reabrible" || d.estado === "caducada") },
    { clave: "pasadas", titulo: "Anteriores", items: decisiones.filter((d) => d.estado === "reabierta" || d.estado === "sustituida") },
  ];

  function fila(d: Decision) {
    if (editando === d.id && caseId && datos) {
      return (
        <Caja.Fila key={d.id}>
          <Formulario
            inicial={d}
            catalogo={datos.catalogo}
            onCancelar={() => setEditando(null)}
            onGuardar={(f) => hecho(editarDecision(caseId, d.id, { tipo: f.tipo, razon: f.razon, caducidad: f.caducidad || null }))}
          />
        </Caja.Fila>
      );
    }
    const condicion = condicionTexto(d);
    return (
      <Caja.Fila key={d.id} className="flex flex-col gap-1">
        <p className="m-0 flex flex-wrap items-center gap-2">
          <Label variant="accent">{TIPO_LABEL[d.tipo]}</Label>
          <Label variant="secondary">{ESTADO_LABEL[d.estado]}</Label>
        </p>
        <h4 className="m-0 text-sm font-semibold">{d.etiqueta}</h4>
        <p className="m-0">«{d.razon}»</p>
        <p className="m-0 text-xs text-fg-muted">
          {fuenteTexto(d.fuente)} el {formatDate(d.fecha)}
          {d.caducidad ? ` · lo tendré en cuenta hasta el ${formatDate(d.caducidad)}` : " · sin fecha de fin"}
          {d.omisiones > 0 && ` · por esto he dejado de avisarte ${d.omisiones} ${d.omisiones === 1 ? "vez" : "veces"}`}
        </p>
        {condicion && <p className="m-0 text-xs">{condicion}</p>}
        {d.motivo_estado && d.estado !== "vigente" && <p className="m-0 text-xs">Ahora: {d.motivo_estado}.</p>}
        {caseId && (
          <div className="mt-1 flex gap-2">
            <Button size="small" leadingVisual={PencilIcon} onClick={() => setEditando(d.id)} aria-label={`Cambiar: ${d.etiqueta}`}>
              Cambiar
            </Button>
            <Button size="small" variant="danger" leadingVisual={TrashIcon} onClick={() => void olvidar(d.id)} aria-label={`Olvidar: ${d.etiqueta}`}>
              Olvidar
            </Button>
          </div>
        )}
      </Caja.Fila>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {caseId && datos && editando !== "nueva" && (
        <div className="flex justify-end">
          <Button size="small" leadingVisual={PlusIcon} onClick={() => setEditando("nueva")}>
            Añadir una preferencia
          </Button>
        </div>
      )}

      {error && <Flash variant="danger">{error}</Flash>}

      {!caseId && (
        <Blankslate narrow>
          <Blankslate.Visual>
            <SlidersIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading as="h3">Aún no tienes expediente</Blankslate.Heading>
          <Blankslate.Description>Cuando lo tengas, aquí verás lo que me hayas dicho que no te interesa o que prefieres.</Blankslate.Description>
        </Blankslate>
      )}
      {caseId && !datos && !error && <CargandoLista filas={2} etiqueta="Cargando tus preferencias" />}

      {caseId && datos && editando === "nueva" && (
        <Caja variante="acento">
          <Caja.Cabecera titulo="Nueva preferencia" />
          <Caja.Cuerpo>
            <Formulario
              catalogo={datos.catalogo}
              onCancelar={() => setEditando(null)}
              onGuardar={(f) =>
                hecho(crearDecision(caseId, { ambito: f.ambito, tipo: f.tipo, razon: f.razon, ...(f.caducidad === "" ? {} : { caducidad: f.caducidad }) }))
              }
            />
          </Caja.Cuerpo>
        </Caja>
      )}

      {datos && decisiones.length === 0 && (
        <Blankslate narrow>
          <Blankslate.Visual>
            <SlidersIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading as="h3">Nada que recordar todavía</Blankslate.Heading>
          <Blankslate.Description>
            Cuando algo no te interese, dímelo en el chat o pulsa «No me interesa» en un aviso, y no volveré a proponértelo.
          </Blankslate.Description>
        </Blankslate>
      )}

      {grupos
        .filter((g) => g.items.length > 0)
        .map((g) => (
          <Caja key={g.clave} as="section" aria-label={g.titulo}>
            <Caja.Cabecera titulo={g.titulo} acciones={<CounterLabel>{g.items.length}</CounterLabel>} />
            <Caja.Lista>{g.items.map(fila)}</Caja.Lista>
          </Caja>
        ))}

      <p className="m-0 text-xs text-fg-muted">
        Se guarda en tu expediente, se incluye en la exportación de tu cuenta y se borra con ella. Si olvidas una decisión, se borran tus
        palabras.
      </p>
    </div>
  );
}
