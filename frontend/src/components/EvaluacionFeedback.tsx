// Feedback under each answer: 👍 / 👎 and «Corregir» (what was wrong, and optionally the right answer or
// figure). Storing the conversation for evaluation is a separate, unticked checkbox with its consent text,
// for THIS answer only; without it only the vote is recorded (unless the user switched on «aprender de los
// fallos» for the case, in «Revisión de casos»). A stored 👎 becomes a masked candidate case the user can
// review there; a stored conversation can be withdrawn here.

import { PencilLine, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api";
import type { ConsentimientoEvaluacion, FeedbackRespuesta, TipoCorreccion } from "../apiEvaluacion";
import { enviarFeedback, getConsentimientoEvaluacion, retirarFeedback } from "../apiEvaluacion";

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
      <div role="status" className="mt-2 text-[12.5px] text-muted">
        {retirado ? "Has retirado la conversación: ya no se guarda para evaluación ni como caso a revisar." : resultado.mensaje}
        {resultado.guardado_para_evaluacion && !retirado && (
          <button type="button" onClick={retirar} className="ml-2 font-semibold text-ink-soft underline hover:text-ink">
            Retirar
          </button>
        )}
        {error && <span className="ml-2 text-risk-high-fg">{error}</span>}
      </div>
    );
  }

  const botonBase =
    "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12.5px] font-medium text-muted transition hover:bg-line/60 hover:text-ink-soft disabled:cursor-not-allowed disabled:opacity-50";
  const tipos = consentimiento?.tipos ?? TIPOS_POR_DEFECTO;

  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-1" aria-label="Valora esta respuesta">
        <span className="mr-1 text-[12.5px] text-muted">¿Te ha sido útil?</span>
        <button type="button" className={botonBase} disabled={estado === "enviando"} onClick={() => enviar("positiva")} aria-label="Sí, útil">
          <ThumbsUp size={14} aria-hidden />
        </button>
        <button type="button" className={botonBase} disabled={estado === "enviando"} onClick={abrirFormulario} aria-label="No, no es correcta">
          <ThumbsDown size={14} aria-hidden />
        </button>
        <button type="button" className={botonBase} disabled={estado === "enviando"} onClick={abrirFormulario}>
          <PencilLine size={14} aria-hidden />
          Corregir
        </button>
      </div>

      {(estado === "formulario" || estado === "enviando") && (
        <form
          className="mt-2 flex flex-col gap-2.5 rounded-xl border border-line bg-surface px-4 py-3 text-[13px] text-ink shadow-card"
          onSubmit={(event) => {
            event.preventDefault();
            enviar("negativa");
          }}
        >
          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 font-semibold">¿Qué estaba mal?</legend>
            {tipos.map((t) => (
              <label key={t.clave} className="flex items-center gap-2">
                <input type="radio" name="tipo-correccion" checked={tipo === t.clave} onChange={() => setTipo(t.clave)} />
                {t.etiqueta}
              </label>
            ))}
          </fieldset>
          <label className="flex flex-col gap-1">
            <span className="font-semibold">Explícalo (opcional)</span>
            <textarea
              value={comentario}
              onChange={(e) => setComentario(e.target.value)}
              maxLength={2000}
              rows={2}
              className="rounded-lg border border-line bg-paper px-2 py-1.5"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-semibold">Respuesta correcta (opcional)</span>
            <textarea
              value={respuestaCorrecta}
              onChange={(e) => setRespuestaCorrecta(e.target.value)}
              maxLength={4000}
              rows={2}
              className="rounded-lg border border-line bg-paper px-2 py-1.5"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-semibold">Cifra correcta (opcional)</span>
            <input
              value={cifraCorrecta}
              onChange={(e) => setCifraCorrecta(e.target.value)}
              maxLength={100}
              placeholder="p. ej. 1.234,56 €"
              className="rounded-lg border border-line bg-paper px-2 py-1.5"
            />
          </label>
          {caseId ? (
            <label className="flex items-start gap-2 rounded-lg bg-paper px-3 py-2">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={guardar}
                disabled={consentimiento === null}
                onChange={(e) => setGuardar(e.target.checked)}
              />
              <span className="text-[12.5px] leading-snug text-ink-soft">
                {consentimiento?.texto ?? "Cargando las condiciones para guardar la conversación…"}
              </span>
            </label>
          ) : (
            <p className="text-[12.5px] text-muted">Sin expediente solo se registra tu valoración, no la conversación.</p>
          )}
          {error && (
            <p role="alert" className="text-[12.5px] text-risk-high-fg">
              {error}
            </p>
          )}
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={estado === "enviando"}
              className="rounded-lg bg-navy px-3 py-1.5 text-[13px] font-semibold text-white disabled:opacity-50"
            >
              {estado === "enviando" ? "Enviando…" : "Enviar"}
            </button>
            <button
              type="button"
              onClick={() => setEstado("inicio")}
              className="rounded-lg px-3 py-1.5 text-[13px] font-semibold text-ink-soft hover:bg-line/60"
            >
              Cancelar
            </button>
          </div>
        </form>
      )}
      {estado === "inicio" && error && (
        <p role="alert" className="mt-1 text-[12.5px] text-risk-high-fg">
          {error}
        </p>
      )}
    </div>
  );
}
