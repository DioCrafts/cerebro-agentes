// «Lo que sé de tus preferencias»: every decision the assistant remembers about the user (what they
// rejected, cannot do, prefer or already did), with their words, the date and until when it holds.
// The user can add, edit or forget any of them; forgetting erases their words (only a trace that
// something was deleted remains).

import { BookUser, Pencil, Plus, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { formatDate } from "../lib/presentation";
import { errorMessage } from "../lib/memoria";
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

interface PreferenciasPanelProps {
  open: boolean;
  onClose: () => void;
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
      className="space-y-2 rounded-xl border border-primary/40 bg-surface px-4 py-3"
      onSubmit={(e) => {
        e.preventDefault();
        setEnviando(true);
        void onGuardar({ ambito, tipo, razon: razon.trim(), caducidad: sinFecha ? null : caducidad || "" }).finally(() => setEnviando(false));
      }}
    >
      {!inicial && (
        <label className="block text-[12.5px] text-ink-soft">
          Sobre qué
          <select value={ambito} onChange={(e) => setAmbito(e.target.value)} className="mt-1 block w-full rounded border border-line bg-paper px-2 py-1 text-[13px] text-ink">
            {catalogo.map((c) => (
              <option key={c.ambito} value={c.ambito}>
                {c.etiqueta}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="block text-[12.5px] text-ink-soft">
        Qué decides
        <select value={tipo} onChange={(e) => setTipo(e.target.value as TipoDecision)} className="mt-1 block w-full rounded border border-line bg-paper px-2 py-1 text-[13px] text-ink">
          {TIPOS.map((t) => (
            <option key={t} value={t}>
              {TIPO_LABEL[t]}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-[12.5px] text-ink-soft">
        Con tus palabras, y si quieres por qué
        <input
          type="text"
          required
          maxLength={500}
          value={razon}
          onChange={(e) => setRazon(e.target.value)}
          placeholder="p. ej. no quiero planes de pensiones porque necesito liquidez"
          className="mt-1 block w-full rounded border border-line bg-paper px-2 py-1 text-[13px] text-ink"
        />
      </label>
      <div className="flex flex-wrap items-end gap-3 text-[12.5px] text-ink-soft">
        <label>
          Hasta
          <input
            type="date"
            value={caducidad}
            disabled={sinFecha}
            onChange={(e) => setCaducidad(e.target.value)}
            className="mt-1 block rounded border border-line bg-paper px-2 py-1 text-[13px] text-ink disabled:opacity-50"
          />
        </label>
        <label className="inline-flex items-center gap-1.5 pb-1">
          <input type="checkbox" checked={sinFecha} onChange={(e) => setSinFecha(e.target.checked)} />
          Sin fecha (hasta que lo cambie)
        </label>
      </div>
      {!inicial && !sinFecha && !caducidad && (
        <p className="text-[11.5px] text-muted">Sin fecha, vale un año (seis meses si ya lo hiciste; las preferencias, hasta que las cambies).</p>
      )}
      <div className="flex gap-2">
        <button type="submit" disabled={enviando || !razon.trim()} className="rounded-lg bg-primary px-3 py-1 text-[12.5px] font-semibold text-white disabled:opacity-50">
          {enviando ? "Guardando…" : "Guardar"}
        </button>
        <button type="button" onClick={onCancelar} className="rounded-lg px-3 py-1 text-[12.5px] font-semibold text-ink-soft hover:bg-line/60">
          Cancelar
        </button>
      </div>
    </form>
  );
}

export function PreferenciasPanel({ open, onClose, caseId, onChanged }: PreferenciasPanelProps) {
  const [datos, setDatos] = useState<DecisionesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editando, setEditando] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

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

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

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

  const decisiones = datos?.decisiones ?? [];
  const activas = decisiones.filter((d) => d.estado === "vigente");
  const pueden = decisiones.filter((d) => d.estado === "reabrible" || d.estado === "caducada");
  const pasadas = decisiones.filter((d) => d.estado === "reabierta" || d.estado === "sustituida");

  function tarjeta(d: Decision) {
    if (editando === d.id && caseId && datos) {
      return (
        <li key={d.id}>
          <Formulario
            inicial={d}
            catalogo={datos.catalogo}
            onCancelar={() => setEditando(null)}
            onGuardar={(f) => hecho(editarDecision(caseId, d.id, { tipo: f.tipo, razon: f.razon, caducidad: f.caducidad || null }))}
          />
        </li>
      );
    }
    const condicion = condicionTexto(d);
    return (
      <li key={d.id} className="rounded-xl border border-line bg-surface px-4 py-3 shadow-card">
        <p className="flex flex-wrap items-center gap-2 text-[11.5px] font-semibold uppercase tracking-[0.08em] text-muted">
          {TIPO_LABEL[d.tipo]}
          <span className="rounded-pill bg-line/70 px-2 py-px normal-case tracking-normal text-ink-soft">{ESTADO_LABEL[d.estado]}</span>
        </p>
        <h4 className="mt-1 text-[14.5px] font-bold leading-snug text-ink">{d.etiqueta}</h4>
        <p className="mt-1 text-[13.5px] leading-snug text-ink">«{d.razon}»</p>
        <p className="mt-1 text-[12px] leading-snug text-muted">
          {d.fuente === "chat" ? "Me lo dijiste" : d.fuente === "boton" ? "Lo marcaste" : d.fuente === "accion" ? "Lo decidiste en una acción pendiente" : "Lo escribiste aquí"} el{" "}
          {formatDate(d.fecha)}
          {d.caducidad ? ` · lo tendré en cuenta hasta el ${formatDate(d.caducidad)}` : " · sin fecha de fin"}
          {d.omisiones > 0 && ` · por esto he dejado de avisarte ${d.omisiones} ${d.omisiones === 1 ? "vez" : "veces"}`}
        </p>
        {condicion && <p className="mt-1 text-[12px] leading-snug text-ink-soft">{condicion}</p>}
        {d.motivo_estado && d.estado !== "vigente" && <p className="mt-1 text-[12px] leading-snug text-ink-soft">Ahora: {d.motivo_estado}.</p>}
        {caseId && (
          <div className="mt-2 flex gap-2">
            <button type="button" onClick={() => setEditando(d.id)} className="inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-[12px] font-semibold text-ink-soft hover:border-primary">
              <Pencil size={12} aria-hidden />
              Cambiar
            </button>
            <button
              type="button"
              onClick={() => {
                if (window.confirm("¿Olvidar esta decisión? Se borrarán tus palabras y podré volver a proponértelo.")) void hecho(borrarDecision(caseId, d.id));
              }}
              className="inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-[12px] font-semibold text-ink-soft hover:border-risk-high-fg hover:text-risk-high-fg"
            >
              <Trash2 size={12} aria-hidden />
              Olvidar
            </button>
          </div>
        )}
      </li>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button type="button" aria-label="Cerrar tus preferencias" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-label="Lo que sé de tus preferencias" className="relative flex h-full w-full max-w-[440px] flex-col border-l border-line bg-paper shadow-pop">
        <header className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <h2 className="flex items-center gap-2 font-serif text-[19px] font-bold text-ink">
              <BookUser size={18} aria-hidden className="text-primary-dark" />
              Lo que sé de tus preferencias
            </h2>
            <p className="text-[12.5px] text-muted">Lo que me has dicho que no quieres, no puedes, prefieres o ya hiciste</p>
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
          {!caseId && <p className="text-[13.5px] text-ink-soft">Cuando tengas un expediente, aquí verás lo que me hayas dicho que no te interesa o que prefieres.</p>}
          {caseId && datos && editando === "nueva" ? (
            <Formulario
              catalogo={datos.catalogo}
              onCancelar={() => setEditando(null)}
              onGuardar={(f) => hecho(crearDecision(caseId, { ambito: f.ambito, tipo: f.tipo, razon: f.razon, ...(f.caducidad === "" ? {} : { caducidad: f.caducidad }) }))}
            />
          ) : (
            caseId &&
            datos && (
              <button type="button" onClick={() => setEditando("nueva")} className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-[13px] font-semibold text-primary-dark hover:border-primary">
                <Plus size={14} aria-hidden />
                Añadir una preferencia
              </button>
            )
          )}
          {datos && decisiones.length === 0 && (
            <p className="text-[13.5px] leading-relaxed text-ink-soft">
              Todavía no me has dicho nada que deba recordar. Cuando algo no te interese, dímelo en el chat o pulsa «No me interesa» en un aviso, y no volveré a proponértelo.
            </p>
          )}
          {activas.length > 0 && (
            <section aria-label="En vigor" className="space-y-2">
              <h3 className="text-[12px] font-bold uppercase tracking-[0.08em] text-ink-soft">En vigor ({activas.length})</h3>
              <ul className="space-y-2">{activas.map(tarjeta)}</ul>
            </section>
          )}
          {pueden.length > 0 && (
            <section aria-label="Pueden volver" className="space-y-2">
              <h3 className="text-[12px] font-bold uppercase tracking-[0.08em] text-ink-soft">Ha cambiado algo: te lo podré proponer una vez más ({pueden.length})</h3>
              <ul className="space-y-2">{pueden.map(tarjeta)}</ul>
            </section>
          )}
          {pasadas.length > 0 && (
            <section aria-label="Anteriores" className="space-y-2">
              <h3 className="text-[12px] font-bold uppercase tracking-[0.08em] text-ink-soft">Anteriores ({pasadas.length})</h3>
              <ul className="space-y-2">{pasadas.map(tarjeta)}</ul>
            </section>
          )}
        </div>
        <footer className="border-t border-line px-5 py-3 text-[11.5px] leading-relaxed text-muted">
          Se guarda en tu expediente, se incluye en la exportación de tu cuenta y se borra con ella. Si olvidas una decisión, se borran tus palabras.
        </footer>
      </aside>
    </div>
  );
}
