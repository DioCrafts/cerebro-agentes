// «Mensajería»: talk to the household assistant from SimpleX Chat.
//
// What the user does here: install SimpleX, scan the assistant's address (a QR drawn in the browser, no third-party
// service sees it) or open its link, generate a one-time pairing code and send it from the chat. Below, the paired
// chats: pause the notices sent there, or unpair. A privacy note says what is kept and what is not, in plain words.

import { Bell, BellOff, Check, Copy, MessageCircle, QrCode, RefreshCw, ShieldCheck, Unlink, X } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useState } from "react";

import { ApiError } from "../api";
import {
  desvincular,
  getDispositivos,
  getEstadoMensajeria,
  pausarAvisos,
  pedirCodigo,
  type CodigoEmparejamiento,
  type DispositivoMensajeria,
  type EstadoMensajeria,
} from "../apiMensajeria";
import { formatDate } from "../lib/presentation";
import { codificarQR, trazadoQR } from "../lib/qr";

interface MensajeriaPanelProps {
  open: boolean;
  onClose: () => void;
  caseId: string | null;
}

const BOTON =
  "inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-[13px] font-semibold text-ink-soft hover:border-primary hover:text-primary-dark focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-50";
const BOTON_PRINCIPAL =
  "inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-[13.5px] font-semibold text-white hover:bg-primary-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-50";
const BOTON_PELIGRO =
  "inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-[13px] font-semibold text-ink-soft hover:border-risk-high-fg hover:text-risk-high-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:opacity-50";
const SIMPLEX_WEB = "https://simplex.chat/";

function mensaje(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "No se ha podido completar. Inténtalo de nuevo.";
}

function mmss(segundos: number): string {
  const m = Math.floor(segundos / 60);
  const s = segundos % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** Only links a messaging app opens: never another scheme (the address comes from the server, but a link is a link). */
function enlaceSeguro(direccion: string): string | null {
  return /^(https:\/\/|simplex:)/i.test(direccion) ? direccion : null;
}

async function copiar(texto: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch {
    return false;
  }
}

/** The bot's address as a QR code (black on white whatever the theme: scanners need the contrast). */
function CodigoQRDireccion({ direccion }: { direccion: string }) {
  const qr = useMemo(() => {
    try {
      return codificarQR(direccion, "M");
    } catch {
      return null;
    }
  }, [direccion]);
  if (qr === null) return null;
  const lado = qr.tamano + 8;
  return (
    <svg
      role="img"
      aria-label="Código QR con la dirección del asistente en SimpleX"
      viewBox={`0 0 ${lado} ${lado}`}
      shapeRendering="crispEdges"
      className="h-52 w-52 max-w-full rounded-lg border border-line"
    >
      <rect width={lado} height={lado} fill="#ffffff" />
      <path d={trazadoQR(qr)} fill="#000000" />
    </svg>
  );
}

function Dispositivo({
  dispositivo,
  ocupado,
  onPausa,
  onDesvincular,
}: {
  dispositivo: DispositivoMensajeria;
  ocupado: boolean;
  onPausa: (pausado: boolean) => void;
  onDesvincular: () => void;
}) {
  const [confirmando, setConfirmando] = useState(false);
  const nombre = dispositivo.nombre_mostrado || `Chat de ${dispositivo.nombre_canal}`;
  const desde = formatDate(dispositivo.creado);
  const uso = formatDate(dispositivo.ultimo_uso);
  return (
    <li className="space-y-2 rounded-xl border border-line bg-surface px-4 py-3">
      <div>
        <p className="font-semibold text-ink">{nombre}</p>
        <p className="text-[12.5px] text-muted">
          {dispositivo.nombre_canal} · vinculado el {desde}
          {uso ? ` · último uso el ${uso}` : ""}
          {dispositivo.avisos_en_pausa ? " · avisos en pausa" : ""}
        </p>
      </div>
      {confirmando ? (
        <div role="group" aria-label={`Confirmar desvincular ${nombre}`} className="space-y-2">
          <p>Dejaré de responder en ese chat y de enviarle avisos. ¿Lo desvinculo?</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={ocupado} onClick={onDesvincular} className={BOTON_PELIGRO}>
              <Unlink size={14} aria-hidden /> Sí, desvincular
            </button>
            <button type="button" onClick={() => setConfirmando(false)} className={BOTON}>
              Cancelar
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={ocupado}
            onClick={() => onPausa(!dispositivo.avisos_en_pausa)}
            className={BOTON}
            aria-pressed={dispositivo.avisos_en_pausa}
          >
            {dispositivo.avisos_en_pausa ? <Bell size={14} aria-hidden /> : <BellOff size={14} aria-hidden />}
            {dispositivo.avisos_en_pausa ? "Reanudar avisos" : "Pausar avisos"}
          </button>
          <button type="button" disabled={ocupado} onClick={() => setConfirmando(true)} className={BOTON_PELIGRO}>
            <Unlink size={14} aria-hidden /> Desvincular
          </button>
        </div>
      )}
    </li>
  );
}

export function MensajeriaPanel({ open, onClose, caseId }: MensajeriaPanelProps) {
  const idTitulo = useId();
  const [estado, setEstado] = useState<EstadoMensajeria | null>(null);
  const [dispositivos, setDispositivos] = useState<DispositivoMensajeria[] | null>(null);
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [codigo, setCodigo] = useState<CodigoEmparejamiento | null>(null);
  const [fin, setFin] = useState<number | null>(null);
  // How many chats were paired when the code was generated (a new one means the code was used).
  const [base, setBase] = useState<number | null>(null);
  const [restante, setRestante] = useState(0);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const recargar = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    Promise.all([getEstadoMensajeria(controller.signal), getDispositivos(controller.signal)]).then(
      ([e, d]) => {
        setEstado(e);
        setDispositivos(d);
        setErrorCarga(null);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setErrorCarga(mensaje(err));
      },
    );
    return () => controller.abort();
  }, [open, tick]);

  // The code's countdown, from its deadline (a sleeping tab catches up when it wakes).
  useEffect(() => {
    if (fin === null) return;
    const actualizar = () => setRestante(Math.max(0, Math.round((fin - Date.now()) / 1000)));
    const id = window.setInterval(actualizar, 1000);
    const primero = window.setTimeout(actualizar, 0);
    return () => {
      window.clearInterval(id);
      window.clearTimeout(primero);
    };
  }, [fin]);

  // A new chat paired while the code is on screen: the list refreshes every few seconds until it appears.
  useEffect(() => {
    if (!open || codigo === null || base === null) return;
    const id = window.setInterval(() => {
      getDispositivos().then(
        (d) => {
          setDispositivos(d);
          if (d.length > base) {
            setCodigo(null);
            setFin(null);
            setBase(null);
            setAviso("¡Chat vinculado! Ya puedes preguntarle a tu asistente desde SimpleX.");
          }
        },
        () => undefined,
      );
    }, 5000);
    return () => window.clearInterval(id);
  }, [open, codigo, base]);

  async function hacer(accion: () => Promise<string | null>) {
    setOcupado(true);
    setError(null);
    setAviso(null);
    try {
      setAviso(await accion());
    } catch (err) {
      setError(mensaje(err));
    } finally {
      setOcupado(false);
      recargar();
    }
  }

  if (!open) return null;

  const canal = estado?.canales[0] ?? null;
  const direccion = codigo?.direccion ?? canal?.direccion ?? null;
  const lleno = estado !== null && dispositivos !== null && dispositivos.length >= estado.max_dispositivos;
  const vigente = codigo !== null && restante > 0;

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button type="button" aria-label="Cerrar la mensajería" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby={idTitulo}
        className="relative flex h-full w-full max-w-[460px] flex-col border-l border-line bg-paper shadow-pop"
      >
        <header className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <h2 id={idTitulo} className="flex items-center gap-2 font-serif text-[19px] font-bold text-ink">
              <MessageCircle size={18} aria-hidden /> Mensajería
            </h2>
            <p className="text-[12.5px] text-muted">Habla con tu asistente desde SimpleX Chat</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Cerrar" className="rounded-lg p-2 text-muted hover:bg-line/60 hover:text-ink">
            <X size={18} />
          </button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5 text-[13.5px] leading-relaxed text-ink-soft">
          <p>
            Pregúntale lo que quieras a tu asistente desde el móvil, como en esta app. Usamos{" "}
            <strong className="text-ink">SimpleX Chat</strong>: los mensajes van cifrados de extremo a extremo y no necesita tu número de
            teléfono ni ningún identificador tuyo. El asistente solo responde a los chats que vincules aquí.
          </p>

          {errorCarga && (
            <p role="alert" className="rounded-lg bg-risk-high-bg px-3 py-2 text-risk-high-fg">
              {errorCarga}
            </p>
          )}
          {estado === null && !errorCarga && <p role="status">Comprobando…</p>}

          {estado !== null && !estado.activa && (
            <p className="rounded-xl border border-line bg-surface px-4 py-3">
              Este servicio no tiene activada la mensajería. Puedes seguir usando el asistente aquí, en la app.
            </p>
          )}

          {estado !== null && estado.activa && (
            <ol className="space-y-4">
              <li className="rounded-xl border border-line bg-surface px-4 py-3">
                <h3 className="font-semibold text-ink">1. Instala SimpleX Chat</h3>
                <p className="mt-1">
                  Está para Android, iPhone y ordenador:{" "}
                  <a href={SIMPLEX_WEB} target="_blank" rel="noopener noreferrer" className="font-semibold text-primary-dark underline">
                    simplex.chat
                  </a>
                  .
                </p>
              </li>

              <li className="space-y-2 rounded-xl border border-line bg-surface px-4 py-3">
                <h3 className="flex items-center gap-2 font-semibold text-ink">
                  <QrCode size={15} aria-hidden /> 2. Conecta con tu asistente
                </h3>
                {direccion ? (
                  <>
                    <p>Escanea este código con SimpleX o, en el móvil, abre el enlace:</p>
                    <CodigoQRDireccion direccion={direccion} />
                    <div className="flex flex-wrap gap-2">
                      {enlaceSeguro(direccion) && (
                        <a href={enlaceSeguro(direccion) ?? undefined} className={BOTON}>
                          Abrir en SimpleX
                        </a>
                      )}
                      <button
                        type="button"
                        className={BOTON}
                        onClick={() =>
                          void copiar(direccion).then((ok) =>
                            setAviso(ok ? "Enlace copiado." : "No se ha podido copiar: mantén pulsado el botón «Abrir en SimpleX»."),
                          )
                        }
                      >
                        <Copy size={14} aria-hidden /> Copiar el enlace
                      </button>
                    </div>
                    {canal !== null && !canal.conectado && (
                      <p className="text-[12.5px] text-muted">
                        Ahora mismo el asistente no está conectado a SimpleX; tu mensaje le llegará cuando vuelva.
                      </p>
                    )}
                  </>
                ) : (
                  <p>{canal?.detalle || "La dirección del asistente aún no está disponible. Vuelve a intentarlo en unos minutos."}</p>
                )}
              </li>

              <li className="space-y-2 rounded-xl border border-line bg-surface px-4 py-3">
                <h3 className="font-semibold text-ink">3. Envíale tu código</h3>
                {caseId === null ? (
                  <p>Haz primero una pregunta en el chat de la app para crear tu expediente; después vuelve aquí.</p>
                ) : lleno ? (
                  <p>Ya tienes vinculado el máximo de chats. Desvincula uno abajo para emparejar otro.</p>
                ) : vigente && codigo ? (
                  <div className="space-y-2">
                    <p>Escribe este código en el chat con tu asistente (solo sirve una vez):</p>
                    <output
                      aria-live="polite"
                      className="block select-all rounded-lg border border-primary/40 bg-paper px-3 py-2 text-center font-mono text-[22px] font-bold tracking-widest text-ink"
                    >
                      {codigo.codigo}
                    </output>
                    <p className="text-[12.5px] text-muted">Caduca en {mmss(restante)}.</p>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        className={BOTON}
                        onClick={() => void copiar(codigo.codigo).then((ok) => setAviso(ok ? "Código copiado." : "No se ha podido copiar."))}
                      >
                        <Copy size={14} aria-hidden /> Copiar el código
                      </button>
                      <button type="button" className={BOTON} onClick={recargar}>
                        <RefreshCw size={14} aria-hidden /> Ya lo he enviado
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {codigo !== null && <p>El código ha caducado. Genera otro.</p>}
                    <button
                      type="button"
                      disabled={ocupado}
                      className={BOTON_PRINCIPAL}
                      onClick={() =>
                        void hacer(async () => {
                          const nuevo = await pedirCodigo(caseId);
                          setCodigo(nuevo);
                          setBase(dispositivos?.length ?? 0);
                          setFin(Date.now() + nuevo.segundos * 1000);
                          setRestante(nuevo.segundos);
                          return null;
                        })
                      }
                    >
                      {codigo !== null ? "Generar otro código" : "Generar mi código"}
                    </button>
                  </div>
                )}
              </li>
            </ol>
          )}

          {dispositivos !== null && dispositivos.length > 0 && (
            <section aria-labelledby={`${idTitulo}-chats`} className="space-y-2">
              <h3 id={`${idTitulo}-chats`} className="flex items-center gap-2 font-semibold text-ink">
                <Check size={15} aria-hidden className="text-cited-fg" /> Chats vinculados
              </h3>
              <ul className="space-y-2">
                {dispositivos.map((d) => (
                  <Dispositivo
                    key={d.id}
                    dispositivo={d}
                    ocupado={ocupado}
                    onPausa={(pausado) =>
                      void hacer(async () => {
                        await pausarAvisos(d.id, pausado);
                        return pausado ? "Avisos en pausa en ese chat." : "Avisos reanudados en ese chat.";
                      })
                    }
                    onDesvincular={() =>
                      void hacer(async () => {
                        await desvincular(d.id);
                        return "Chat desvinculado. Le enviaré un último mensaje de despedida.";
                      })
                    }
                  />
                ))}
              </ul>
              <p className="text-[12.5px] text-muted">
                En el chat, escribe /ayuda para ver lo que puedo hacer, /pausa para parar los avisos o /desemparejar para desvincularlo.
              </p>
            </section>
          )}

          {(aviso || error) && (
            <p role={error ? "alert" : "status"} className={error ? "rounded-lg bg-risk-high-bg px-3 py-2 text-risk-high-fg" : "text-ink"}>
              {error ?? aviso}
            </p>
          )}

          <section className="space-y-2 border-t border-line pt-4" aria-labelledby={`${idTitulo}-privacidad`}>
            <h3 id={`${idTitulo}-privacidad`} className="flex items-center gap-2 font-semibold text-ink">
              <ShieldCheck size={15} aria-hidden /> Tu privacidad
            </h3>
            <ul className="list-disc space-y-1 pl-5">
              <li>El chat va cifrado de extremo a extremo entre tu móvil y el servidor del asistente.</li>
              <li>
                El servidor guarda qué chat está vinculado (nunca tu número) y, como en la app, tus preguntas y respuestas en tu
                expediente y en tu memoria, que puedes ver, exportar y borrar.
              </li>
              <li>Lo que reenvíes o cites lo leo como datos de otra persona, nunca como palabras tuyas.</li>
              <li>Nada se confirma con un «sí»: las acciones y el consejo piden un código de un solo uso.</li>
              <li>Los documentos que envíes solo se leen si diste permiso, y se borran después. Las notas de voz aún no las escucho.</li>
              <li>Por ahí te aviso solo de lo urgente, del repaso semanal y de los dictámenes del consejo, respetando las horas de silencio.</li>
            </ul>
          </section>
        </div>
      </aside>
    </div>
  );
}
