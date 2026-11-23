// «Consejo»: the household council's sessions of this case — the convocations waiting for the user, the ones running
// (live), the rulings, and those that could not finish — plus a way to put a decision to the council directly. Each
// session opens its card (estimate and confirmation, progress, ruling); a finished one can be deleted.
//
// A page (registro.ts: presentacion "pagina"): the shell puts the title, the description and the icon. The sessions
// are a Box list in the style of an issue list, each with its state as a StateLabel.

import { useCallback, useEffect, useState } from "react";

import { EN_MARCHA, borrarSesion, convocarConsejo, listarSesiones, usd, type ListaSesiones } from "../apiConsejo";
import { ConvocarConsejo } from "../components/ConvocarConsejo";
import { EstadoSesionLabel } from "../components/conversacion/EstadoSesion";
import { errorMessage } from "../lib/memoria";
import { relativeTime } from "../lib/presentation";
import { AvisoError, Blankslate, Button, Caja, CargandoLista, CounterLabel, Flash, FormControl, Link, Textarea, TextInput, useConfirm } from "../ui";
import { ArrowLeftIcon, PeopleIcon, TrashIcon } from "../ui/iconos";
import type { PropsPanel } from "./tipos";

function Nueva({ caseId, onCreada }: { caseId: string; onCreada: (id: string) => void }) {
  const [pregunta, setPregunta] = useState("");
  const [preferencia, setPreferencia] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Caja as="section" aria-label="Plantear una decisión al consejo">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setEnviando(true);
          setError(null);
          convocarConsejo(caseId, { pregunta: pregunta.trim(), preferencia: preferencia.trim() || null })
            .then((s) => {
              setPregunta("");
              setPreferencia("");
              onCreada(s.id);
            })
            .catch((err: unknown) => setError(errorMessage(err)))
            .finally(() => setEnviando(false));
        }}
      >
        <Caja.Cuerpo className="flex flex-col gap-3">
          <FormControl required>
            <FormControl.Label>Plantea una decisión difícil</FormControl.Label>
            <Textarea
              block
              minLength={10}
              maxLength={4000}
              rows={3}
              value={pregunta}
              placeholder="¿Amortizo la hipoteca o invierto los ahorros?"
              onChange={(e) => setPregunta(e.target.value)}
            />
          </FormControl>
          <FormControl>
            <FormControl.Label>Hacia dónde te inclinas (opcional)</FormControl.Label>
            <TextInput block maxLength={600} value={preferencia} onChange={(e) => setPreferencia(e.target.value)} />
            <FormControl.Caption>El consejo no lo verá hasta revisar sus propuestas.</FormControl.Caption>
          </FormControl>
          {error && (
            <Flash variant="danger" role="alert">
              {error}
            </Flash>
          )}
        </Caja.Cuerpo>
        <Caja.Pie className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs text-fg-muted">Te diré antes cuánto cuesta; nada se ejecuta hasta que lo confirmes.</span>
          <Button type="submit" variant="primary" disabled={enviando || pregunta.trim().length < 10} loading={enviando}>
            Ver el coste
          </Button>
        </Caja.Pie>
      </form>
    </Caja>
  );
}

export default function PanelConsejo({ ctx, open }: PropsPanel) {
  const caseId = ctx.caso.id;
  const [datos, setDatos] = useState<ListaSesiones | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [abierta, setAbierta] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const recargar = useCallback(() => setTick((n) => n + 1), []);
  // Loading the sessions failed: it can be retried (an error deleting one cannot).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const confirmar = useConfirm();

  useEffect(() => {
    if (!open || !caseId) return;
    const controller = new AbortController();
    listarSesiones(caseId, controller.signal).then(
      (lista) => {
        setDatos(lista);
        setErrorCarga(null);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setErrorCarga(errorMessage(err));
      },
    );
    return () => controller.abort();
  }, [open, caseId, tick]);

  const sesiones = datos?.sesiones ?? [];
  const seleccionada = sesiones.find((s) => s.id === abierta) ?? null;

  async function borrar(id: string) {
    if (!caseId) return;
    const si = await confirmar({
      title: "¿Borro esta sesión del consejo?",
      content: "Se borran el expediente, las propuestas y el dictamen. No se puede deshacer.",
      confirmButtonContent: "Borrar",
      cancelButtonContent: "Cancelar",
      confirmButtonType: "danger",
    });
    if (!si) return;
    borrarSesion(caseId, id).then(
      () => {
        setAbierta(null);
        recargar();
      },
      (err: unknown) => setError(errorMessage(err)),
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {!caseId && (
        <Blankslate narrow>
          <Blankslate.Visual>
            <PeopleIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading as="h3">Aún no hay nada en el consejo</Blankslate.Heading>
          <Blankslate.Description>
            Cuando me plantees una decisión en una conversación, aquí verás lo que el consejo estudió y sus dictámenes.
          </Blankslate.Description>
        </Blankslate>
      )}
      {errorCarga !== null && <AvisoError frase="No he podido cargar las sesiones del consejo." detalle={errorCarga} onReintentar={recargar} />}
      {error && <AvisoError frase={error} />}
      {caseId && datos && !datos.disponible && (
        <Flash variant="warning">El consejo no está disponible en este servidor. Puedes seguir viendo y borrando lo que estudió.</Flash>
      )}

      {caseId && seleccionada && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap justify-between gap-2">
            <Button size="small" variant="invisible" leadingVisual={ArrowLeftIcon} onClick={() => setAbierta(null)}>
              Todas las sesiones
            </Button>
            {!EN_MARCHA.has(seleccionada.estado) && (
              <Button size="small" variant="danger" leadingVisual={TrashIcon} onClick={() => void borrar(seleccionada.id)}>
                Borrar esta sesión
              </Button>
            )}
          </div>
          <ConvocarConsejo caseId={caseId} sesionId={seleccionada.id} onCambio={recargar} />
        </div>
      )}

      {caseId && !seleccionada && (
        <>
          {datos?.disponible !== false && (
            <Nueva
              caseId={caseId}
              onCreada={(id) => {
                setAbierta(id);
                recargar();
              }}
            />
          )}
          {!datos && errorCarga === null && <CargandoLista filas={2} etiqueta="Cargando las sesiones del consejo" />}
          {datos && sesiones.length === 0 && (
            <Blankslate narrow>
              <Blankslate.Visual>
                <PeopleIcon size={24} />
              </Blankslate.Visual>
              <Blankslate.Heading as="h3">Todavía no has consultado nada al consejo</Blankslate.Heading>
              <Blankslate.Description>
                Te lo propondré cuando una decisión lo merezca (elegir entre opciones difíciles de deshacer, importes grandes, mucha
                incertidumbre), o puedes pedírmelo: «consúltalo con el consejo».
              </Blankslate.Description>
            </Blankslate>
          )}
          {sesiones.length > 0 && (
            <Caja as="section" aria-label="Sesiones del consejo">
              <Caja.Cabecera titulo="Sesiones" acciones={<CounterLabel>{sesiones.length}</CounterLabel>} />
              <Caja.Lista>
                {sesiones.map((s) => (
                  <Caja.Fila key={s.id} className="flex flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <EstadoSesionLabel estado={s.estado} />
                      <Link as="button" type="button" onClick={() => setAbierta(s.id)} className="min-w-0 text-left font-semibold text-fg-default">
                        {s.pregunta_usuario}
                      </Link>
                    </div>
                    <p className="m-0 text-xs text-fg-muted">
                      {relativeTime(Date.parse(s.creada))} · {s.estado === "pendiente_confirmacion" ? `coste estimado ${usd(s.estimacion_usd)}` : `coste ${usd(s.coste_usd)}`}
                    </p>
                    {s.parcial && <p className="m-0 text-xs text-fg-attention">Dictamen parcial: {s.parcial}</p>}
                    {s.recomendacion && (
                      <p className="m-0">
                        Recomendación: {s.recomendacion}
                        {s.consenso ? ` (${s.consenso})` : ""}
                      </p>
                    )}
                  </Caja.Fila>
                ))}
              </Caja.Lista>
            </Caja>
          )}
        </>
      )}

      <p className="m-0 text-xs text-fg-muted">
        El consejo no calcula cifras ni interpreta la ley: usa los cálculos de los especialistas. Sus miembros no reciben tu nombre ni
        datos que la decisión no necesite. Un dictamen es una ayuda para decidir: la decisión es tuya.
      </p>
    </div>
  );
}
