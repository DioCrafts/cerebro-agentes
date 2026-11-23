// «¿Estás obligado a declarar?»: the answer the calculator gives with every
// individual simulation (art. 96 LIRPF, asistente/ambitos/fiscal/calculo/obligacion.py), the
// rule that decides it with its literal quote, and the questions still open.
// Nothing is decided here: the card only shows the result and edits the facts.

import { OBLIGACION_FIELDS, type ObligacionDeclarar } from "../lib/obligacion";
import { Caja, Flash, Label } from "../ui";
import { LawIcon } from "../ui/iconos";
import { CampoCifra, CampoSelector, CitaLegal, Plegable } from "./calculos/piezas";

const VERDICT = {
  si: { text: "Sí", variant: "danger" },
  no: { text: "No", variant: "success" },
  no_se_sabe: { text: "Falta un dato", variant: "secondary" },
} as const;

const SI_NO = [
  { value: "", label: "Sin indicar" },
  { value: "true", label: "Sí" },
  { value: "false", label: "No" },
];

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
    <Caja as="section" aria-labelledby="obligacion-title">
      <Caja.Cabecera>
        <LawIcon className="shrink-0 text-fg-muted" />
        <h3 id="obligacion-title" className="m-0 min-w-0 flex-1 text-sm font-semibold">
          ¿Estás obligado a declarar?
        </h3>
        <Label data-verdict={obligacion.obligado} variant={verdict.variant}>
          {verdict.text}
        </Label>
      </Caja.Cabecera>
      <Caja.Cuerpo className="flex flex-col gap-3 text-sm">
        <p className="m-0">{obligacion.respuesta}</p>
        {obligacion.recomendacion && <Flash variant={obligacion.devolucion_estimada ? "success" : "default"}>{obligacion.recomendacion}</Flash>}

        {(fields.length > 0 || questions.length > 0) && (
          <div className="grid gap-4 sm:grid-cols-2">
            {fields.map((field) => {
              const pregunta = preguntaDe(field.name);
              const comun = { etiqueta: field.label, valor: value(field.name), onCambio: (raw: string) => onChange(field.name, raw), ayuda: pregunta, cambiado: dirty(field.name), falta: missing.has(field.name) };
              return field.kind === "si_no" ? (
                <CampoSelector key={field.name} {...comun} opciones={SI_NO} className="sm:col-span-2" />
              ) : (
                <CampoCifra key={field.name} {...comun} tipo={field.kind === "importe" ? "dinero" : "numero"} placeholder={field.kind === "importe" ? "0" : "1"} />
              );
            })}
            {questions.map((q) => (
              <p key={q.pregunta} className="m-0 text-fg-muted sm:col-span-2">
                {q.pregunta}
              </p>
            ))}
          </div>
        )}

        {obligacion.regla && (
          <Plegable titulo={`Qué dice la ley · ${obligacion.regla.referencia}`}>
            <div className="flex flex-col gap-2">
              {[obligacion.regla, ...obligacion.otras_reglas].map((regla, i) => (
                <CitaLegal key={i} pie={`${regla.referencia} · ${regla.boe_id}`}>
                  «{regla.cita}»
                </CitaLegal>
              ))}
              {obligacion.avisos.length > 0 && (
                <ul className="m-0 list-disc pl-5 text-xs text-fg-muted">
                  {obligacion.avisos.map((aviso, i) => (
                    <li key={i}>{aviso}</li>
                  ))}
                </ul>
              )}
            </div>
          </Plegable>
        )}
      </Caja.Cuerpo>
    </Caja>
  );
}
