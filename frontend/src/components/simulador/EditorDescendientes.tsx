// The detail of each child in the simulator: one card per child (date or age, stage, disability,
// adoption and the child's own expenses). The rows are validated in lib/simulador/descendientes.

import { Plus, Trash2 } from "lucide-react";
import clsx from "clsx";
import type { ReactNode } from "react";

import { newId } from "../../lib/id";
import { ETAPA_LABEL, factLabel } from "../../lib/presentation";
import { CHILD_EXPENSES, type ChildRow, admitsAssistance, childAge, emptyChild } from "../../lib/simulador/descendientes";

const SMALL_INPUT_CLASS = "w-full rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[16px] text-ink focus:border-primary focus:outline-none sm:text-[13.5px]";

interface Props {
  rows: ChildRow[];
  /** What is wrong with each row (same order), or null. */
  problems: Array<string | null>;
  year: number;
  /** The saved detail cannot be read and has not been replaced yet. */
  brokenSaved: boolean;
  onChange: (rows: ChildRow[]) => void;
}

export function EditorDescendientes({ rows, problems, year, brokenSaved, onChange }: Props) {
  return (
  <div className="sm:col-span-2">
    <p className="text-[12.5px] font-semibold text-ink">Detalle de cada hijo</p>
    <p className="mt-0.5 text-[11.5px] leading-snug text-muted">
      Opcional. Con él no hace falta contar los hijos a mano y se aplican bien las deducciones que dependen de la edad, la etapa educativa, la discapacidad o los gastos de cada hijo (los
      máximos «por hijo» se calculan hijo a hijo).
    </p>
    {brokenSaved && (
      <p role="alert" className="mt-1.5 text-[12px] font-semibold leading-snug text-risk-high-fg">
        El detalle guardado en tu expediente no se puede leer. Vuelve a introducirlo y guárdalo.
      </p>
    )}
    {rows.length > 0 && (
      <ul className="mt-2 space-y-2.5">
        {rows.map((row, index) => (
          <FilaHijo
            key={row.id}
            row={row}
            index={index}
            year={year}
            problem={problems[index] ?? null}
            onChange={(patch) => onChange(rows.map((r) => (r.id === row.id ? { ...r, ...patch } : r)))}
            onRemove={() => onChange(rows.filter((r) => r.id !== row.id))}
          />
        ))}
      </ul>
    )}
    <button
      type="button"
      onClick={() => onChange([...rows, emptyChild(newId())])}
      disabled={rows.length >= 20}
      className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-[12.5px] font-semibold text-primary-dark transition hover:border-primary disabled:cursor-not-allowed disabled:opacity-50"
    >
      <Plus size={14} aria-hidden />
      {rows.length === 0 ? "Añadir el detalle de un hijo" : "Añadir otro hijo"}
    </button>
  </div>
);
}

interface FilaProps {
  row: ChildRow;
  index: number;
  year: number;
  problem: string | null;
  onChange: (patch: Partial<ChildRow>) => void;
  onRemove: () => void;
}

function FilaHijo({ row, index, year, problem, onChange, onRemove }: FilaProps) {
  const hasDate = row.fecha !== "";
  const age = childAge(row, year);
  const expenses = [...CHILD_EXPENSES, ...Object.keys(row.gastos).filter((name) => !CHILD_EXPENSES.some((e) => e.name === name)).map((name) => ({ name, label: factLabel(name) }))];
  const small = (label: string, node: ReactNode) => (
    <label className="block">
      <span className="text-[12px] text-ink-soft">{label}</span>
      <span className="mt-0.5 block">{node}</span>
    </label>
  );
  return (
    <li className="rounded-xl border border-line bg-surface px-3 py-3">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-[13px] font-semibold text-ink">
          Hijo {index + 1}
          {age !== null && age >= 0 && <span className="font-normal text-muted"> · {age === 0 ? `nacido en ${year}` : `${age} ${age === 1 ? "año" : "años"} a 31-12-${year}`}</span>}
        </p>
        <button type="button" onClick={onRemove} aria-label={`Quitar el hijo ${index + 1}`} title="Quitar" className="rounded-lg p-1.5 text-muted transition hover:bg-risk-high-bg hover:text-risk-high-fg">
          <Trash2 size={15} aria-hidden />
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        {small("Fecha de nacimiento", <input type="date" value={row.fecha} max={`${year}-12-31`} onChange={(e) => onChange({ fecha: e.target.value })} className={SMALL_INPUT_CLASS} />)}
        {small(
          `o edad a 31-12-${year}`,
          <input
            value={hasDate ? (age !== null && age >= 0 ? String(age) : "") : row.edad}
            onChange={(e) => onChange({ edad: e.target.value })}
            readOnly={hasDate}
            inputMode="numeric"
            placeholder="0 si nació este año"
            className={clsx(SMALL_INPUT_CLASS, hasDate && "cursor-default bg-line/40 text-ink-soft")}
          />,
        )}
        {small(
          "Etapa educativa",
          <select value={row.etapa} onChange={(e) => onChange({ etapa: e.target.value })} className={SMALL_INPUT_CLASS}>
            <option value="">Sin indicar</option>
            {Object.entries(ETAPA_LABEL).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>,
        )}
        {small("Discapacidad (%)", <input value={row.discapacidad} onChange={(e) => onChange({ discapacidad: e.target.value })} inputMode="numeric" placeholder="0" className={SMALL_INPUT_CLASS} />)}
        {small("Meses en guardería este año", <input value={row.mesesGuarderia} onChange={(e) => onChange({ mesesGuarderia: e.target.value })} inputMode="numeric" placeholder="—" className={SMALL_INPUT_CLASS} />)}
        {small("Año de adopción (si es adoptado)", <input value={row.adopcion} onChange={(e) => onChange({ adopcion: e.target.value })} inputMode="numeric" maxLength={4} placeholder="—" className={SMALL_INPUT_CLASS} />)}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5">
        <label className="flex items-center gap-2 text-[12.5px] text-ink">
          <input type="checkbox" checked={row.multiple} onChange={(e) => onChange({ multiple: e.target.checked })} className="h-4 w-4 accent-[rgb(var(--primary))]" />
          Parto o adopción múltiple
        </label>
        {admitsAssistance(row) && (
          <label className="flex items-center gap-2 text-[12.5px] text-ink">
            <input type="checkbox" checked={row.asistencia} onChange={(e) => onChange({ asistencia: e.target.checked })} className="h-4 w-4 accent-[rgb(var(--primary))]" />
            Necesita ayuda de terceros o tiene movilidad reducida
          </label>
        )}
        {row.adopcion.trim() !== "" && (
          <label className="flex items-center gap-2 text-[12.5px] text-ink">
            <input type="checkbox" checked={row.internacional} onChange={(e) => onChange({ internacional: e.target.checked })} className="h-4 w-4 accent-[rgb(var(--primary))]" />
            Adopción internacional
          </label>
        )}
      </div>
      <p className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted">Gastos de este hijo en el año</p>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
        {expenses.map((expense) => (
          <label key={expense.name} className="block">
            <span className="text-[12px] text-ink-soft">{expense.label}</span>
            <span className="relative mt-0.5 block">
              <input
                value={row.gastos[expense.name] ?? ""}
                onChange={(e) => onChange({ gastos: { ...row.gastos, [expense.name]: e.target.value } })}
                inputMode="decimal"
                placeholder="0"
                aria-label={`${expense.label} del hijo ${index + 1}`}
                className={clsx(SMALL_INPUT_CLASS, "pr-6")}
              />
              <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[12px] text-muted">€</span>
            </span>
          </label>
        ))}
      </div>
      {problem && (
        <p role="alert" className="mt-2 text-[12px] font-semibold leading-snug text-risk-high-fg">
          Hijo {index + 1}: {problem}
        </p>
      )}
    </li>
  );
}
