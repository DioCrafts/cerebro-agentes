// «Tu expediente»: what the advisor has recorded about the user in this conversation. Answers depend on these
// facts, so the user can see every one, correct it in place, or remove it.
//
// Shown as a page (registro.ts: presentacion "pagina"). The facts are a Box of editable rows, as in GitHub's
// settings lists: the value with an edit and a delete button; editing turns the row into a small form;
// deleting asks first.

import { useState } from "react";

import type { Fact, ObligationCandidate } from "../api";
import { STRUCTURED_FACTS } from "../lib/expediente";
import { textoDeValor } from "../lib/guards";
import { factInput, factLabel, factValue, formatDate } from "../lib/presentation";
import { Blankslate, Button, Caja, CounterLabel, Flash, FormControl, IconButton, Select, TextInput } from "../ui";
import { CalendarIcon, CheckIcon, FileDirectoryIcon, PencilIcon, TrashIcon } from "../ui/iconos";
import { DeadlinesSection, DocumentSection } from "./CaseExtras";
import { useConfirmar } from "./useConfirmar";

type FactValue = string | number | boolean;

interface CasePanelProps {
  facts: Fact[];
  taxPeriod: string | null;
  obligations: ObligationCandidate[];
  caseId: string | null;
  // Resolves true when the change was saved. `null` deletes the fact.
  onChangeFact: (factName: string, value: FactValue | null) => Promise<boolean>;
  saving: string | null;
  error: string | null;
  disabled: boolean;
  // Facts that belong to a household module: edited in «Módulos», not here.
  excludeFacts?: ReadonlySet<string>;
}

// Internal flag, not something the user states or needs to manage.
const HIDDEN_FACTS = new Set(["menciona_ingresos"]);

interface EditorProps {
  fact: Pick<Fact, "name" | "value">;
  busy: boolean;
  onSave: (value: FactValue) => void;
  onCancel: () => void;
}

function FactEditor({ fact, busy, onSave, onCancel }: EditorProps) {
  const input = factInput(fact);
  const [draft, setDraft] = useState<string>(() => textoDeValor(fact.value));
  const label = factLabel(fact.name);

  function save() {
    if (input.kind === "choice") {
      const picked = input.options.find((o) => String(o.value) === draft);
      if (picked) onSave(picked.value);
      return;
    }
    const trimmed = draft.trim();
    if (trimmed) onSave(trimmed); // the server parses amounts ("45.000 €") and validates years
  }

  return (
    // Escape inside the editor cancels the edit (the inputs are the interactive part).
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <form
      className="flex flex-col gap-2"
      aria-label={`Editar «${label}»`}
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          // Cancel the edit only; do not let an enclosing sheet or page close.
          e.stopPropagation();
          e.nativeEvent.stopImmediatePropagation();
          onCancel();
        }
      }}
    >
      <FormControl>
        <FormControl.Label>{label}</FormControl.Label>
        {input.kind === "choice" ? (
          // The editor opens on the user's click: focus goes to the box being edited.
          // eslint-disable-next-line jsx-a11y/no-autofocus
          <Select autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} block>
            {input.options.map((o) => (
              <Select.Option key={String(o.value)} value={String(o.value)}>
                {o.label}
              </Select.Option>
            ))}
          </Select>
        ) : (
          <TextInput
            // eslint-disable-next-line jsx-a11y/no-autofocus -- opened by the user's click
            autoFocus
            block
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            inputMode={input.kind === "text" ? "text" : input.kind === "year" ? "numeric" : "decimal"}
            maxLength={input.kind === "year" ? 4 : 120}
            placeholder={input.kind === "money" ? "Importe en euros" : input.kind === "year" ? "Año, p. ej. 2025" : undefined}
          />
        )}
      </FormControl>
      <div className="flex gap-2">
        <Button type="submit" size="small" variant="primary" leadingVisual={CheckIcon} disabled={busy || !draft.trim()} loading={busy}>
          Guardar
        </Button>
        <Button type="button" size="small" onClick={onCancel} disabled={busy}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}

export function CasePanel({ facts, taxPeriod, obligations, caseId, onChangeFact, saving, error, disabled, excludeFacts }: CasePanelProps) {
  // Which row is in edit mode (by fact name).
  const [editing, setEditing] = useState<string | null>(null);
  const confirmar = useConfirmar();

  // The fiscal year lives on the case even before the advisor records it as a
  // fact; show it as the first, always-present row.
  const yearFact: Pick<Fact, "name" | "value"> | null = taxPeriod ? { name: "periodo_fiscal", value: taxPeriod } : null;
  const rows: Array<Pick<Fact, "name" | "value"> & { removable: boolean }> = [
    ...(yearFact ? [{ ...yearFact, removable: false }] : []),
    ...facts
      .filter((f) => f.name !== "periodo_fiscal" && !HIDDEN_FACTS.has(f.name) && !excludeFacts?.has(f.name))
      .map((f) => ({ name: f.name, value: f.value, removable: true })),
  ];
  const locked = disabled || saving !== null || !caseId;

  async function save(name: string, value: FactValue | null) {
    if (await onChangeFact(name, value)) setEditing(null);
  }

  async function remove(name: string, label: string) {
    const si = await confirmar(
      `¿Eliminar «${label}»?`,
      "Se borrará de tu expediente y no lo tendré en cuenta en las próximas respuestas. Si vuelves a contármelo, lo anotaré de nuevo.",
      "Eliminar",
    );
    if (si) await save(name, null);
  }

  return (
    <div className="flex flex-col gap-4">
      <Caja as="section" aria-label="Tus datos">
        <Caja.Cabecera titulo="Tus datos" acciones={rows.length > 0 ? <CounterLabel>{rows.length}</CounterLabel> : undefined} />
        {rows.length === 0 ? (
          <Caja.Cuerpo>
            <Blankslate narrow>
              <Blankslate.Visual>
                <FileDirectoryIcon size={24} />
              </Blankslate.Visual>
              <Blankslate.Heading as="h4">Todavía no he anotado nada</Blankslate.Heading>
              <Blankslate.Description>
                A medida que me cuentes tu situación (dónde resides, qué ingresos tienes, si eres autónomo…), lo verás aquí y podrás
                corregirlo.
              </Blankslate.Description>
            </Blankslate>
          </Caja.Cuerpo>
        ) : (
          <Caja.Lista>
            {rows.map((row) => {
              const label = factLabel(row.name);
              if (editing === row.name) {
                return (
                  <Caja.Fila key={row.name}>
                    <FactEditor fact={row} busy={saving === row.name} onSave={(value) => void save(row.name, value)} onCancel={() => setEditing(null)} />
                  </Caja.Fila>
                );
              }
              // The children's detail, the spouse's figures and the optimiser's preferences are
              // edited in their own screen: here only their summary (in full) and the bin.
              const structured = STRUCTURED_FACTS.has(row.name);
              return (
                <Caja.Fila key={row.name} className="flex items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="m-0 text-xs text-fg-muted">{label}</p>
                    <p className={structured ? "m-0 font-semibold wrap-anywhere" : "m-0 truncate font-semibold"}>{factValue(row)}</p>
                  </div>
                  {saving === row.name && <span className="text-xs text-fg-muted">Guardando…</span>}
                  {!structured && (
                    <IconButton
                      icon={PencilIcon}
                      variant="invisible"
                      size="small"
                      aria-label={`Editar «${label}»`}
                      disabled={locked}
                      onClick={() => setEditing(row.name)}
                    />
                  )}
                  {row.removable && (
                    <IconButton
                      icon={TrashIcon}
                      variant="invisible"
                      size="small"
                      aria-label={`Eliminar «${label}»`}
                      disabled={locked}
                      onClick={() => {
                        setEditing(null);
                        void remove(row.name, label);
                      }}
                    />
                  )}
                </Caja.Fila>
              );
            })}
          </Caja.Lista>
        )}
        {error && (
          <Caja.Cuerpo>
            <Flash variant="danger">{error}</Flash>
          </Caja.Cuerpo>
        )}
        {rows.length > 0 && (
          <Caja.Pie className="text-xs text-fg-muted">
            Si corriges un dato, las posibles obligaciones se recalculan al momento. Las respuestas ya dadas no cambian: vuelve a preguntar
            para que las tenga en cuenta.
          </Caja.Pie>
        )}
      </Caja>

      <DocumentSection caseId={caseId} disabled={disabled || saving !== null} onApply={(name, value) => onChangeFact(name, value)} />

      {/* Re-fetched whenever the facts change: a new fact can add obligations. */}
      <DeadlinesSection caseId={caseId} refreshKey={facts.map((f) => `${f.name}=${textoDeValor(f.value)}`).join("&")} />

      {obligations.length > 0 && (
        <Caja as="section" aria-label="Posibles obligaciones">
          <Caja.Cabecera titulo="Posibles obligaciones" acciones={<CounterLabel>{obligations.length}</CounterLabel>} />
          <Caja.Lista>
            {obligations.map((o) => (
              <Caja.Fila key={o.obligation_id}>
                <p className="m-0 font-semibold">{o.title}</p>
                {o.due_date && (
                  <p className="m-0 mt-1 flex items-center gap-1 text-xs text-fg-muted">
                    <CalendarIcon size={12} />
                    Hasta el {formatDate(o.due_date)}
                  </p>
                )}
              </Caja.Fila>
            ))}
          </Caja.Lista>
        </Caja>
      )}

      <p className="m-0 text-xs text-fg-muted">Estos datos se guardan con tu consulta para no volver a preguntártelos.</p>
    </div>
  );
}
