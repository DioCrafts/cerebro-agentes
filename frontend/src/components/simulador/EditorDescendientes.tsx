// The detail of each child in the simulator: one box per child (date or age, stage, disability,
// adoption and the child's own expenses). The rows are validated in lib/simulador/descendientes.

import { newId } from "../../lib/id";
import { ETAPA_LABEL, factLabel } from "../../lib/presentation";
import { CHILD_EXPENSES, type ChildRow, admitsAssistance, childAge, emptyChild } from "../../lib/simulador/descendientes";
import { Button, Caja, FormControl, Flash, IconButton, TextInput, VisuallyHidden } from "../../ui";
import { PlusIcon, TrashIcon } from "../../ui/iconos";
import { CampoCasilla, CampoCifra, CampoSelector } from "../calculos/piezas";

const ETAPAS = [{ value: "", label: "Sin indicar" }, ...Object.entries(ETAPA_LABEL).map(([value, label]) => ({ value, label }))];

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
    <div className="flex flex-col gap-3 sm:col-span-2">
      <div>
        <p className="m-0 text-sm font-semibold">Detalle de cada hijo</p>
        <p className="m-0 text-xs text-fg-muted">
          Opcional. Con él no hace falta contar los hijos a mano y se aplican bien las deducciones que dependen de la edad, la etapa educativa, la discapacidad o los gastos de cada hijo (los
          máximos «por hijo» se calculan hijo a hijo).
        </p>
      </div>
      {brokenSaved && (
        <Flash variant="danger" role="alert">
          El detalle guardado en tu expediente no se puede leer. Vuelve a introducirlo y guárdalo.
        </Flash>
      )}
      {rows.length > 0 && (
        <ul className="m-0 flex list-none flex-col gap-3 p-0">
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
      <div>
        <Button size="small" leadingVisual={PlusIcon} onClick={() => onChange([...rows, emptyChild(newId())])} disabled={rows.length >= 20}>
          {rows.length === 0 ? "Añadir el detalle de un hijo" : "Añadir otro hijo"}
        </Button>
      </div>
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
  const titulo = `Hijo ${index + 1}`;
  return (
    <Caja as="li" aria-label={titulo} condensada variante={problem ? "peligro" : "normal"}>
      <Caja.Cabecera
        titulo={
          <>
            {titulo}
            {age !== null && age >= 0 && <span className="font-normal text-fg-muted"> · {age === 0 ? `nacido en ${year}` : `${age} ${age === 1 ? "año" : "años"} a 31-12-${year}`}</span>}
          </>
        }
        nivel={4}
        acciones={<IconButton icon={TrashIcon} size="small" variant="invisible" aria-label={`Quitar el hijo ${index + 1}`} onClick={onRemove} />}
      />
      <Caja.Cuerpo className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-3">
          <FormControl>
            <FormControl.Label>Fecha de nacimiento</FormControl.Label>
            <TextInput block type="date" value={row.fecha} max={`${year}-12-31`} onChange={(e) => onChange({ fecha: e.target.value })} />
          </FormControl>
          <CampoCifra
            etiqueta={`o edad a 31-12-${year}`}
            tipo="numero"
            valor={hasDate ? (age !== null && age >= 0 ? String(age) : "") : row.edad}
            onCambio={(edad) => onChange({ edad })}
            soloLectura={hasDate}
            placeholder="0 si nació este año"
          />
          <CampoSelector etiqueta="Etapa educativa" valor={row.etapa} onCambio={(etapa) => onChange({ etapa })} opciones={ETAPAS} />
          <CampoCifra etiqueta="Discapacidad" tipo="porcentaje" valor={row.discapacidad} onCambio={(discapacidad) => onChange({ discapacidad })} placeholder="0" />
          <CampoCifra etiqueta="Meses en guardería este año" tipo="numero" valor={row.mesesGuarderia} onCambio={(mesesGuarderia) => onChange({ mesesGuarderia })} placeholder="—" />
          <CampoCifra etiqueta="Año de adopción (si es adoptado)" tipo="ano" valor={row.adopcion} onCambio={(adopcion) => onChange({ adopcion })} maxLength={4} placeholder="—" />
        </div>
        <div className="grid gap-2">
          <CampoCasilla etiqueta="Parto o adopción múltiple" marcada={row.multiple} onCambio={(multiple) => onChange({ multiple })} />
          {admitsAssistance(row) && (
            <CampoCasilla etiqueta="Necesita ayuda de terceros o tiene movilidad reducida" marcada={row.asistencia} onCambio={(asistencia) => onChange({ asistencia })} />
          )}
          {row.adopcion.trim() !== "" && <CampoCasilla etiqueta="Adopción internacional" marcada={row.internacional} onCambio={(internacional) => onChange({ internacional })} />}
        </div>
        <fieldset className="m-0 min-w-0 border-0 p-0">
          <legend className="mb-2 text-xs font-semibold text-fg-muted">Gastos de este hijo en el año</legend>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {expenses.map((expense) => (
              <CampoCifra
                key={expense.name}
                etiqueta={
                  <>
                    {expense.label}
                    <VisuallyHidden> del hijo {index + 1}</VisuallyHidden>
                  </>
                }
                valor={row.gastos[expense.name] ?? ""}
                onCambio={(valor) => onChange({ gastos: { ...row.gastos, [expense.name]: valor } })}
                placeholder="0"
              />
            ))}
          </div>
        </fieldset>
        {problem && (
          <Flash variant="danger" role="alert">
            {titulo}: {problem}
          </Flash>
        )}
      </Caja.Cuerpo>
    </Caja>
  );
}
