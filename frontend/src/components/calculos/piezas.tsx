// Form and result pieces shared by the tax calculators (simulator, self-employed, optimiser, planner), on
// Primer: dense forms like GitHub's settings pages (FormControl with label, caption and validation) and
// results in bordered boxes like GitHub's tables (rows with dividers, figures aligned to the right).
//
// They live with the calculators, not in src/ui, because only this group uses them (see the migration
// report in docs/ui/sistema-de-diseno.md). Nothing here holds a colour: variants and Primer tokens only.

import { clsx } from "clsx";
import type { ReactNode } from "react";

import { Caja, Checkbox, FormControl, Select, TextInput } from "../../ui";
import { AlertIcon, ChevronDownIcon } from "../../ui/iconos";

/** How a figure is typed: it decides the keyboard on phones and the unit shown inside the box. */
export type TipoCifra = "dinero" | "porcentaje" | "numero" | "ano" | "texto";

const UNIDAD: Partial<Record<TipoCifra, string>> = { dinero: "€", porcentaje: "%" };

/** The border that marks a figure changed on screen (not yet saved) or one the calculation still needs. */
function resalte(cambiado: boolean | undefined, falta: boolean | undefined, error: boolean): string | undefined {
  if (error) return undefined;
  // `!`: Primer's own CSS is unlayered and would otherwise win over Tailwind's utilities.
  if (cambiado) return "!border-border-accent";
  if (falta) return "!border-border-attention";
  return undefined;
}

interface CampoCifraProps {
  id?: string;
  etiqueta: ReactNode;
  valor: string;
  onCambio: (valor: string) => void;
  tipo?: TipoCifra;
  /** Caption under the box (GitHub's grey help text). */
  ayuda?: ReactNode;
  /** What is wrong with the figure: shown as the field's validation, and the box turns red. */
  error?: string | null;
  /** Typed on screen and different from the case: an accent border. */
  cambiado?: boolean;
  /** The calculation asks for it: an attention border. */
  falta?: boolean;
  soloLectura?: boolean;
  placeholder?: string;
  maxLength?: number;
  /** The label is for screen readers only (a table of figures already says what each box is). */
  etiquetaOculta?: boolean;
  className?: string;
}

/** One figure of a form: label, box (with its unit), caption and validation. */
export function CampoCifra({
  id,
  etiqueta,
  valor,
  onCambio,
  tipo = "dinero",
  ayuda,
  error,
  cambiado,
  falta,
  soloLectura,
  placeholder,
  maxLength,
  etiquetaOculta,
  className,
}: CampoCifraProps) {
  return (
    <FormControl id={id} className={className}>
      <FormControl.Label visuallyHidden={etiquetaOculta}>{etiqueta}</FormControl.Label>
      <TextInput
        block
        value={valor}
        onChange={(e) => onCambio(e.target.value)}
        readOnly={soloLectura}
        inputMode={tipo === "dinero" || tipo === "porcentaje" ? "decimal" : tipo === "texto" ? undefined : "numeric"}
        placeholder={placeholder}
        maxLength={maxLength}
        trailingVisual={UNIDAD[tipo]}
        validationStatus={error ? "error" : undefined}
        className={resalte(cambiado, falta, Boolean(error))}
      />
      {ayuda && <FormControl.Caption>{ayuda}</FormControl.Caption>}
      {error && <FormControl.Validation variant="error">{error}</FormControl.Validation>}
    </FormControl>
  );
}

interface CampoSelectorProps {
  id?: string;
  etiqueta: ReactNode;
  valor: string;
  onCambio: (valor: string) => void;
  opciones: ReadonlyArray<{ value: string; label: string }>;
  ayuda?: ReactNode;
  error?: string | null;
  cambiado?: boolean;
  falta?: boolean;
  className?: string;
}

/** A choice from a list: label, native select (the phone's own picker), caption and validation. */
export function CampoSelector({ id, etiqueta, valor, onCambio, opciones, ayuda, error, cambiado, falta, className }: CampoSelectorProps) {
  return (
    <FormControl id={id} className={className}>
      <FormControl.Label>{etiqueta}</FormControl.Label>
      <Select block value={valor} onChange={(e) => onCambio(e.target.value)} validationStatus={error ? "error" : undefined} className={resalte(cambiado, falta, Boolean(error))}>
        {opciones.map((o) => (
          <Select.Option key={o.value} value={o.value}>
            {o.label}
          </Select.Option>
        ))}
      </Select>
      {ayuda && <FormControl.Caption>{ayuda}</FormControl.Caption>}
      {error && <FormControl.Validation variant="error">{error}</FormControl.Validation>}
    </FormControl>
  );
}

interface CampoCasillaProps {
  id?: string;
  etiqueta: ReactNode;
  marcada: boolean;
  onCambio: (marcada: boolean) => void;
  ayuda?: ReactNode;
  /** Typed on screen and different from the case: the label in bold. */
  cambiado?: boolean;
  className?: string;
}

/** A yes/no question: checkbox, label to its right and caption under it. Takes the full row of a grid. */
export function CampoCasilla({ id, etiqueta, marcada, onCambio, ayuda, cambiado, className }: CampoCasillaProps) {
  return (
    <FormControl id={id} className={clsx("sm:col-span-2", className)}>
      <Checkbox checked={marcada} onChange={(e) => onCambio(e.target.checked)} />
      <FormControl.Label className={cambiado ? "font-semibold" : "font-normal"}>{etiqueta}</FormControl.Label>
      {ayuda && <FormControl.Caption>{ayuda}</FormControl.Caption>}
    </FormControl>
  );
}

/**
 * A group of fields under a heading with a rule, like a section of GitHub's settings («Subhead»). The fields
 * go on a two-column grid from `sm`; a field takes both with `sm:col-span-2`.
 */
export function SeccionFormulario({ titulo, nota, children, className }: { titulo: ReactNode; nota?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <fieldset className={clsx("m-0 min-w-0 border-0 p-0", className)}>
      <legend className="mb-3 w-full border-b border-border-default pb-2 text-base font-semibold">{titulo}</legend>
      {nota && <div className="mb-3 text-sm text-fg-muted">{nota}</div>}
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

interface PlegableProps {
  titulo: ReactNode;
  children: ReactNode;
  /** Controlled: the open state lives in React, so a re-render (a new result arriving) does not undo a click. */
  abierto?: boolean;
  onAlternar?: () => void;
  /** Uncontrolled: whether it starts open. */
  abiertoAlInicio?: boolean;
  /** "aviso": things the user must know (an attention header with an alert icon). */
  tono?: "normal" | "aviso";
  /** The body without padding (a table of figures that brings its own). */
  sinRelleno?: boolean;
  id?: string;
  className?: string;
}

/** A box whose body opens and closes from its header (GitHub's collapsible box). */
export function Plegable({ titulo, children, abierto, onAlternar, abiertoAlInicio, tono = "normal", sinRelleno, id, className }: PlegableProps) {
  const controlado = onAlternar !== undefined;
  return (
    <details
      id={id}
      open={controlado ? abierto : abiertoAlInicio}
      className={clsx(
        "group/plegable min-w-0 rounded-medium border bg-canvas-default",
        tono === "aviso" ? "border-border-attention-muted" : "border-border-default",
        className,
      )}
    >
      <summary
        onClick={
          controlado
            ? (e) => {
                e.preventDefault();
                onAlternar();
              }
            : undefined
        }
        className={clsx(
          "flex cursor-pointer list-none items-center gap-2 rounded-medium px-4 py-2 text-sm font-semibold group-open/plegable:rounded-b-none [&::-webkit-details-marker]:hidden",
          tono === "aviso" ? "bg-attention-muted" : "bg-canvas-muted",
        )}
      >
        {tono === "aviso" && <AlertIcon className="shrink-0 text-fg-attention" />}
        <span className="min-w-0 flex-1">{titulo}</span>
        <ChevronDownIcon className="shrink-0 text-fg-muted transition-transform group-open/plegable:rotate-180" />
      </summary>
      <div className={clsx("border-t text-sm", !sinRelleno && "px-4 py-3", tono === "aviso" ? "border-border-attention-muted" : "border-border-default")}>{children}</div>
    </details>
  );
}

export interface FilaCifras {
  concepto: ReactNode;
  /** The legal reference of the line, under the concept. */
  referencia?: string | null;
  /** One figure per column, already written («1.082,46 €»). */
  cifras: ReactNode[];
  /** The total or the plan's row: in bold. */
  destacada?: boolean;
}

/**
 * Lines of a calculation as a GitHub table: concept on the left (with its legal reference), figures on the
 * right in tabular digits, one divider per row. Put it inside a `Caja` (or a `Plegable`) for the border.
 */
export function TablaCifras({ titulo, columnas, filas, className }: { titulo: string; columnas?: string[]; filas: FilaCifras[]; className?: string }) {
  return (
    <table className={clsx("w-full border-collapse text-sm", className)}>
      <caption className="sr-only">{titulo}</caption>
      {columnas && (
        <thead>
          <tr className="text-xs text-fg-muted">
            {columnas.map((c, i) => (
              <th key={c} scope="col" className={clsx("px-4 py-2 font-semibold", i === 0 ? "text-left" : "text-right")}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
      )}
      <tbody>
        {filas.map((fila, i) => (
          <tr key={i} className={clsx("align-top", (i > 0 || columnas) && "border-t border-border-muted")}>
            <th scope="row" className={clsx("px-4 py-2 text-left wrap-anywhere", fila.destacada ? "font-semibold" : "font-normal")}>
              {fila.concepto}
              {fila.referencia && <span className="block text-xs font-normal text-fg-muted">{fila.referencia}</span>}
            </th>
            {fila.cifras.map((cifra, j) => (
              <td key={j} className={clsx("whitespace-nowrap px-4 py-2 text-right font-mono tabular-nums", fila.destacada && "font-semibold")}>
                {cifra}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface ResultadoProps {
  titulo: ReactNode;
  /** The figure, already written. */
  cifra: ReactNode;
  /** «a pagar», «a devolver», «al año»… */
  sufijo?: ReactNode;
  /** Good news (nothing to pay, money back): the figure in the success colour. */
  favorable?: boolean;
  children?: ReactNode;
  /** Stays on screen while the form scrolls (in the sidebar on wide screens). */
  fijo?: boolean;
  className?: string;
}

/** The headline result of a calculator: a box with the figure large, announced when it changes. */
export function Resultado({ titulo, cifra, sufijo, favorable, children, fijo, className }: ResultadoProps) {
  return (
    <Caja as="section" aria-live="polite" aria-label="Resultado" className={clsx(fijo && "z-10 shadow-resting-sm lg:sticky lg:top-4", className)}>
      <Caja.Cabecera titulo={titulo} />
      <Caja.Cuerpo className="flex flex-col gap-2">
        <p className="m-0 flex flex-wrap items-baseline gap-x-2">
          <span className={clsx("text-3xl font-semibold tabular-nums", favorable && "text-fg-success")}>{cifra}</span>
          {sufijo && <span className="text-sm font-semibold text-fg-muted">{sufijo}</span>}
        </p>
        {children}
      </Caja.Cuerpo>
    </Caja>
  );
}

/** Literal quotes of the law: a quote block with GitHub's left rule. */
export function CitaLegal({ children, pie }: { children: ReactNode; pie?: ReactNode }) {
  return (
    <blockquote className="m-0 border-l-4 border-border-default pl-3 text-sm text-fg-muted">
      {children}
      {pie && <span className="mt-1 block text-xs">{pie}</span>}
    </blockquote>
  );
}
