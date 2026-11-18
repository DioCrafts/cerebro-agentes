// «Consejo»: the household council's sessions of this case — the convocations waiting for the user, the ones running
// (live), the rulings, and those that could not finish — plus a way to put a decision to the council directly. Each
// session opens its card (estimate and confirmation, progress, ruling); a finished one can be deleted.

import clsx from "clsx";
import { ChevronLeft, Loader2, Trash2, UsersRound, X } from "lucide-react";
import { useCallback, useEffect, useId, useState } from "react";

import {
  ESTADO_LABEL,
  EN_MARCHA,
  borrarSesion,
  convocarConsejo,
  listarSesiones,
  usd,
  type ListaSesiones,
  type ResumenSesion,
} from "../apiConsejo";
import { ConvocarConsejo } from "../components/ConvocarConsejo";
import { errorMessage } from "../lib/memoria";
import { relativeTime } from "../lib/presentation";
import type { PropsPanel } from "./tipos";

function Insignia({ sesion }: { sesion: ResumenSesion }) {
  const color =
    sesion.estado === "terminada"
      ? "bg-grounding-cited-bg text-grounding-cited-fg"
      : sesion.estado === "fallida"
        ? "bg-risk-high-bg text-risk-high-fg"
        : sesion.estado === "pendiente_confirmacion" || EN_MARCHA.has(sesion.estado)
          ? "bg-primary-soft text-primary-dark"
          : "bg-line/70 text-ink-soft";
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-pill px-2 py-0.5 text-[11.5px] font-semibold", color)}>
      {EN_MARCHA.has(sesion.estado) && <Loader2 size={11} aria-hidden className="animate-spin" />}
      {ESTADO_LABEL[sesion.estado]}
    </span>
  );
}

function Nueva({ caseId, onCreada }: { caseId: string; onCreada: (id: string) => void }) {
  const [pregunta, setPregunta] = useState("");
  const [preferencia, setPreferencia] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const idPregunta = useId();
  const idPreferencia = useId();
  return (
    <form
      className="space-y-2 rounded-xl border border-line bg-surface px-4 py-3"
      onSubmit={(e) => {
        e.preventDefault();
        setEnviando(true);
        setError(null);
        convocarConsejo(caseId, { pregunta: pregunta.trim(), preferencia: preferencia.trim() || null })
          .then((s) => {
            setPregunta("");
            setPreferencia("");
            onCreada(s.id);
          })
          .catch((err: unknown) => setError(errorMessage(err)))
          .finally(() => setEnviando(false));
      }}
    >
      <label htmlFor={idPregunta} className="block text-[13px] font-semibold text-ink">
        Plantea una decisión difícil
      </label>
      <textarea
        id={idPregunta}
        required
        minLength={10}
        maxLength={4000}
        rows={3}
        value={pregunta}
        placeholder="¿Amortizo la hipoteca o invierto los ahorros?"
        onChange={(e) => setPregunta(e.target.value)}
        className="block w-full rounded border border-line bg-paper px-2 py-1.5 text-[13.5px] text-ink"
      />
      <label htmlFor={idPreferencia} className="block text-[12.5px] text-ink-soft">
        Hacia dónde te inclinas (opcional: el consejo no lo verá hasta revisar sus propuestas)
      </label>
      <input
        id={idPreferencia}
        maxLength={600}
        value={preferencia}
        onChange={(e) => setPreferencia(e.target.value)}
        className="block w-full rounded border border-line bg-paper px-2 py-1.5 text-[13px] text-ink"
      />
      <p className="text-[11.5px] leading-snug text-muted">Te diré antes cuánto cuesta; nada se ejecuta hasta que lo confirmes.</p>
      <button
        type="submit"
        disabled={enviando || pregunta.trim().length < 10}
        className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[13px] font-semibold text-white disabled:opacity-50"
      >
        {enviando && <Loader2 size={14} aria-hidden className="animate-spin" />}
        Ver el coste
      </button>
      {error && (
        <p role="alert" className="text-[12.5px] text-risk-high-fg">
          {error}
        </p>
      )}
    </form>
  );
}

export default function PanelConsejo({ ctx, open, onClose }: PropsPanel) {
  const caseId = ctx.caso.id;
  const [datos, setDatos] = useState<ListaSesiones | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [abierta, setAbierta] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const idTitulo = useId();
  const recargar = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!open || !caseId) return;
    const controller = new AbortController();
    listarSesiones(caseId, controller.signal).then(
      (lista) => {
        setDatos(lista);
        setError(null);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setError(`No he podido cargar las sesiones del consejo. ${errorMessage(err)}`);
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
  const sesiones = datos?.sesiones ?? [];
  const seleccionada = sesiones.find((s) => s.id === abierta) ?? null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button type="button" aria-label="Cerrar el consejo" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-labelledby={idTitulo} className="relative flex h-full w-full max-w-[560px] flex-col border-l border-line bg-paper shadow-pop">
        <header className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <h2 id={idTitulo} className="flex items-center gap-2 font-serif text-[19px] font-bold text-ink">
              <UsersRound size={18} aria-hidden className="text-primary-dark" />
              Consejo del hogar
            </h2>
            <p className="text-[12.5px] text-muted">Decisiones difíciles estudiadas por varias IA, por separado y de forma anónima</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Cerrar" className="rounded-lg p-2 text-muted hover:bg-line/60 hover:text-ink">
            <X size={18} />
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {!caseId && (
            <p className="text-[13.5px] leading-relaxed text-ink-soft">
              Cuando me plantees una decisión en una conversación, aquí verás lo que el consejo estudió y sus dictámenes.
            </p>
          )}
          {error && (
            <p role="alert" className="rounded-lg border border-risk-high-fg/30 bg-risk-high-bg px-3 py-2 text-[13px] text-ink-soft">
              {error}
            </p>
          )}
          {caseId && datos && !datos.disponible && (
            <p className="rounded-lg border border-line bg-surface px-3 py-2 text-[13px] text-ink-soft">
              El consejo no está disponible en este servidor. Puedes seguir viendo y borrando lo que estudió.
            </p>
          )}

          {caseId && seleccionada && (
            <div className="space-y-3">
              <button type="button" onClick={() => setAbierta(null)} className="inline-flex items-center gap-1 text-[13px] font-semibold text-primary-dark hover:underline">
                <ChevronLeft size={14} aria-hidden />
                Todas las sesiones
              </button>
              <ConvocarConsejo caseId={caseId} sesionId={seleccionada.id} onCambio={recargar} />
              {!EN_MARCHA.has(seleccionada.estado) && (
                <button
                  type="button"
                  onClick={() => {
                    if (!window.confirm("¿Borro esta sesión del consejo? Se borran el expediente, las propuestas y el dictamen.")) return;
                    borrarSesion(caseId, seleccionada.id).then(
                      () => {
                        setAbierta(null);
                        recargar();
                      },
                      (err: unknown) => setError(errorMessage(err)),
                    );
                  }}
                  className="inline-flex items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-[12.5px] font-semibold text-ink-soft hover:border-risk-high-fg hover:text-risk-high-fg"
                >
                  <Trash2 size={13} aria-hidden />
                  Borrar esta sesión
                </button>
              )}
            </div>
          )}

          {caseId && !seleccionada && (
            <>
              {datos?.disponible !== false && (
                <Nueva
                  caseId={caseId}
                  onCreada={(id) => {
                    setAbierta(id);
                    recargar();
                  }}
                />
              )}
              {datos && sesiones.length === 0 && (
                <p className="text-[13.5px] leading-relaxed text-ink-soft">
                  Todavía no has consultado nada al consejo. Te lo propondré cuando una decisión lo merezca (elegir entre opciones
                  difíciles de deshacer, importes grandes, mucha incertidumbre), o puedes pedírmelo: «consúltalo con el consejo».
                </p>
              )}
              <ul className="space-y-2" aria-label="Sesiones del consejo">
                {sesiones.map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      onClick={() => setAbierta(s.id)}
                      className="block w-full rounded-xl border border-line bg-surface px-4 py-3 text-left shadow-card transition hover:border-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
                    >
                      <span className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
                        <Insignia sesion={s} />
                        {relativeTime(Date.parse(s.creada))}
                      </span>
                      <span className="mt-1 block text-[14px] font-semibold leading-snug text-ink">{s.pregunta_usuario}</span>
                      {s.recomendacion && (
                        <span className="mt-1 block text-[13px] text-ink-soft">
                          Recomendación: {s.recomendacion}
                          {s.consenso ? ` (${s.consenso})` : ""}
                        </span>
                      )}
                      <span className="mt-1 block text-[12px] text-muted">
                        {s.estado === "pendiente_confirmacion" ? `Coste estimado ${usd(s.estimacion_usd)}` : `Coste ${usd(s.coste_usd)}`}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
        <footer className="border-t border-line px-5 py-3 text-[11.5px] leading-relaxed text-muted">
          El consejo no calcula cifras ni interpreta la ley: usa los cálculos de los especialistas. Sus miembros no reciben tu
          nombre ni datos que la decisión no necesite. Un dictamen es una ayuda para decidir: la decisión es tuya.
        </footer>
      </aside>
    </div>
  );
}
