// «Avisos en el móvil»: receive the advisor's notices on this device (Web Push), opt-in.
//
// What the user agrees to is spelled out before the switch: the notification only says «Tienes un aviso…:
// abre la app» — no figure, no date, nothing about them — and it travels through their browser's push service
// (Google, Apple, Mozilla or Microsoft). The details stay in the app, behind their session.

import { BellRing, Check, Download, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../api";
import { getPushConfig, pruebaPush, type PushConfig } from "../apiPush";
import { activarPush, desactivarPush, PushNoDisponibleError, retirarPush, suscripcionActual } from "../lib/push";
import { admitePush, enModoApp, esIOS, instalar, usePwa } from "../lib/pwa";
import { InstruccionesIOS } from "./AvisoInstalar";

interface AvisosMovilPanelProps {
  open: boolean;
  onClose: () => void;
}

function mensaje(err: unknown): string {
  if (err instanceof ApiError || err instanceof PushNoDisponibleError) return err.message;
  if (err instanceof DOMException && err.name === "NotAllowedError") return "El navegador no ha permitido las notificaciones.";
  return "No se ha podido completar. Inténtalo de nuevo.";
}

export function AvisosMovilPanel({ open, onClose }: AvisosMovilPanelProps) {
  const { instalable } = usePwa();
  const [config, setConfig] = useState<PushConfig | null>(null);
  const [errorConfig, setErrorConfig] = useState<string | null>(null);
  const [esteDispositivo, setEsteDispositivo] = useState(false);
  const [acepto, setAcepto] = useState(false);
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
    getPushConfig(controller.signal).then(
      (c) => {
        setConfig(c);
        setErrorConfig(null);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setErrorConfig(mensaje(err));
      },
    );
    suscripcionActual().then(
      (sub) => setEsteDispositivo(sub !== null),
      () => setEsteDispositivo(false),
    );
    return () => controller.abort();
  }, [open, tick]);

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

  const ios = esIOS();
  // iOS/iPadOS: Web Push exists only for an app opened from the home screen (16.4+), never in a Safari tab.
  const puede = admitePush() && !(ios && !enModoApp());
  const permisoDenegado = puede && Notification.permission === "denied";
  const activo = esteDispositivo && (config?.consentimiento ?? false);

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button type="button" aria-label="Cerrar los avisos en el móvil" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-labelledby="movil-titulo" className="relative flex h-full w-full max-w-[440px] flex-col border-l border-line bg-paper shadow-pop">
        <header className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <h2 id="movil-titulo" className="font-serif text-[19px] font-bold text-ink">
              Avisos en el móvil
            </h2>
            <p className="text-[12.5px] text-muted">Te avisamos en este dispositivo cuando haya algo urgente</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Cerrar" className="rounded-lg p-2 text-muted hover:bg-line/60 hover:text-ink">
            <X size={18} />
          </button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5 text-[13.5px] leading-relaxed text-ink-soft">
          {!enModoApp() && (ios || instalable) && (
            <section className="rounded-xl border border-line bg-surface px-4 py-3">
              <h3 className="flex items-center gap-2 font-semibold text-ink">
                <Download size={15} aria-hidden /> Instala la aplicación
              </h3>
              <p className="mt-1">{ios ? <InstruccionesIOS /> : "Se abre como una aplicación, sin la barra del navegador."}</p>
              {!ios && instalable && (
                <button
                  type="button"
                  onClick={() => void instalar()}
                  className="mt-2 rounded-lg bg-primary px-3 py-1.5 text-[13px] font-semibold text-white hover:bg-primary-hover"
                >
                  Instalar
                </button>
              )}
            </section>
          )}

          {errorConfig && (
            <p role="alert" className="rounded-lg bg-risk-high-bg px-3 py-2 text-risk-high-fg">
              {errorConfig}
            </p>
          )}

          {!puede ? (
            <p>
              {ios && !enModoApp()
                ? "Para recibir avisos en el iPhone o el iPad, primero añade la app a la pantalla de inicio y ábrela desde allí."
                : "Este navegador no puede recibir avisos aquí. Prueba con Chrome, Edge, Firefox o Safari, y con una conexión segura (https)."}
            </p>
          ) : config && !config.disponible ? (
            <p>Este servicio no tiene activados los avisos en el móvil. Seguirás viéndolos en «Avisos» dentro de la app.</p>
          ) : permisoDenegado ? (
            <p>
              Has bloqueado las notificaciones de esta web en el navegador. Para recibir avisos, permítelas en los ajustes del sitio y vuelve
              aquí.
            </p>
          ) : config === null ? (
            !errorConfig && <p role="status">Comprobando…</p>
          ) : activo ? (
            <section className="space-y-3">
              <p className="flex items-center gap-2 font-semibold text-ink">
                <Check size={16} aria-hidden className="text-cited-fg" /> Activados en este dispositivo
              </p>
              <p>
                Recibirás «Tienes un aviso urgente: abre la app» o «Tienes un aviso nuevo: abre la app». Nunca cifras, fechas ni datos tuyos:
                los detalles están dentro de la app.
                {config.dispositivos > 1 && ` Tienes los avisos activados en ${config.dispositivos} dispositivos.`}
              </p>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={ocupado}
                  onClick={() =>
                    void hacer(async () => {
                      const r = await pruebaPush();
                      return r.enviadas > 0 ? "Prueba enviada. Debería llegarte en unos segundos." : "No se ha podido entregar la prueba.";
                    })
                  }
                  className="rounded-lg border border-line bg-surface px-3 py-1.5 text-[13px] font-semibold text-ink-soft hover:border-primary hover:text-primary-dark disabled:opacity-50"
                >
                  Enviar una prueba
                </button>
                <button
                  type="button"
                  disabled={ocupado}
                  onClick={() =>
                    void hacer(async () => {
                      await desactivarPush();
                      return "Desactivados en este dispositivo.";
                    })
                  }
                  className="rounded-lg border border-line bg-surface px-3 py-1.5 text-[13px] font-semibold text-ink-soft hover:border-primary hover:text-primary-dark disabled:opacity-50"
                >
                  Desactivar en este dispositivo
                </button>
              </div>
            </section>
          ) : (
            <section className="space-y-3">
              <label className="flex items-start gap-2.5 rounded-xl border border-line bg-surface px-4 py-3">
                <input type="checkbox" checked={acepto} onChange={(e) => setAcepto(e.target.checked)} className="mt-1 h-4 w-4 accent-[rgb(var(--primary))]" />
                <span>
                  Acepto recibir avisos en este dispositivo. El aviso solo dirá «Tienes un aviso urgente: abre la app» (o «un aviso nuevo»),
                  sin cifras ni datos míos, y pasará por el servicio de notificaciones de mi navegador (Google, Apple, Mozilla o Microsoft).
                  Puedo retirarlo cuando quiera.
                </span>
              </label>
              <button
                type="button"
                disabled={!acepto || ocupado || !config.clave_publica}
                onClick={() =>
                  void hacer(async () => {
                    await activarPush(config.clave_publica ?? "");
                    return "Avisos activados en este dispositivo.";
                  })
                }
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-[13.5px] font-semibold text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
              >
                <BellRing size={15} aria-hidden /> Activar en este dispositivo
              </button>
            </section>
          )}

          {config?.consentimiento && (
            <section className="border-t border-line pt-4">
              <button
                type="button"
                disabled={ocupado}
                onClick={() =>
                  void hacer(async () => {
                    await retirarPush();
                    return "Consentimiento retirado: ningún dispositivo recibirá avisos.";
                  })
                }
                className="text-[13px] font-semibold text-risk-high-fg underline-offset-2 hover:underline disabled:opacity-50"
              >
                Retirar mi consentimiento en todos mis dispositivos
              </button>
            </section>
          )}

          {(aviso || error) && (
            <p role={error ? "alert" : "status"} className={error ? "rounded-lg bg-risk-high-bg px-3 py-2 text-risk-high-fg" : "text-ink"}>
              {error ?? aviso}
            </p>
          )}
        </div>
      </aside>
    </div>
  );
}
