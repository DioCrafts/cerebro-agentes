/* ui-legado: pendiente de migrar a src/ui (docs/ui/sistema-de-diseno.md) */
// «Revisión de casos»: what the assistant got wrong, as masked candidate cases, so it does not happen again.
// The owner of the case (or the operator) reads the masked conversation, the brain's plan and what went wrong,
// writes the expected behaviour and accepts it (it becomes a PRIVATE regression case, never part of the code)
// or rejects it with a reason. The per-case «aprender de los fallos» switch lives here too.

import { ClipboardCheck, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../api";
import {
  aceptarCandidato,
  borrarAceptado,
  cambiarAprendizaje,
  getAceptados,
  getAprendizaje,
  getCandidato,
  getColaRevision,
  rechazarCandidato,
  type Aceptado,
  type ColaRevision,
  type DetalleCandidato,
  type EdicionCaso,
  type EstadoAprendizaje,
  type EstadoCandidato,
} from "../apiEvaluacion";

interface Props {
  open: boolean;
  onClose: () => void;
  caseId: string | null;
}

const ESPECIALISTAS = ["fiscal", "hipoteca", "luz", "caducidades", "ayudas", "suscripciones", "correo"];
const ORIGEN: Record<string, string> = {
  valoracion: "Lo corregiste",
  voto: "Lo marcaste como incorrecto",
  senal: "Detectado automáticamente",
};
const PESTANAS: Array<{ id: EstadoCandidato | "aceptados"; etiqueta: string }> = [
  { id: "pendiente", etiqueta: "Pendientes" },
  { id: "aceptados", etiqueta: "Aceptados" },
  { id: "rechazado", etiqueta: "Rechazados" },
];

function mensaje(err: unknown): string {
  return err instanceof ApiError ? err.message : "No se ha podido completar la operación.";
}

function lineas(texto: string): string[] {
  return texto
    .split(/\n|;/)
    .map((t) => t.trim())
    .filter(Boolean);
}

function fecha(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric" });
}

const campo = "mt-1 block w-full rounded-sm border border-line bg-paper px-2 py-1 text-[13px] text-ink";
const etiqueta = "block text-[12.5px] text-ink-soft";
const boton = "rounded-lg px-3 py-1.5 text-[12.5px] font-semibold disabled:opacity-50";

function Formulario({
  detalle,
  onHecho,
}: {
  detalle: DetalleCandidato;
  onHecho: (texto: string) => void;
}) {
  const c = detalle.candidato;
  const contenido = c.contenido;
  const informes = contenido.cerebro?.informes ?? [];
  const [categoria, setCategoria] = useState(c.categoria_sugerida);
  const [descripcion, setDescripcion] = useState("");
  const [pregunta, setPregunta] = useState(contenido.pregunta);
  const [obligatorios, setObligatorios] = useState<string[]>([]);
  const [prohibidos, setProhibidos] = useState<string[]>([]);
  const [citas, setCitas] = useState(0);
  const [cifrasProhibidas, setCifrasProhibidas] = useState("");
  const [herramienta, setHerramienta] = useState("");
  const [argumentos, setArgumentos] = useState("{}");
  const [referencia, setReferencia] = useState(contenido.correccion.cifra_correcta ?? "");
  const [especialistaCifra, setEspecialistaCifra] = useState("");
  const [abstenerse, setAbstenerse] = useState(false);
  const [aclarar, setAclarar] = useState(false);
  const [puerta, setPuerta] = useState(false);
  const [presentes, setPresentes] = useState("");
  const [ausentes, setAusentes] = useState("");
  const [aunque, setAunque] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const alternar = (lista: string[], valor: string, set: (v: string[]) => void) =>
    set(lista.includes(valor) ? lista.filter((x) => x !== valor) : [...lista, valor]);

  const aceptar = () => {
    let args: Record<string, unknown> = {};
    if (herramienta.trim()) {
      try {
        const leido: unknown = JSON.parse(argumentos || "{}");
        if (typeof leido !== "object" || leido === null || Array.isArray(leido)) throw new Error("no es un objeto");
        args = leido as Record<string, unknown>;
      } catch {
        setError("Los argumentos de la calculadora deben ser un objeto JSON, p. ej. {\"fecha\": \"2026-03-20\"}.");
        return;
      }
    }
    const edicion: EdicionCaso = {
      categoria,
      descripcion: descripcion.trim(),
      pregunta: pregunta.trim() && pregunta.trim() !== contenido.pregunta ? pregunta.trim() : null,
      esperado: {
        especialistas_obligatorios: obligatorios,
        especialistas_prohibidos: prohibidos,
        citas_minimas: citas,
        cifras_prohibidas: lineas(cifrasProhibidas),
        cifra_esperada: herramienta.trim()
          ? { herramienta: herramienta.trim(), argumentos: args, referencia: referencia.trim() || null, especialista: especialistaCifra || null }
          : null,
        debe_abstenerse: abstenerse,
        debe_aclarar: aclarar,
        puerta_sin_violaciones: puerta,
        textos_obligatorios: lineas(presentes),
        textos_prohibidos: lineas(ausentes),
      },
      aunque_no_reproduzca: aunque,
    };
    setEnviando(true);
    setError(null);
    aceptarCandidato(c.id, edicion)
      .then((a) => onHecho(`Aceptado como ${a.id}: ${a.expectativas.length} comprobaciones.${a.avisos.length ? " " + a.avisos.join(" ") : ""}`))
      .catch((err: unknown) => setError(mensaje(err)))
      .finally(() => setEnviando(false));
  };

  const rechazar = () => {
    if (motivo.trim().length < 3) {
      setError("Indica por qué se rechaza.");
      return;
    }
    setEnviando(true);
    setError(null);
    rechazarCandidato(c.id, motivo.trim())
      .then((r) => onHecho(`Rechazado (${r.rechazados} ${r.rechazados === 1 ? "caso" : "casos"}).`))
      .catch((err: unknown) => setError(mensaje(err)))
      .finally(() => setEnviando(false));
  };

  const especialistas = Array.from(new Set([...informes.map((i) => i.especialista), ...ESPECIALISTAS]));

  return (
    <div className="space-y-4">
      <section aria-label="Conversación" className="space-y-1.5 rounded-xl border border-line bg-surface px-4 py-3 text-[13px]">
        <p className="text-[11.5px] font-semibold uppercase tracking-[0.08em] text-muted">Conversación (datos personales ocultos)</p>
        <p className="text-ink">
          <span className="font-semibold">Pregunta: </span>
          {contenido.pregunta}
        </p>
        {contenido.respuesta && (
          <p className="whitespace-pre-line text-ink-soft">
            <span className="font-semibold text-ink">Respuesta{contenido.respuesta_parcial ? " (reconstruida de sus afirmaciones)" : ""}: </span>
            {contenido.respuesta}
          </p>
        )}
        {contenido.siguiente_pregunta && (
          <p className="text-ink-soft">
            <span className="font-semibold text-ink">Luego preguntó: </span>
            {contenido.siguiente_pregunta}
          </p>
        )}
      </section>

      <section aria-label="Qué falló" className="space-y-1 text-[13px]">
        <h4 className="font-semibold text-ink">Qué falló: {detalle.etiqueta_fallo}</h4>
        <p className="text-[12px] text-muted">
          {ORIGEN[c.origen]} · {fecha(c.creado_el)}
          {detalle.frecuencia > 1 ? ` · ha pasado ${detalle.frecuencia} veces` : ""}
          {c.importe_en_juego ? ` · ${c.importe_en_juego} € en juego` : ""}
        </p>
        {contenido.correccion.comentario && <p className="text-ink-soft">«{contenido.correccion.comentario}»</p>}
        {contenido.correccion.cifra_correcta && <p className="text-ink-soft">Cifra correcta según el usuario: {contenido.correccion.cifra_correcta}</p>}
        {contenido.senales.length > 0 && (
          <ul className="list-disc pl-5 text-[12.5px] text-ink-soft">
            {contenido.senales.map((s, i) => (
              <li key={i}>
                {s.tipo}
                {s.detalle ? `: ${s.detalle}` : ""}
              </li>
            ))}
          </ul>
        )}
        {(contenido.cerebro?.puerta?.violaciones ?? []).map((v, i) => (
          <p key={i} className="text-[12.5px] text-risk-high-fg">
            Puerta de cifras: {v.tipo} («{v.literal}») — {v.detalle}
          </p>
        ))}
      </section>

      {contenido.cerebro && (
        <section aria-label="Plan del cerebro" className="space-y-1 text-[13px]">
          <h4 className="font-semibold text-ink">Cómo lo preparó</h4>
          <p className="text-ink-soft">
            {contenido.cerebro.intencion.resumen || contenido.cerebro.intencion.tipo} · ruta {contenido.cerebro.ruta} · confianza{" "}
            {Math.round(contenido.cerebro.intencion.confianza * 100)} %
          </p>
          <ul className="space-y-1 text-[12.5px]">
            {contenido.cerebro.intencion.encargos.map((e) => {
              const informe = informes.find((i) => i.encargo_id === e.id);
              return (
                <li key={e.id} className="rounded-lg border border-line px-2 py-1">
                  <span className="font-semibold">{e.especialista}</span>
                  {e.depende_de.length ? ` (espera a ${e.depende_de.join(", ")})` : ""}: {e.pregunta}
                  {informe && (
                    <span className="block text-muted">
                      {informe.estado}
                      {informe.modo ? ` · ${informe.modo}` : ""}
                      {informe.cifras.length ? ` · ${informe.cifras.map((x) => `${x.concepto}: ${x.literal || x.valor}`).join("; ")}` : ""}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
          {contenido.cerebro.peticiones.length > 0 && (
            <p className="text-[12.5px] text-ink-soft">
              Peticiones: {contenido.cerebro.peticiones.map((p) => `${p.solicitante} → ${p.destinatario}: ${p.dato}`).join("; ")}
            </p>
          )}
        </section>
      )}

      {!contenido.cerebro && contenido.cerebro_resumen && (
        <p className="text-[12.5px] text-ink-soft">
          Cómo lo preparó: {contenido.cerebro_resumen.tipo ?? "—"} · ruta {contenido.cerebro_resumen.ruta ?? "—"} · consultó a{" "}
          {(contenido.cerebro_resumen.especialistas ?? []).join(", ") || "nadie"}
          {contenido.cerebro_resumen.violaciones_cifras ? ` · ${contenido.cerebro_resumen.violaciones_cifras} problemas en la puerta de cifras` : ""}
        </p>
      )}

      {contenido.enmascarado.length > 0 && (
        <details className="text-[12.5px] text-ink-soft">
          <summary className="cursor-pointer font-semibold text-ink">Qué se ha ocultado ({contenido.enmascarado.length})</summary>
          <ul className="mt-1 space-y-0.5">
            {contenido.enmascarado.map((m, i) => (
              <li key={i}>
                {m.campo}: {m.tipo} {m.vista} → {m.sustituto}
              </li>
            ))}
          </ul>
        </details>
      )}

      {c.estado === "pendiente" && (
        <form
          className="space-y-3 rounded-xl border border-primary/40 bg-surface px-4 py-3"
          onSubmit={(e) => {
            e.preventDefault();
            aceptar();
          }}
        >
          <h4 className="font-semibold text-ink">Cómo debería haber respondido</h4>
          <label className={etiqueta}>
            Categoría
            <select value={categoria} onChange={(e) => setCategoria(e.target.value)} className={campo}>
              {detalle.categorias.map((cat) => (
                <option key={cat.clave} value={cat.clave}>
                  {cat.clave}
                  {cat.critica ? " (crítica)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className={etiqueta}>
            Pregunta para la prueba (puedes quitar lo que aún sea personal)
            <textarea value={pregunta} onChange={(e) => setPregunta(e.target.value)} rows={2} maxLength={4000} className={campo} />
          </label>
          <fieldset className="text-[12.5px] text-ink-soft">
            <legend className="mb-1">Especialistas: debe consultar / no debe consultar</legend>
            <div className="grid grid-cols-2 gap-x-3 gap-y-1">
              {especialistas.map((e) => (
                <span key={e} className="flex items-center gap-2">
                  <input type="checkbox" aria-label={`Debe consultar ${e}`} checked={obligatorios.includes(e)} onChange={() => alternar(obligatorios, e, setObligatorios)} />
                  <input type="checkbox" aria-label={`No debe consultar ${e}`} checked={prohibidos.includes(e)} onChange={() => alternar(prohibidos, e, setProhibidos)} />
                  {e}
                </span>
              ))}
            </div>
          </fieldset>
          <div className="grid grid-cols-2 gap-3">
            <label className={etiqueta}>
              Citas mínimas
              <input type="number" min={0} max={20} value={citas} onChange={(e) => setCitas(Number(e.target.value) || 0)} className={campo} />
            </label>
            <label className={etiqueta}>
              Cifras que no debe dar
              <input value={cifrasProhibidas} onChange={(e) => setCifrasProhibidas(e.target.value)} placeholder="p. ej. 532,00 €" className={campo} />
            </label>
          </div>
          <fieldset className="space-y-2 rounded-lg bg-paper px-3 py-2">
            <legend className="text-[12.5px] font-semibold text-ink">Cifra esperada (siempre de una calculadora)</legend>
            <div className="grid grid-cols-2 gap-3">
              <label className={etiqueta}>
                Calculadora
                <input value={herramienta} onChange={(e) => setHerramienta(e.target.value)} placeholder="hipoteca_cuadro" className={campo} />
              </label>
              <label className={etiqueta}>
                Cifra que localiza la línea
                <input value={referencia} onChange={(e) => setReferencia(e.target.value)} placeholder="474,21 €" className={campo} />
              </label>
            </div>
            <label className={etiqueta}>
              Argumentos (JSON)
              <input value={argumentos} onChange={(e) => setArgumentos(e.target.value)} className={campo} />
            </label>
            <label className={etiqueta}>
              Debe atribuirse a
              <select value={especialistaCifra} onChange={(e) => setEspecialistaCifra(e.target.value)} className={campo}>
                <option value="">—</option>
                {especialistas.map((e) => (
                  <option key={e} value={e}>
                    {e}
                  </option>
                ))}
              </select>
            </label>
          </fieldset>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-ink-soft">
            <label className="inline-flex items-center gap-1.5">
              <input type="checkbox" checked={abstenerse} onChange={(e) => setAbstenerse(e.target.checked)} />
              Debe abstenerse
            </label>
            <label className="inline-flex items-center gap-1.5">
              <input type="checkbox" checked={aclarar} onChange={(e) => setAclarar(e.target.checked)} />
              Debe preguntar antes
            </label>
            <label className="inline-flex items-center gap-1.5">
              <input type="checkbox" checked={puerta} onChange={(e) => setPuerta(e.target.checked)} />
              Cifras sin problemas en la puerta
            </label>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className={etiqueta}>
              Debe decir (uno por línea)
              <textarea value={presentes} onChange={(e) => setPresentes(e.target.value)} rows={2} className={campo} />
            </label>
            <label className={etiqueta}>
              No debe decir (uno por línea)
              <textarea value={ausentes} onChange={(e) => setAusentes(e.target.value)} rows={2} className={campo} />
            </label>
          </div>
          <label className={etiqueta}>
            Descripción (opcional)
            <input value={descripcion} onChange={(e) => setDescripcion(e.target.value)} maxLength={500} className={campo} />
          </label>
          <label className="inline-flex items-center gap-1.5 text-[12px] text-muted">
            <input type="checkbox" checked={aunque} onChange={(e) => setAunque(e.target.checked)} />
            Aceptar aunque la respuesta original pasara estas comprobaciones
          </label>
          {error && (
            <p role="alert" className="rounded-lg border border-risk-high-fg/30 bg-risk-high-bg px-3 py-2 text-[12.5px] text-ink-soft">
              {error}
            </p>
          )}
          <div className="flex flex-wrap items-end gap-2">
            <button type="submit" disabled={enviando} className={`${boton} bg-primary text-white`}>
              {enviando ? "Guardando…" : "Aceptar como prueba"}
            </button>
            <input value={motivo} onChange={(e) => setMotivo(e.target.value)} maxLength={500} placeholder="Motivo para rechazar" aria-label="Motivo para rechazar" className="min-w-0 flex-1 rounded-sm border border-line bg-paper px-2 py-1 text-[12.5px]" />
            <button type="button" disabled={enviando} onClick={rechazar} className={`${boton} border border-line text-ink-soft hover:border-risk-high-fg hover:text-risk-high-fg`}>
              Rechazar
            </button>
          </div>
        </form>
      )}
      {c.estado === "rechazado" && c.motivo_rechazo && <p className="text-[12.5px] text-muted">Rechazado: {c.motivo_rechazo}</p>}
    </div>
  );
}

export function RevisionCasosPanel({ open, onClose, caseId }: Props) {
  const [pestana, setPestana] = useState<EstadoCandidato | "aceptados">("pendiente");
  const [cola, setCola] = useState<ColaRevision | null>(null);
  const [aceptados, setAceptados] = useState<Aceptado[]>([]);
  const [detalle, setDetalle] = useState<DetalleCandidato | null>(null);
  const [aprendizaje, setAprendizaje] = useState<EstadoAprendizaje | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const recargar = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const fallo = (err: unknown) => {
      if (!controller.signal.aborted) setError(`No se ha podido cargar la revisión. ${mensaje(err)}`);
    };
    if (pestana === "aceptados") {
      getAceptados(controller.signal).then((r) => setAceptados(r.casos), fallo);
    } else {
      getColaRevision(pestana, controller.signal).then(setCola, fallo);
    }
    if (caseId) getAprendizaje(caseId, controller.signal).then(setAprendizaje, () => setAprendizaje(null));
    return () => controller.abort();
  }, [open, pestana, caseId, tick]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const abrir = (id: string) => {
    setError(null);
    getCandidato(id).then(setDetalle, (err: unknown) => setError(mensaje(err)));
  };

  const alternarAprendizaje = (activo: boolean) => {
    if (!caseId || !aprendizaje) return;
    cambiarAprendizaje(caseId, activo, aprendizaje.version).then(
      (r) => {
        setAprendizaje(r);
        recargar();
      },
      (err: unknown) => setError(mensaje(err)),
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button type="button" aria-label="Cerrar la revisión de casos" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-label="Revisión de casos" className="relative flex h-full w-full max-w-[560px] flex-col border-l border-line bg-paper shadow-pop">
        <header className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <h2 className="flex items-center gap-2 font-serif text-[19px] font-bold text-ink">
              <ClipboardCheck size={18} aria-hidden className="text-primary-dark" />
              Revisión de casos
            </h2>
            <p className="text-[12.5px] text-muted">Lo que el asistente hizo mal, para que no vuelva a pasar</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Cerrar" className="rounded-lg p-2 text-muted hover:bg-line/60 hover:text-ink">
            <X size={18} />
          </button>
        </header>
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {caseId && aprendizaje && (
            <label className="flex items-start gap-2 rounded-lg bg-surface px-3 py-2 text-[12.5px] leading-snug text-ink-soft">
              <input type="checkbox" className="mt-0.5" checked={aprendizaje.activo} onChange={(e) => alternarAprendizaje(e.target.checked)} />
              <span>{aprendizaje.texto}</span>
            </label>
          )}
          {aviso && (
            <p role="status" className="rounded-lg bg-surface px-3 py-2 text-[12.5px] text-ink-soft">
              {aviso}
            </p>
          )}
          {error && (
            <p role="alert" className="rounded-lg border border-risk-high-fg/30 bg-risk-high-bg px-3 py-2 text-[13px] text-ink-soft">
              {error}
            </p>
          )}
          {detalle ? (
            <div className="space-y-3">
              <button type="button" onClick={() => setDetalle(null)} className="text-[12.5px] font-semibold text-primary-dark underline">
                ← Volver a la lista
              </button>
              <Formulario
                key={detalle.candidato.id}
                detalle={detalle}
                onHecho={(texto) => {
                  setAviso(texto);
                  setDetalle(null);
                  recargar();
                }}
              />
            </div>
          ) : (
            <>
              <nav aria-label="Estado" className="flex gap-1">
                {PESTANAS.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    aria-pressed={pestana === p.id}
                    onClick={() => setPestana(p.id)}
                    className={`rounded-pill px-3 py-1 text-[12.5px] font-semibold ${pestana === p.id ? "bg-primary text-white" : "text-ink-soft hover:bg-line/60"}`}
                  >
                    {p.etiqueta}
                  </button>
                ))}
              </nav>
              {pestana === "aceptados" ? (
                aceptados.length === 0 ? (
                  <p className="text-[13.5px] text-ink-soft">Aún no hay casos aceptados.</p>
                ) : (
                  <ul className="space-y-2">
                    {aceptados.map((a) => (
                      <li key={a.id} className="rounded-xl border border-line bg-surface px-4 py-3 text-[13px] shadow-card">
                        <p className="font-semibold text-ink">{a.descripcion || a.id}</p>
                        <p className="text-[12px] text-muted">
                          {a.categoria} · {a.expectativas.length} comprobaciones · {fecha(a.aceptado_el)}
                          {a.vigente ? "" : ` · retirado: ${a.motivo_baja ?? ""}`}
                        </p>
                        {a.avisos.map((t, i) => (
                          <p key={i} className="text-[12px] text-ink-soft">
                            {t}
                          </p>
                        ))}
                        <button
                          type="button"
                          onClick={() => {
                            if (window.confirm("¿Borrar este caso? Dejará de comprobarse en las evaluaciones.")) {
                              borrarAceptado(a.id).then(recargar, (err: unknown) => setError(mensaje(err)));
                            }
                          }}
                          className="mt-2 inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-[12px] font-semibold text-ink-soft hover:border-risk-high-fg hover:text-risk-high-fg"
                        >
                          <Trash2 size={12} aria-hidden />
                          Borrar
                        </button>
                      </li>
                    ))}
                  </ul>
                )
              ) : cola && cola.grupos.length === 0 ? (
                <p className="text-[13.5px] text-ink-soft">
                  {pestana === "pendiente"
                    ? "No hay nada pendiente. Cuando marques una respuesta como incorrecta (o, si lo activas, cuando el asistente detecte un fallo), aparecerá aquí."
                    : "Nada por aquí."}
                </p>
              ) : (
                <ul className="space-y-2">
                  {(cola?.grupos ?? []).map((g) => (
                    <li key={g.id}>
                      <button type="button" onClick={() => abrir(g.id)} className="w-full rounded-xl border border-line bg-surface px-4 py-3 text-left shadow-card hover:border-primary">
                        <p className="flex flex-wrap items-center gap-2 text-[11.5px] font-semibold uppercase tracking-[0.08em] text-muted">
                          {g.etiqueta_fallo}
                          <span className="rounded-pill bg-line/70 px-2 py-px normal-case tracking-normal text-ink-soft">
                            {g.categoria_sugerida}
                            {g.critica ? " · crítica" : ""}
                          </span>
                          {g.frecuencia > 1 && <span className="normal-case tracking-normal">×{g.frecuencia}</span>}
                        </p>
                        <p className="mt-1 text-[13.5px] leading-snug text-ink">{g.pregunta}</p>
                        <p className="mt-1 text-[12px] text-muted">
                          {ORIGEN[g.origen]} · {fecha(g.creado_el)} · prioridad {g.prioridad.toFixed(0)}
                          {g.importe_en_juego ? ` · ${g.importe_en_juego} € en juego` : ""}
                        </p>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
        <footer className="border-t border-line px-5 py-3 text-[11.5px] leading-relaxed text-muted">
          Lo que aceptes se guarda enmascarado y en privado (nunca en el código del programa) para comprobar que el error no vuelve; se exporta y se borra con tu cuenta.
        </footer>
      </aside>
    </div>
  );
}
