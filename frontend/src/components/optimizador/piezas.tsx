// Small presentational pieces of «Optimizar mis impuestos»: a box of the form, a group, a
// collapsible block, quoted law, a badge and the trace of one tax return.

import { ChevronDown } from "lucide-react";
import clsx from "clsx";
import type { ReactNode } from "react";

import type { Declaracion } from "../../apiOptimizador";
import type { FieldDef } from "../../lib/optimizador/campos";
import { eur, resultadoTexto, splitQuotes } from "../../lib/optimizador/textos";

export const INPUT = "w-full rounded-lg border bg-surface px-3 py-2 text-[16px] text-ink focus:outline-hidden sm:text-[14px]";

export function Field({
  id,
  def,
  value,
  onChange,
  problem,
  dirty,
  readOnlyNote,
}: {
  id: string;
  def: FieldDef;
  value: string;
  onChange: (value: string) => void;
  problem?: string | null;
  dirty?: boolean;
  readOnlyNote?: string;
}) {
  const describedBy = [def.hint ? `${id}-hint` : null, problem ? `${id}-problem` : null].filter(Boolean).join(" ") || undefined;
  if (def.kind === "flag") {
    return (
      <label className="flex items-start gap-2.5 sm:col-span-2">
        <input
          id={id}
          type="checkbox"
          checked={value === "true"}
          onChange={(e) => onChange(String(e.target.checked))}
          aria-describedby={describedBy}
          className="mt-0.5 h-4 w-4 shrink-0 accent-[rgb(var(--primary))]"
        />
        <span>
          <span className={clsx("text-[13.5px]", dirty ? "font-semibold text-primary-dark" : "text-ink")}>{def.label}</span>
          {def.hint && (
            <span id={`${id}-hint`} className="block text-[11.5px] leading-snug text-muted">
              {def.hint}
            </span>
          )}
        </span>
      </label>
    );
  }
  const money = def.kind === "money" || def.kind === "signed";
  return (
    <div>
      <label htmlFor={id} className="block text-[12.5px] leading-snug text-ink-soft">
        {def.label}
      </label>
      <span className="relative mt-1 block">
        <input
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          readOnly={readOnlyNote !== undefined}
          inputMode={money ? "decimal" : "numeric"}
          placeholder="—"
          aria-invalid={problem ? true : undefined}
          aria-describedby={describedBy}
          className={clsx(
            INPUT,
            money && "pr-7",
            readOnlyNote !== undefined && "cursor-default bg-line/40 text-ink-soft",
            problem ? "border-risk-high-fg" : dirty ? "border-primary" : "border-line focus:border-primary",
          )}
        />
        {money && <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[13px] text-muted">€</span>}
        {def.kind === "percent" && <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[13px] text-muted">%</span>}
      </span>
      {problem ? (
        <span id={`${id}-problem`} className="mt-1 block text-[11.5px] font-semibold leading-snug text-risk-high-fg">
          {problem}
        </span>
      ) : readOnlyNote ? (
        <span className="mt-1 block text-[11.5px] leading-snug text-muted">{readOnlyNote}</span>
      ) : (
        def.hint && (
          <span id={`${id}-hint`} className="mt-1 block text-[11.5px] leading-snug text-muted">
            {def.hint}
          </span>
        )
      )}
    </div>
  );
}

export function Group({ title, children, note }: { title: string; children: ReactNode; note?: ReactNode }) {
  return (
    <fieldset className="min-w-0">
      <legend className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">{title}</legend>
      {note && <div className="mb-2.5 text-[12.5px] leading-snug text-ink-soft">{note}</div>}
      <div className="grid gap-3 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

/** A collapsible block whose open state lives in React (a re-render must not undo the user's click). */
export function Collapsible({
  title,
  open,
  onToggle,
  children,
  tone = "plain",
  id,
}: {
  title: ReactNode;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
  tone?: "plain" | "warn";
  id?: string;
}) {
  return (
    <details id={id} open={open} className={clsx("group rounded-xl border", tone === "warn" ? "border-grounding-uncited-bd bg-grounding-uncited-bg" : "border-line bg-surface")}>
      <summary
        onClick={(e) => {
          e.preventDefault();
          onToggle();
        }}
        className={clsx(
          "flex cursor-pointer list-none items-center justify-between gap-2 px-4 py-2.5 text-[13px] font-semibold [&::-webkit-details-marker]:hidden",
          tone === "warn" ? "text-grounding-uncited-fg" : "text-ink-soft",
        )}
      >
        <span className="min-w-0">{title}</span>
        <ChevronDown size={16} aria-hidden className="shrink-0 transition group-open:rotate-180" />
      </summary>
      <div className={clsx("border-t px-4 py-3", tone === "warn" ? "border-grounding-uncited-bd" : "border-line")}>{children}</div>
    </details>
  );
}

/** Text with the literal quotes of the law («…») set apart. */
export function Quoted({ text }: { text: string }) {
  return (
    <>
      {splitQuotes(text).map((part, i) =>
        part.quote ? (
          <span key={i} className="italic text-ink">
            «{part.text}»
          </span>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  );
}

export function Badge({ className, children }: { className: string; children: ReactNode }) {
  return <span className={clsx("inline-flex items-center gap-1 rounded-pill border px-2 py-0.5 text-[11.5px] font-semibold", className)}>{children}</span>;
}

export function Trace({ declaracion, title }: { declaracion: Declaracion; title: string }) {
  const r = declaracion.resultado;
  return (
    <div>
      <p className="mb-1 text-[12.5px] font-semibold text-ink">
        {title} · {resultadoTexto(r.resultado)}
      </p>
      <table className="w-full text-[12.5px]">
        <caption className="sr-only">{title}</caption>
        <tbody>
          {r.lineas.map((line, i) => (
            <tr key={i} className="border-b border-line last:border-0">
              <td className="py-1.5 pr-3 text-ink-soft wrap-anywhere">
                {line.concepto}
                {line.referencia && <span className="block text-[11px] text-muted">{line.referencia}</span>}
              </td>
              <td className="whitespace-nowrap py-1.5 text-right font-mono tabular-nums text-ink">{eur(line.importe)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {r.parametros_no_verificados.length > 0 && (
        <p className="mt-1.5 text-[11.5px] leading-snug text-risk-high-fg">
          <span className="font-semibold">No verificado: </span>
          {r.parametros_no_verificados.join("; ")}
        </p>
      )}
    </div>
  );
}

