// Small presentational pieces of «Optimizar mis impuestos»: one field of the form (from its definition),
// quoted law and the trace of one tax return. The generic pieces (sections, collapsible boxes, tables of
// figures) are the calculators' own, in ../calculos/piezas.

import type { Declaracion } from "../../apiOptimizador";
import type { FieldDef } from "../../lib/optimizador/campos";
import { eur, resultadoTexto, splitQuotes } from "../../lib/optimizador/textos";
import { InlineMessage } from "../../ui";
import { CampoCasilla, CampoCifra, TablaCifras } from "../calculos/piezas";

export function Field({
  id,
  def,
  value,
  onChange,
  problem,
  dirty,
  readOnlyNote,
  className,
}: {
  id: string;
  def: FieldDef;
  value: string;
  onChange: (value: string) => void;
  problem?: string | null;
  dirty?: boolean;
  readOnlyNote?: string;
  className?: string;
}) {
  if (def.kind === "flag") {
    return <CampoCasilla id={id} etiqueta={def.label} ayuda={def.hint} marcada={value === "true"} onCambio={(marcada) => onChange(String(marcada))} cambiado={dirty} className={className} />;
  }
  return (
    <CampoCifra
      id={id}
      etiqueta={def.label}
      tipo={def.kind === "money" || def.kind === "signed" ? "dinero" : def.kind === "percent" ? "porcentaje" : "numero"}
      valor={value}
      onCambio={onChange}
      soloLectura={readOnlyNote !== undefined}
      placeholder="—"
      ayuda={readOnlyNote ?? def.hint}
      error={problem}
      cambiado={dirty}
      className={className}
    />
  );
}

/** Text with the literal quotes of the law («…») set apart. */
export function Quoted({ text }: { text: string }) {
  return (
    <>
      {splitQuotes(text).map((part, i) =>
        part.quote ? (
          <em key={i}>«{part.text}»</em>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  );
}

export function Trace({ declaracion, title }: { declaracion: Declaracion; title: string }) {
  const r = declaracion.resultado;
  return (
    <div className="flex flex-col gap-2">
      <p className="m-0 px-4 pt-3 text-sm font-semibold">
        {title} · {resultadoTexto(r.resultado)}
      </p>
      <TablaCifras titulo={title} filas={r.lineas.map((line) => ({ concepto: line.concepto, referencia: line.referencia, cifras: [eur(line.importe)] }))} />
      {r.parametros_no_verificados.length > 0 && (
        <div className="px-4 pb-3">
          <InlineMessage variant="critical" size="small">
            <span>
              <strong>No verificado: </strong>
              {r.parametros_no_verificados.join("; ")}
            </span>
          </InlineMessage>
        </div>
      )}
    </div>
  );
}
