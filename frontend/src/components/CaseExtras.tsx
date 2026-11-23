// Two blocks of «Tu expediente» that talk to the server on their own:
// upcoming deadlines, and "read my document" with confirm-before-save.

import { useEffect, useRef, useState } from "react";

import type { Deadline, DocumentAnalysis } from "../api";
import { ApiError, analyzeDocument, getCalendar } from "../api";
import { factLabel, factValue, fileToBase64, formatDate } from "../lib/presentation";
import { Button, Caja, CargandoLista, Checkbox, CounterLabel, Details, Flash, FormControl, Label } from "../ui";
import { CalendarIcon, UploadIcon } from "../ui/iconos";

function message(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function cuando(dias: number): string {
  return dias === 0 ? "hoy" : dias === 1 ? "mañana" : `en ${dias} días`;
}

export function DeadlinesSection({ caseId, refreshKey }: { caseId: string | null; refreshKey: string }) {
  const [state, setState] = useState<{ key: string; items: Deadline[]; aviso: string; error: string | null } | null>(null);

  useEffect(() => {
    if (!caseId) return;
    const controller = new AbortController();
    const key = `${caseId}|${refreshKey}`;
    getCalendar({ caseId, dias: 270, signal: controller.signal }).then(
      (calendar) => setState({ key, items: calendar.vencimientos, aviso: calendar.aviso, error: null }),
      (err: unknown) => {
        if (!controller.signal.aborted) setState({ key, items: [], aviso: "", error: message(err, "No se pudieron cargar los plazos.") });
      },
    );
    return () => controller.abort();
  }, [caseId, refreshKey]);

  if (!caseId) return null;
  return (
    <Caja as="section" aria-label="Próximos plazos">
      <Caja.Cabecera
        titulo={
          <span className="inline-flex items-center gap-2">
            <CalendarIcon />
            Próximos plazos
          </span>
        }
        acciones={state && !state.error ? <CounterLabel>{state.items.length}</CounterLabel> : undefined}
      />
      {!state && (
        <Caja.Cuerpo>
          <CargandoLista filas={2} etiqueta="Cargando los plazos" />
        </Caja.Cuerpo>
      )}
      {state?.error && (
        <Caja.Cuerpo>
          <Flash variant="danger">{state.error}</Flash>
        </Caja.Cuerpo>
      )}
      {state && !state.error && state.items.length === 0 && <Caja.Cuerpo className="text-fg-muted">No hay plazos en los próximos nueve meses.</Caja.Cuerpo>}
      {state && !state.error && state.items.length > 0 && (
        <Caja.Lista>
          {state.items.map((d) => {
            const urgent = d.dias_restantes <= 30;
            return (
              <Caja.Fila key={d.id} className="flex flex-col gap-1">
                <p className="m-0 flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{formatDate(d.fecha)}</span>
                  <Label variant={urgent ? "danger" : "secondary"}>{cuando(d.dias_restantes)}</Label>
                </p>
                <p className="m-0">
                  {d.titulo} · {d.periodo}
                  {d.modelo ? ` · modelo ${d.modelo}` : ""}
                </p>
                <Details>
                  <summary className="cursor-pointer text-xs text-fg-muted">Detalle</summary>
                  <p className="m-0 mt-1 text-xs">{d.detalle}</p>
                  <p className="m-0 mt-1 text-xs text-fg-muted">
                    {d.referencia}
                    {!d.verificado && " · fecha habitual, pendiente de confirmar con la orden del año"}
                  </p>
                  {d.nota && <p className="m-0 mt-1 text-xs text-fg-muted">{d.nota}</p>}
                </Details>
              </Caja.Fila>
            );
          })}
        </Caja.Lista>
      )}
      {state && !state.error && (
        <Caja.Pie className="text-xs text-fg-muted">Solo aparecen las obligaciones que constan en tus datos. No se tienen en cuenta los festivos.</Caja.Pie>
      )}
    </Caja>
  );
}

interface DocumentSectionProps {
  caseId: string | null;
  disabled: boolean;
  // Persist one accepted fact. Resolves true when saved.
  onApply: (factName: string, value: string | number | boolean) => Promise<boolean>;
}

export function DocumentSection({ caseId, disabled, onApply }: DocumentSectionProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [status, setStatus] = useState<"idle" | "reading" | "saving">("idle");
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<DocumentAnalysis | null>(null);
  const [accepted, setAccepted] = useState<Set<string>>(new Set());
  const [savedCount, setSavedCount] = useState<number | null>(null);

  async function handleFile(file: File) {
    if (!caseId) return;
    setStatus("reading");
    setError(null);
    setAnalysis(null);
    setSavedCount(null);
    try {
      if (file.size > 10 * 1024 * 1024) throw new ApiError("El documento supera el tamaño máximo de 10 MB.");
      const contentBase64 = await fileToBase64(file);
      const result = await analyzeDocument({ caseId, filename: file.name, contentBase64 });
      setAnalysis(result);
      setAccepted(new Set(result.hechos_propuestos.map((f) => f.name)));
    } catch (err) {
      setError(message(err, "No se pudo leer el documento."));
    } finally {
      setStatus("idle");
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function saveAccepted() {
    if (!analysis) return;
    setStatus("saving");
    let saved = 0;
    try {
      for (const fact of analysis.hechos_propuestos) {
        if (!accepted.has(fact.name)) continue;
        if (!(await onApply(fact.name, fact.value))) return;
        saved += 1;
      }
      setSavedCount(saved);
      setAnalysis(null);
    } finally {
      setStatus("idle");
    }
  }

  return (
    <Caja as="section" aria-label="Tus documentos">
      <Caja.Cabecera titulo="Tus documentos" />
      <Caja.Cuerpo className="flex flex-col gap-3">
        <p className="m-0 text-fg-muted">
          {caseId
            ? "Extraigo las cifras y te las enseño para que las confirmes antes de guardarlas. El documento no se almacena; su contenido se envía al modelo de lenguaje para leerlo. También leo escaneados y fotos."
            : "Haz primero una consulta para abrir tu expediente."}
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.txt,.csv,.png,.jpg,.jpeg,.webp,application/pdf,text/plain,image/png,image/jpeg,image/webp"
          className="sr-only"
          tabIndex={-1}
          aria-label="Documento a leer"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
          }}
        />
        <div>
          <Button leadingVisual={UploadIcon} onClick={() => inputRef.current?.click()} disabled={!caseId || disabled || status !== "idle"} loading={status === "reading"}>
            {status === "reading" ? "Leyendo el documento…" : "Leer un certificado o los datos fiscales (PDF o foto)"}
          </Button>
        </div>
        {error && <Flash variant="danger">{error}</Flash>}
        {savedCount !== null && (
          <Flash variant="success" role="status">
            {savedCount === 1 ? "1 dato guardado en tu expediente." : `${savedCount} datos guardados en tu expediente.`}
          </Flash>
        )}
      </Caja.Cuerpo>

      {analysis && (
        <>
          <Caja.Cuerpo className="flex flex-wrap items-center gap-2 border-t border-border-default bg-canvas-muted">
            <strong>{analysis.tipo_documento}</strong>
            <span className="text-fg-muted">· {analysis.filename}</span>
            {analysis.lectura === "imagen" && <Label variant="attention">Leído de la imagen: revisa las cifras</Label>}
          </Caja.Cuerpo>
          {analysis.hechos_propuestos.length > 0 && (
            <Caja.Lista aria-label="Datos encontrados en el documento">
              {analysis.hechos_propuestos.map((fact) => (
                <Caja.Fila key={fact.name}>
                  <FormControl>
                    <Checkbox
                      checked={accepted.has(fact.name)}
                      onChange={(e) =>
                        setAccepted((prev) => {
                          const next = new Set(prev);
                          if (e.target.checked) next.add(fact.name);
                          else next.delete(fact.name);
                          return next;
                        })
                      }
                    />
                    <FormControl.Label>
                      {factLabel(fact.name)}: {factValue(fact)}
                    </FormControl.Label>
                    <FormControl.Caption>«{fact.evidencia}»</FormControl.Caption>
                  </FormControl>
                </Caja.Fila>
              ))}
            </Caja.Lista>
          )}
          {analysis.avisos.map((aviso, i) => (
            <Caja.Cuerpo key={i} className="border-t border-border-muted text-xs">
              {aviso}
            </Caja.Cuerpo>
          ))}
          <Caja.Pie className="flex flex-wrap gap-2">
            {analysis.hechos_propuestos.length > 0 && (
              <Button variant="primary" onClick={() => void saveAccepted()} disabled={accepted.size === 0 || status !== "idle"} loading={status === "saving"}>
                {`Guardar ${accepted.size} ${accepted.size === 1 ? "dato" : "datos"}`}
              </Button>
            )}
            <Button onClick={() => setAnalysis(null)}>Descartar</Button>
          </Caja.Pie>
        </>
      )}
    </Caja>
  );
}
