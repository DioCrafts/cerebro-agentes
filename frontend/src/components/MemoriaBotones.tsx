// «No me interesa» / «Ya lo hice» / «Recuérdamelo más tarde» on a notice or on a lever of the
// optimiser's plan. The answer is remembered (hacienda_gpt/memoria): the assistant will not propose it
// again unless something important changes, and then only once, saying what changed.

import { AlarmClock, CheckCheck, ThumbsDown } from "lucide-react";
import { useState } from "react";

import { errorMessage, isoEnDias } from "../lib/memoria";
import { crearDecision, responderAviso, type BotonAviso, type DecisionResponse } from "../apiMemoria";

interface Palanca {
  id: string;
  opcion?: string | null;
  titulo: string;
  importe?: string | null;
}

interface MemoriaBotonesProps {
  caseId: string;
  /** A notice of the inbox… */
  notificationId?: string;
  /** …or a lever of the optimiser's plan (no «remind me later»: the plan is computed on demand). */
  palanca?: Palanca;
  onDone: (resp: DecisionResponse) => void;
}

const BOTONES: Array<{ boton: BotonAviso; label: string; icon: typeof ThumbsDown }> = [
  { boton: "no_me_interesa", label: "No me interesa", icon: ThumbsDown },
  { boton: "ya_lo_hice", label: "Ya lo hice", icon: CheckCheck },
  { boton: "recuerdamelo", label: "Recuérdamelo más tarde", icon: AlarmClock },
];

const PLAZOS: Array<{ dias: number; label: string }> = [
  { dias: 7, label: "dentro de una semana" },
  { dias: 30, label: "dentro de un mes" },
  { dias: 90, label: "dentro de tres meses" },
];

export function MemoriaBotones({ caseId, notificationId, palanca, onDone }: MemoriaBotonesProps) {
  const [abierto, setAbierto] = useState<BotonAviso | null>(null);
  const [razon, setRazon] = useState("");
  const [dias, setDias] = useState(7);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const botones = palanca ? BOTONES.filter((b) => b.boton !== "recuerdamelo") : BOTONES;

  async function guardar() {
    if (!abierto) return;
    setEnviando(true);
    setError(null);
    try {
      const texto = razon.trim() || undefined;
      const resp = notificationId
        ? await responderAviso(caseId, notificationId, {
            boton: abierto,
            razon: texto,
            hasta: abierto === "recuerdamelo" ? isoEnDias(dias) : undefined,
          })
        : await crearDecision(caseId, {
            palanca: palanca?.id,
            opcion: palanca?.opcion ?? null,
            boton: abierto === "ya_lo_hice" ? "ya_lo_hice" : "no_me_interesa",
            razon: texto,
            propuesta: palanca ? { titulo: palanca.titulo, importe: palanca.importe ?? null } : undefined,
          });
      setAbierto(null);
      setRazon("");
      onDone(resp);
    } catch (err) {
      setError(`No se ha podido guardar. ${errorMessage(err)}`);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="mt-2">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Tu respuesta a esta propuesta">
        {botones.map(({ boton, label, icon: Icon }) => (
          <button
            key={boton}
            type="button"
            onClick={() => setAbierto(abierto === boton ? null : boton)}
            aria-pressed={abierto === boton}
            className="inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-[12px] font-semibold text-ink-soft transition hover:border-primary hover:text-primary-dark aria-pressed:border-primary aria-pressed:text-primary-dark"
          >
            <Icon size={13} aria-hidden />
            {label}
          </button>
        ))}
      </div>
      {abierto && (
        <div className="mt-2 space-y-2 rounded-lg border border-line bg-paper px-3 py-2.5">
          {abierto === "recuerdamelo" ? (
            <label className="block text-[12.5px] text-ink-soft">
              Vuelve a avisarme{" "}
              <select value={dias} onChange={(e) => setDias(Number(e.target.value))} className="rounded border border-line bg-surface px-1.5 py-0.5 text-[12.5px] text-ink">
                {PLAZOS.map((p) => (
                  <option key={p.dias} value={p.dias}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label className="block text-[12.5px] text-ink-soft">
              {abierto === "no_me_interesa" ? "¿Por qué? (opcional, con tus palabras)" : "¿Algo que deba saber? (opcional)"}
              <input
                type="text"
                value={razon}
                maxLength={300}
                onChange={(e) => setRazon(e.target.value)}
                placeholder={abierto === "no_me_interesa" ? "p. ej. tengo permanencia hasta marzo" : ""}
                className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1 text-[13px] text-ink"
              />
            </label>
          )}
          <p className="text-[11.5px] leading-snug text-muted">
            {abierto === "recuerdamelo"
              ? "Hasta entonces no te lo volveré a proponer."
              : abierto === "no_me_interesa"
                ? "No te lo volveré a proponer durante un año, salvo que cambie algo importante (y entonces solo una vez, diciéndote qué ha cambiado)."
                : "Lo tendré en cuenta y no te lo volveré a proponer en los próximos meses."}{" "}
            Puedes cambiarlo en «Lo que sé de tus preferencias».
          </p>
          <div className="flex gap-2">
            <button type="button" onClick={() => void guardar()} disabled={enviando} className="rounded-lg bg-primary px-2.5 py-1 text-[12.5px] font-semibold text-white disabled:opacity-50">
              {enviando ? "Guardando…" : "Guardar"}
            </button>
            <button type="button" onClick={() => setAbierto(null)} className="rounded-lg px-2.5 py-1 text-[12.5px] font-semibold text-ink-soft hover:bg-line/60">
              Cancelar
            </button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="mt-1.5 text-[12px] text-risk-high-fg">
          {error}
        </p>
      )}
    </div>
  );
}
