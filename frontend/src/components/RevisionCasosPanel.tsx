// «Revisión de casos»: what the assistant got wrong, as masked candidate cases, so it does not happen again.
// The owner of the case (or the operator) reads the masked conversation, the brain's plan and what went wrong,
// writes the expected behaviour and accepts it (it becomes a PRIVATE regression case, never part of the code)
// or rejects it with a reason. The per-case «aprender de los fallos» switch lives here too.
//
// Migrated to the design system (docs/ui/sistema-de-diseno.md): a filterable list like GitHub's issues
// (SegmentedControl for pending / accepted / rejected), a setting row with a ToggleSwitch, and a form of
// FormControls. The shell shows it as a page (registro.ts: presentacion "pagina").

import { useCallback, useEffect, useId, useState } from "react";

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
import { AvisoError, Blankslate, Button, Caja, CargandoLista, Checkbox, CounterLabel, Flash, FormControl, Heading, Label, Link, SegmentedControl, Select, Textarea, TextInput, ToggleSwitch, useConfirm } from "../ui";
import { ArrowLeftIcon, ChecklistIcon, TrashIcon } from "../ui/iconos";

interface Props {
  open: boolean;
  caseId: string | null;
}

type Pestana = EstadoCandidato | "aceptados";

const ESPECIALISTAS = ["fiscal", "hipoteca", "luz", "caducidades", "ayudas", "suscripciones", "correo"];
const ORIGEN: Record<string, string> = {
  valoracion: "Lo corregiste",
  voto: "Lo marcaste como incorrecto",
  senal: "Detectado automáticamente",
};
const PESTANAS: Array<{ id: Pestana; etiqueta: string }> = [
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

function Formulario({ detalle, onHecho }: { detalle: DetalleCandidato; onHecho: (texto: string) => void }) {
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
    <div className="flex flex-col gap-4">
      <Caja as="section" aria-label="Conversación">
        <Caja.Cabecera titulo="Conversación (datos personales ocultos)" nivel={4} />
        <Caja.Cuerpo className="flex flex-col gap-2">
          <p className="m-0">
            <span className="font-semibold">Pregunta: </span>
            {contenido.pregunta}
          </p>
          {contenido.respuesta && (
            <p className="m-0 whitespace-pre-line">
              <span className="font-semibold">Respuesta{contenido.respuesta_parcial ? " (reconstruida de sus afirmaciones)" : ""}: </span>
              {contenido.respuesta}
            </p>
          )}
          {contenido.siguiente_pregunta && (
            <p className="m-0">
              <span className="font-semibold">Luego preguntó: </span>
              {contenido.siguiente_pregunta}
            </p>
          )}
        </Caja.Cuerpo>
      </Caja>

      <section aria-label="Qué falló" className="flex flex-col gap-1">
        <Heading as="h4" variant="small">
          Qué falló: {detalle.etiqueta_fallo}
        </Heading>
        <p className="m-0 text-xs text-fg-muted">
          {ORIGEN[c.origen]} · {fecha(c.creado_el)}
          {detalle.frecuencia > 1 ? ` · ha pasado ${detalle.frecuencia} veces` : ""}
          {c.importe_en_juego ? ` · ${c.importe_en_juego} € en juego` : ""}
        </p>
        {contenido.correccion.comentario && <p className="m-0">«{contenido.correccion.comentario}»</p>}
        {contenido.correccion.cifra_correcta && <p className="m-0">Cifra correcta según el usuario: {contenido.correccion.cifra_correcta}</p>}
        {contenido.senales.length > 0 && (
          <ul className="m-0 list-disc pl-5 text-xs">
            {contenido.senales.map((s, i) => (
              <li key={i}>
                {s.tipo}
                {s.detalle ? `: ${s.detalle}` : ""}
              </li>
            ))}
          </ul>
        )}
        {(contenido.cerebro?.puerta?.violaciones ?? []).map((v, i) => (
          <p key={i} className="m-0 text-xs text-fg-danger">
            Puerta de cifras: {v.tipo} («{v.literal}») — {v.detalle}
          </p>
        ))}
      </section>

      {contenido.cerebro && (
        <section aria-label="Plan del cerebro" className="flex flex-col gap-2">
          <Heading as="h4" variant="small">
            Cómo lo preparó
          </Heading>
          <p className="m-0 text-fg-muted">
            {contenido.cerebro.intencion.resumen || contenido.cerebro.intencion.tipo} · ruta {contenido.cerebro.ruta} · confianza{" "}
            {Math.round(contenido.cerebro.intencion.confianza * 100)} %
          </p>
          <Caja condensada>
            <Caja.Lista>
              {contenido.cerebro.intencion.encargos.map((e) => {
                const informe = informes.find((i) => i.encargo_id === e.id);
                return (
                  <Caja.Fila key={e.id} className="text-xs">
                    <span className="font-semibold">{e.especialista}</span>
                    {e.depende_de.length ? ` (espera a ${e.depende_de.join(", ")})` : ""}: {e.pregunta}
                    {informe && (
                      <span className="block text-fg-muted">
                        {informe.estado}
                        {informe.modo ? ` · ${informe.modo}` : ""}
                        {informe.cifras.length ? ` · ${informe.cifras.map((x) => `${x.concepto}: ${x.literal || x.valor}`).join("; ")}` : ""}
                      </span>
                    )}
                  </Caja.Fila>
                );
              })}
            </Caja.Lista>
          </Caja>
          {contenido.cerebro.peticiones.length > 0 && (
            <p className="m-0 text-xs">
              Peticiones: {contenido.cerebro.peticiones.map((p) => `${p.solicitante} → ${p.destinatario}: ${p.dato}`).join("; ")}
            </p>
          )}
        </section>
      )}

      {!contenido.cerebro && contenido.cerebro_resumen && (
        <p className="m-0 text-xs">
          Cómo lo preparó: {contenido.cerebro_resumen.tipo ?? "—"} · ruta {contenido.cerebro_resumen.ruta ?? "—"} · consultó a{" "}
          {(contenido.cerebro_resumen.especialistas ?? []).join(", ") || "nadie"}
          {contenido.cerebro_resumen.violaciones_cifras ? ` · ${contenido.cerebro_resumen.violaciones_cifras} problemas en la puerta de cifras` : ""}
        </p>
      )}

      {contenido.enmascarado.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer font-semibold">Qué se ha ocultado ({contenido.enmascarado.length})</summary>
          <ul className="m-0 mt-1 list-none p-0">
            {contenido.enmascarado.map((m, i) => (
              <li key={i}>
                {m.campo}: {m.tipo} {m.vista} → {m.sustituto}
              </li>
            ))}
          </ul>
        </details>
      )}

      {c.estado === "pendiente" && (
        <Caja as="section" aria-label="Cómo debería haber respondido" variante="acento">
          <Caja.Cabecera titulo="Cómo debería haber respondido" nivel={4} />
          <Caja.Cuerpo>
            <form
              className="flex flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                aceptar();
              }}
            >
              <FormControl>
                <FormControl.Label>Categoría</FormControl.Label>
                <Select block value={categoria} onChange={(e) => setCategoria(e.target.value)}>
                  {detalle.categorias.map((cat) => (
                    <Select.Option key={cat.clave} value={cat.clave}>
                      {cat.clave}
                      {cat.critica ? " (crítica)" : ""}
                    </Select.Option>
                  ))}
                </Select>
              </FormControl>
              <FormControl>
                <FormControl.Label>Pregunta para la prueba</FormControl.Label>
                <Textarea block value={pregunta} onChange={(e) => setPregunta(e.target.value)} rows={2} maxLength={4000} />
                <FormControl.Caption>Puedes quitar lo que aún sea personal.</FormControl.Caption>
              </FormControl>
              <fieldset className="m-0 border-0 p-0">
                <legend className="mb-2 text-sm font-semibold">Especialistas: debe consultar / no debe consultar</legend>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1">
                  {especialistas.map((e) => (
                    <span key={e} className="flex items-center gap-2">
                      <Checkbox aria-label={`Debe consultar ${e}`} checked={obligatorios.includes(e)} onChange={() => alternar(obligatorios, e, setObligatorios)} />
                      <Checkbox aria-label={`No debe consultar ${e}`} checked={prohibidos.includes(e)} onChange={() => alternar(prohibidos, e, setProhibidos)} />
                      {e}
                    </span>
                  ))}
                </div>
              </fieldset>
              <div className="grid gap-3 sm:grid-cols-2">
                <FormControl>
                  <FormControl.Label>Citas mínimas</FormControl.Label>
                  <TextInput block type="number" min={0} max={20} value={String(citas)} onChange={(e) => setCitas(Number(e.target.value) || 0)} />
                </FormControl>
                <FormControl>
                  <FormControl.Label>Cifras que no debe dar</FormControl.Label>
                  <TextInput block value={cifrasProhibidas} onChange={(e) => setCifrasProhibidas(e.target.value)} placeholder="p. ej. 532,00 €" />
                </FormControl>
              </div>
              <fieldset className="m-0 flex flex-col gap-3 rounded-medium border border-border-default bg-canvas-muted p-3">
                <legend className="px-1 text-sm font-semibold">Cifra esperada (siempre de una calculadora)</legend>
                <div className="grid gap-3 sm:grid-cols-2">
                  <FormControl>
                    <FormControl.Label>Calculadora</FormControl.Label>
                    <TextInput block value={herramienta} onChange={(e) => setHerramienta(e.target.value)} placeholder="hipoteca_cuadro" />
                  </FormControl>
                  <FormControl>
                    <FormControl.Label>Cifra que localiza la línea</FormControl.Label>
                    <TextInput block value={referencia} onChange={(e) => setReferencia(e.target.value)} placeholder="474,21 €" />
                  </FormControl>
                </div>
                <FormControl>
                  <FormControl.Label>Argumentos (JSON)</FormControl.Label>
                  <TextInput block className="font-mono" value={argumentos} onChange={(e) => setArgumentos(e.target.value)} />
                </FormControl>
                <FormControl>
                  <FormControl.Label>Debe atribuirse a</FormControl.Label>
                  <Select block value={especialistaCifra} onChange={(e) => setEspecialistaCifra(e.target.value)}>
                    <Select.Option value="">—</Select.Option>
                    {especialistas.map((e) => (
                      <Select.Option key={e} value={e}>
                        {e}
                      </Select.Option>
                    ))}
                  </Select>
                </FormControl>
              </fieldset>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                <FormControl>
                  <Checkbox checked={abstenerse} onChange={(e) => setAbstenerse(e.target.checked)} />
                  <FormControl.Label>Debe abstenerse</FormControl.Label>
                </FormControl>
                <FormControl>
                  <Checkbox checked={aclarar} onChange={(e) => setAclarar(e.target.checked)} />
                  <FormControl.Label>Debe preguntar antes</FormControl.Label>
                </FormControl>
                <FormControl>
                  <Checkbox checked={puerta} onChange={(e) => setPuerta(e.target.checked)} />
                  <FormControl.Label>Cifras sin problemas en la puerta</FormControl.Label>
                </FormControl>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <FormControl>
                  <FormControl.Label>Debe decir</FormControl.Label>
                  <Textarea block value={presentes} onChange={(e) => setPresentes(e.target.value)} rows={2} />
                  <FormControl.Caption>Uno por línea.</FormControl.Caption>
                </FormControl>
                <FormControl>
                  <FormControl.Label>No debe decir</FormControl.Label>
                  <Textarea block value={ausentes} onChange={(e) => setAusentes(e.target.value)} rows={2} />
                  <FormControl.Caption>Uno por línea.</FormControl.Caption>
                </FormControl>
              </div>
              <FormControl>
                <FormControl.Label>Descripción (opcional)</FormControl.Label>
                <TextInput block value={descripcion} onChange={(e) => setDescripcion(e.target.value)} maxLength={500} />
              </FormControl>
              <FormControl>
                <Checkbox checked={aunque} onChange={(e) => setAunque(e.target.checked)} />
                <FormControl.Label>Aceptar aunque la respuesta original pasara estas comprobaciones</FormControl.Label>
              </FormControl>
              {error && (
                <Flash variant="danger" role="alert">
                  {error}
                </Flash>
              )}
              <div className="flex flex-wrap items-end gap-2">
                <Button type="submit" variant="primary" loading={enviando}>
                  Aceptar como prueba
                </Button>
                <TextInput
                  className="min-w-0 flex-1"
                  value={motivo}
                  onChange={(e) => setMotivo(e.target.value)}
                  maxLength={500}
                  placeholder="Motivo para rechazar"
                  aria-label="Motivo para rechazar"
                />
                <Button variant="danger" disabled={enviando} onClick={rechazar}>
                  Rechazar
                </Button>
              </div>
            </form>
          </Caja.Cuerpo>
        </Caja>
      )}
      {c.estado === "rechazado" && c.motivo_rechazo && <p className="m-0 text-xs text-fg-muted">Rechazado: {c.motivo_rechazo}</p>}
    </div>
  );
}

function Vacio({ titulo, texto }: { titulo: string; texto?: string }) {
  return (
    <Blankslate narrow>
      <Blankslate.Visual>
        <ChecklistIcon size={24} />
      </Blankslate.Visual>
      <Blankslate.Heading as="h3">{titulo}</Blankslate.Heading>
      {texto && <Blankslate.Description>{texto}</Blankslate.Description>}
    </Blankslate>
  );
}

export function RevisionCasosPanel({ open, caseId }: Props) {
  const [pestana, setPestana] = useState<Pestana>("pendiente");
  const [cola, setCola] = useState<ColaRevision | null>(null);
  const [aceptados, setAceptados] = useState<Aceptado[] | null>(null);
  const [detalle, setDetalle] = useState<DetalleCandidato | null>(null);
  const [aprendizaje, setAprendizaje] = useState<EstadoAprendizaje | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  // The last load failed: its error can be retried (an error deciding on a case cannot).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const confirmar = useConfirm();
  const idAprendizaje = useId();

  const recargar = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const fallo = (err: unknown) => {
      if (!controller.signal.aborted) setErrorCarga(mensaje(err));
    };
    if (pestana === "aceptados") {
      getAceptados(controller.signal).then((r) => {
        setAceptados(r.casos);
        setErrorCarga(null);
      }, fallo);
    } else {
      getColaRevision(pestana, controller.signal).then((r) => {
        setCola(r);
        setErrorCarga(null);
      }, fallo);
    }
    if (caseId) getAprendizaje(caseId, controller.signal).then(setAprendizaje, () => setAprendizaje(null));
    return () => controller.abort();
  }, [open, pestana, caseId, tick]);

  const elegir = (p: Pestana) => {
    if (p === pestana) return;
    // The list of another tab must not show under this one while it loads.
    setCola(null);
    setPestana(p);
  };

  const abrir = (id: string) => {
    setError(null);
    getCandidato(id).then(setDetalle, (err: unknown) => setError(mensaje(err)));
  };

  const alternarAprendizaje = () => {
    if (!caseId || !aprendizaje) return;
    cambiarAprendizaje(caseId, !aprendizaje.activo, aprendizaje.version).then(
      (r) => {
        setAprendizaje(r);
        recargar();
      },
      (err: unknown) => setError(mensaje(err)),
    );
  };

  const borrar = async (a: Aceptado) => {
    const si = await confirmar({
      title: "¿Borrar este caso?",
      content: "Dejará de comprobarse en las evaluaciones.",
      confirmButtonContent: "Borrar",
      cancelButtonContent: "Cancelar",
      confirmButtonType: "danger",
    });
    if (si) borrarAceptado(a.id).then(recargar, (err: unknown) => setError(mensaje(err)));
  };

  const grupos = cola?.grupos ?? [];

  return (
    <div className="flex flex-col gap-4">
      {caseId && aprendizaje && (
        <Caja>
          <Caja.Cuerpo className="flex items-start gap-4">
            <span id={idAprendizaje} className="min-w-0 flex-1">
              {aprendizaje.texto}
            </span>
            <ToggleSwitch
              aria-labelledby={idAprendizaje}
              size="small"
              checked={aprendizaje.activo}
              onClick={alternarAprendizaje}
              buttonLabelOn="Sí"
              buttonLabelOff="No"
            />
          </Caja.Cuerpo>
        </Caja>
      )}
      {aviso && (
        <Flash variant="success" role="status">
          {aviso}
        </Flash>
      )}
      {errorCarga !== null && <AvisoError frase="No he podido cargar la revisión." detalle={errorCarga} onReintentar={recargar} />}
      {error && <AvisoError frase={error} />}
      {detalle ? (
        <div className="flex flex-col gap-3">
          <div>
            <Button size="small" variant="invisible" leadingVisual={ArrowLeftIcon} onClick={() => setDetalle(null)}>
              Volver a la lista
            </Button>
          </div>
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
          <SegmentedControl aria-label="Estado de los casos" onChange={(i) => elegir(PESTANAS[i]?.id ?? "pendiente")}>
            {PESTANAS.map((p) => (
              <SegmentedControl.Button key={p.id} selected={pestana === p.id}>
                {p.etiqueta}
              </SegmentedControl.Button>
            ))}
          </SegmentedControl>
          {pestana === "aceptados" ? (
            aceptados === null ? (
              errorCarga === null && <CargandoLista filas={2} etiqueta="Cargando los casos aceptados" />
            ) : aceptados.length === 0 ? (
              <Vacio titulo="Aún no hay casos aceptados" />
            ) : (
              <Caja as="section" aria-label="Casos aceptados">
                <Caja.Cabecera titulo="Casos aceptados" acciones={<CounterLabel>{aceptados.length}</CounterLabel>} />
                <Caja.Lista>
                  {aceptados.map((a) => (
                    <Caja.Fila key={a.id} className="flex items-start gap-3">
                      <div className="flex min-w-0 flex-1 flex-col gap-1">
                        <span className="font-semibold">{a.descripcion || a.id}</span>
                        <span className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
                          <Label variant="secondary">{a.categoria}</Label>
                          {a.expectativas.length} comprobaciones · {fecha(a.aceptado_el)}
                          {!a.vigente && <Label variant="attention">retirado{a.motivo_baja ? `: ${a.motivo_baja}` : ""}</Label>}
                        </span>
                        {a.avisos.map((t, i) => (
                          <span key={i} className="text-xs">
                            {t}
                          </span>
                        ))}
                      </div>
                      <Button size="small" variant="danger" leadingVisual={TrashIcon} onClick={() => void borrar(a)}>
                        Borrar
                      </Button>
                    </Caja.Fila>
                  ))}
                </Caja.Lista>
              </Caja>
            )
          ) : cola === null ? (
            errorCarga === null && <CargandoLista filas={2} etiqueta="Cargando los casos" />
          ) : grupos.length === 0 ? (
            pestana === "pendiente" ? (
              <Vacio
                titulo="No hay nada pendiente"
                texto="Cuando marques una respuesta como incorrecta (o, si lo activas, cuando el asistente detecte un fallo), aparecerá aquí."
              />
            ) : (
              <Vacio titulo="Nada por aquí" />
            )
          ) : (
            <Caja as="section" aria-label={pestana === "pendiente" ? "Casos pendientes" : "Casos rechazados"}>
              <Caja.Cabecera
                titulo={pestana === "pendiente" ? "Casos pendientes" : "Casos rechazados"}
                acciones={<CounterLabel>{grupos.length}</CounterLabel>}
              />
              <Caja.Lista>
                {grupos.map((g) => (
                  <Caja.Fila key={g.id} className="flex flex-col gap-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <Link as="button" type="button" className="text-left font-semibold" onClick={() => abrir(g.id)}>
                        {g.pregunta}
                      </Link>
                      <Label variant="danger">{g.etiqueta_fallo}</Label>
                      <Label variant="secondary">{g.categoria_sugerida}</Label>
                      {g.critica && <Label variant="severe">crítica</Label>}
                      {g.frecuencia > 1 && <CounterLabel aria-label={`${g.frecuencia} veces`}>×{g.frecuencia}</CounterLabel>}
                    </span>
                    <span className="text-xs text-fg-muted">
                      {ORIGEN[g.origen]} · {fecha(g.creado_el)} · prioridad {g.prioridad.toFixed(0)}
                      {g.importe_en_juego ? ` · ${g.importe_en_juego} € en juego` : ""}
                    </span>
                  </Caja.Fila>
                ))}
              </Caja.Lista>
            </Caja>
          )}
        </>
      )}
      <p className="m-0 text-xs text-fg-muted">
        Lo que aceptes se guarda enmascarado y en privado (nunca en el código del programa) para comprobar que el error no vuelve; se exporta y se
        borra con tu cuenta.
      </p>
    </div>
  );
}
