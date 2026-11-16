// «¿Estás obligado a declarar?»: the answer the calculator gives with every
// individual simulation (art. 96 LIRPF, asistente/ambitos/fiscal/calculo/obligacion.py), the
// rule that decides it with its literal quote, and the questions still open.
// Nothing is decided here: the card only shows the result and edits the facts.

import { ChevronDown, Scale } from "lucide-react";
import clsx from "clsx";

import { OBLIGACION_FIELDS, type ObligacionDeclarar } from "../lib/obligacion";

const VERDICT = {
  si: { text: "Sí", className: "bg-risk-high-bg text-risk-high-fg" },
  no: { text: "No", className: "bg-grounding-cited-bg text-grounding-cited-fg" },
  no_se_sabe: { text: "Falta un dato", className: "bg-grounding-abstain-bg text-grounding-abstain-fg" },
} as const;

const SELECT_CLASS = "mt-1 w-full rounded-lg border bg-surface px-2.5 py-1.5 text-[16px] text-ink focus:border-primary focus:outline-none sm:text-[13.5px]";

interface Props {
  obligacion: ObligacionDeclarar;
  // Current raw value of a fact (edit, else saved, else "") and its setter; `dirty` marks an edited one.
  value: (name: string) => string;
  onChange: (name: string, raw: string) => void;
  dirty: (name: string) => boolean;
}

export function ObligacionDeclararCard({ obligacion, value, onChange, dirty }: Props) {
  const verdict = VERDICT[obligacion.obligado];
  const missing = new Set(obligacion.datos_que_faltan.flatMap((d) => (d.campo ? [d.campo] : [])));
  const questions = obligacion.datos_que_faltan.filter((d) => d.campo === null);
  // What is missing is asked; what was already answered stays editable.
  const fields = OBLIGACION_FIELDS.filter((f) => missing.has(f.name) || value(f.name) !== "");
  const preguntaDe = (name: string) => obligacion.datos_que_faltan.find((d) => d.campo === name)?.pregunta;

  return (
    <section aria-labelledby="obligacion-title" className="rounded-xl border border-line bg-surface px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <h3 id="obligacion-title" className="flex items-center gap-1.5 text-[14px] font-semibold text-ink">
          <Scale size={15} aria-hidden className="text-primary-dark" />
          ¿Estás obligado a declarar?
        </h3>
        <span data-verdict={obligacion.obligado} className={clsx("flex-shrink-0 rounded-pill px-2 py-0.5 text-[12px] font-bold", verdict.className)}>{verdict.text}</span>
      </div>
      <p className="mt-1.5 text-[13px] leading-snug text-ink-soft">{obligacion.respuesta}</p>
      {obligacion.recomendacion && (
        <p className={clsx("mt-2 rounded-lg px-3 py-1.5 text-[12.5px] leading-snug", obligacion.devolucion_estimada ? "bg-grounding-cited-bg font-semibold text-grounding-cited-fg" : "bg-line/40 text-ink-soft")}>
          {obligacion.recomendacion}
        </p>
      )}

      {(fields.length > 0 || questions.length > 0) && (
        <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
          {fields.map((field) => {
            const pregunta = preguntaDe(field.name);
            const current = value(field.name);
            const border = dirty(field.name) ? "border-primary" : missing.has(field.name) ? "border-grounding-abstain-fg" : "border-line";
            return (
              <label key={field.name} className={clsx("block", field.kind === "si_no" && "sm:col-span-2")}>
                <span className="text-[12.5px] text-ink-soft">{field.label}</span>
                {field.kind === "si_no" ? (
                  <select value={current} onChange={(e) => onChange(field.name, e.target.value)} className={clsx(SELECT_CLASS, border)}>
                    <option value="">Sin indicar</option>
                    <option value="true">Sí</option>
                    <option value="false">No</option>
                  </select>
                ) : (
                  <input
                    value={current}
                    onChange={(e) => onChange(field.name, e.target.value)}
                    inputMode={field.kind === "importe" ? "decimal" : "numeric"}
                    placeholder={field.kind === "importe" ? "0 €" : "1"}
                    className={clsx(SELECT_CLASS, border)}
                  />
                )}
                {pregunta && <span className="mt-1 block text-[11.5px] leading-snug text-muted">{pregunta}</span>}
              </label>
            );
          })}
          {questions.map((q) => (
            <p key={q.pregunta} className="text-[12px] leading-snug text-muted sm:col-span-2">
              {q.pregunta}
            </p>
          ))}
        </div>
      )}

      {obligacion.regla && (
        <details className="group mt-2.5">
          <summary className="flex cursor-pointer list-none items-center gap-1 text-[12.5px] font-semibold text-primary-dark [&::-webkit-details-marker]:hidden">
            <ChevronDown size={14} aria-hidden className="transition group-open:rotate-180" />
            Qué dice la ley · {obligacion.regla.referencia}
          </summary>
          {[obligacion.regla, ...obligacion.otras_reglas].map((regla, i) => (
            <blockquote key={i} className="mt-1.5 border-l-2 border-line pl-3 text-[12px] leading-snug text-ink-soft">
              «{regla.cita}»<span className="mt-0.5 block text-[11px] text-muted">{regla.referencia} · {regla.boe_id}</span>
            </blockquote>
          ))}
          {obligacion.avisos.length > 0 && (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-[11.5px] leading-snug text-muted">
              {obligacion.avisos.map((aviso, i) => (
                <li key={i}>{aviso}</li>
              ))}
            </ul>
          )}
        </details>
      )}
    </section>
  );
}
