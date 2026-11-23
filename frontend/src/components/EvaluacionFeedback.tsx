// Feedback under each answer: 👍 / 👎 and «Corregir» (what was wrong, and optionally the right answer or
// figure). Storing the conversation for evaluation is a separate, unticked checkbox with its consent text,
// for THIS answer only; without it only the vote is recorded (unless the user switched on «aprender de los
// fallos» for the case, in «Revisión de casos»). A stored 👎 becomes a masked candidate case the user can
// review there; a stored conversation can be withdrawn here.
//
// It sits in the footer of the answer's comment, like GitHub's reactions: two icon buttons and «Corregir», which
// opens a small form in a Box (Primer FormControl, RadioGroup, Textarea, Checkbox).

import { useState } from "react";

import { ApiError } from "../api";
import type { ConsentimientoEvaluacion, FeedbackRespuesta, TipoCorreccion } from "../apiEvaluacion";
import { enviarFeedback, getConsentimientoEvaluacion, retirarFeedback } from "../apiEvaluacion";
import { Button, Checkbox, Flash, FormControl, IconButton, Radio, RadioGroup, Textarea, TextInput } from "../ui";
import { PencilIcon, ThumbsdownIcon, ThumbsupIcon } from "../ui/iconos";

interface Props {
  caseId: string | null;
  question: string;
  answer: string;
}

type Estado = "inicio" | "formulario" | "enviando" | "enviado";

const TIPOS_POR_DEFECTO: ConsentimientoEvaluacion["tipos"] = [
  { clave: "cifra_erronea", etiqueta: "Cifra equivocada" },
  { clave: "modulo_erroneo", etiqueta: "Ha consultado el ámbito equivocado" },
  { clave: "hecho_omitido", etiqueta: "Ha pasado por alto un dato" },
  { clave: "afirmacion_inventada", etiqueta: "Afirma algo que no es cierto" },
  { clave: "suma_indebida", etiqueta: "Ha sumado cifras que no se pueden sumar" },
  { clave: "memoria_erronea", etiqueta: "Recuerda mal algo que le dije" },
  { clave: "otro", etiqueta: "Otro" },
];

function mensajeDeError(err: unknown): string {
  return err instanceof ApiError ? err.message : "No se ha podido enviar la valoración.";
}

export function EvaluacionFeedback({ caseId, question, answer }: Props) {
  const [estado, setEstado] = useState<Estado>("inicio");
  const [consentimiento, setConsentimiento] = useState<ConsentimientoEvaluacion | null>(null);
  const [tipo, setTipo] = useState<TipoCorreccion | null>(null);
  const [comentario, setComentario] = useState("");
  const [respuestaCorrecta, setRespuestaCorrecta] = useState("");
  const [cifraCorrecta, setCifraCorrecta] = useState("");
  const [guardar, setGuardar] = useState(false);
  const [resultado, setResultado] = useState<FeedbackRespuesta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retirado, setRetirado] = useState(false);

  const abrirFormulario = () => {
    setEstado("formulario");
    setError(null);
    if (consentimiento === null) {
      getConsentimientoEvaluacion()
        .then(setConsentimiento)
        .catch(() => setConsentimiento(null));
    }
  };

  const enviar = (valoracion: "positiva" | "negativa") => {
    setEstado("enviando");
    setError(null);
    enviarFeedback({
      caseId,
      pregunta: question,
      respuesta: answer,
      valoracion,
      tipo,
      comentario,
      respuestaCorrecta,
      cifraCorrecta,
      versionConsentimiento: valoracion === "negativa" && guardar && consentimiento ? consentimiento.version : null,
    })
      .then((r) => {
        setResultado(r);
        setEstado("enviado");
      })
      .catch((err: unknown) => {
        setError(mensajeDeError(err));
        setEstado(valoracion === "negativa" ? "formulario" : "inicio");
      });
  };

  const retirar = () => {
    if (!resultado) return;
    retirarFeedback(resultado.id)
      .then(() => setRetirado(true))
      .catch((err: unknown) => setError(mensajeDeError(err)));
  };

  if (estado === "enviado" && resultado) {
    return (
      <div role="status" className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
        <span>{retirado ? "Has retirado la conversación: ya no se guarda para evaluación ni como caso a revisar." : resultado.mensaje}</span>
        {resultado.guardado_para_evaluacion && !retirado && (
          <Button size="small" variant="invisible" onClick={retirar}>
            Retirar
          </Button>
        )}
        {error && <span className="text-fg-danger">{error}</span>}
      </div>
    );
  }

  const tipos = consentimiento?.tipos ?? TIPOS_POR_DEFECTO;
  const ocupado = estado === "enviando";

  return (
    <div>
      <div role="group" className="flex flex-wrap items-center gap-1" aria-label="Valora esta respuesta">
        <span className="mr-1 text-xs text-fg-muted">¿Te ha sido útil?</span>
        <IconButton icon={ThumbsupIcon} size="small" variant="invisible" disabled={ocupado} onClick={() => enviar("positiva")} aria-label="Sí, útil" />
        <IconButton icon={ThumbsdownIcon} size="small" variant="invisible" disabled={ocupado} onClick={abrirFormulario} aria-label="No, no es correcta" />
        <Button size="small" variant="invisible" leadingVisual={PencilIcon} disabled={ocupado} onClick={abrirFormulario}>
          Corregir
        </Button>
      </div>

      {(estado === "formulario" || ocupado) && (
        <form
          aria-label="Corregir la respuesta"
          className="mt-2 flex flex-col gap-3 rounded-medium border border-border-default bg-canvas-default p-4"
          onSubmit={(event) => {
            event.preventDefault();
            enviar("negativa");
          }}
        >
          <RadioGroup name="tipo-correccion" onChange={(valor) => setTipo(valor as TipoCorreccion | null)}>
            <RadioGroup.Label>¿Qué estaba mal?</RadioGroup.Label>
            {tipos.map((t) => (
              <FormControl key={t.clave}>
                <Radio value={t.clave} checked={tipo === t.clave} onChange={() => setTipo(t.clave)} />
                <FormControl.Label>{t.etiqueta}</FormControl.Label>
              </FormControl>
            ))}
          </RadioGroup>
          <FormControl>
            <FormControl.Label>Explícalo (opcional)</FormControl.Label>
            <Textarea block value={comentario} onChange={(e) => setComentario(e.target.value)} maxLength={2000} rows={2} />
          </FormControl>
          <FormControl>
            <FormControl.Label>Respuesta correcta (opcional)</FormControl.Label>
            <Textarea block value={respuestaCorrecta} onChange={(e) => setRespuestaCorrecta(e.target.value)} maxLength={4000} rows={2} />
          </FormControl>
          <FormControl>
            <FormControl.Label>Cifra correcta (opcional)</FormControl.Label>
            <TextInput value={cifraCorrecta} onChange={(e) => setCifraCorrecta(e.target.value)} maxLength={100} placeholder="p. ej. 1.234,56 €" />
          </FormControl>
          {caseId ? (
            <FormControl disabled={consentimiento === null}>
              <Checkbox checked={guardar} onChange={(e) => setGuardar(e.target.checked)} />
              <FormControl.Label className="font-normal!">
                {consentimiento?.texto ?? "Cargando las condiciones para guardar la conversación…"}
              </FormControl.Label>
            </FormControl>
          ) : (
            <p className="m-0 text-xs text-fg-muted">Sin expediente solo se registra tu valoración, no la conversación.</p>
          )}
          {error && <Flash variant="danger" role="alert">{error}</Flash>}
          <div className="flex gap-2">
            <Button type="submit" variant="primary" disabled={ocupado} loading={ocupado}>
              Enviar
            </Button>
            <Button onClick={() => setEstado("inicio")}>Cancelar</Button>
          </div>
        </form>
      )}
      {estado === "inicio" && error && (
        <Flash variant="danger" role="alert" className="mt-2">
          {error}
        </Flash>
      )}
    </div>
  );
}
