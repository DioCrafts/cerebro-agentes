// «Avisos en el móvil»: receive the advisor's notices on this device (Web Push), opt-in.
//
// What the user agrees to is spelled out before the switch: the notification only says «Tienes un aviso…:
// abre la app» — no figure, no date, nothing about them — and it travels through their browser's push service
// (Google, Apple, Mozilla or Microsoft). The details stay in the app, behind their session.
//
// Shown in a side sheet (registro.ts: presentacion "dialogo"); `open` only triggers reading the state again.

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../api";
import { getPushConfig, pruebaPush, type PushConfig } from "../apiPush";
import { activarPush, desactivarPush, PushNoDisponibleError, retirarPush, suscripcionActual } from "../lib/push";
import { admitePush, enModoApp, esIOS, instalar, usePwa } from "../lib/pwa";
import { Button, Caja, CargandoTexto, Checkbox, Flash, FormControl } from "../ui";
import { BellIcon, CheckCircleIcon, DownloadIcon } from "../ui/iconos";
import { InstruccionesIOS } from "./AvisoInstalar";
import { FilaPeligro, ZonaPeligro } from "./ZonaPeligro";
import { useConfirmar } from "./useConfirmar";

interface AvisosMovilPanelProps {
  open: boolean;
}

function mensaje(err: unknown): string {
  if (err instanceof ApiError || err instanceof PushNoDisponibleError) return err.message;
  if (err instanceof DOMException && err.name === "NotAllowedError") return "El navegador no ha permitido las notificaciones.";
  return "No se ha podido completar. Inténtalo de nuevo.";
}

export function AvisosMovilPanel({ open }: AvisosMovilPanelProps) {
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
  const confirmar = useConfirmar();

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

  async function retirar() {
    const si = await confirmar(
      "¿Retirar tu consentimiento?",
      "Ningún dispositivo tuyo recibirá avisos. Seguirás viéndolos dentro de la app, en «Avisos».",
      "Retirar el consentimiento",
    );
    if (si)
      await hacer(async () => {
        await retirarPush();
        return "Consentimiento retirado: ningún dispositivo recibirá avisos.";
      });
  }

  const ios = esIOS();
  // iOS/iPadOS: Web Push exists only for an app opened from the home screen (16.4+), never in a Safari tab.
  const puede = admitePush() && !(ios && !enModoApp());
  const permisoDenegado = puede && Notification.permission === "denied";
  const activo = esteDispositivo && (config?.consentimiento ?? false);

  return (
    <div className="flex flex-col gap-4">
      {!enModoApp() && (ios || instalable) && (
        <Caja as="section" aria-label="Instala la aplicación">
          <Caja.Cabecera
            titulo={
              <span className="inline-flex items-center gap-2">
                <DownloadIcon />
                Instala la aplicación
              </span>
            }
          />
          <Caja.Cuerpo className="flex flex-col gap-3">
            <p className="m-0">{ios ? <InstruccionesIOS /> : "Se abre como una aplicación, sin la barra del navegador."}</p>
            {!ios && instalable && (
              <div>
                <Button onClick={() => void instalar()}>Instalar</Button>
              </div>
            )}
          </Caja.Cuerpo>
        </Caja>
      )}

      {errorConfig && <Flash variant="danger">{errorConfig}</Flash>}

      {!puede ? (
        <Flash variant="warning">
          {ios && !enModoApp()
            ? "Para recibir avisos en el iPhone o el iPad, primero añade la app a la pantalla de inicio y ábrela desde allí."
            : "Este navegador no puede recibir avisos aquí. Prueba con Chrome, Edge, Firefox o Safari, y con una conexión segura (https)."}
        </Flash>
      ) : config && !config.disponible ? (
        <Flash>Este servicio no tiene activados los avisos en el móvil. Seguirás viéndolos en «Avisos» dentro de la app.</Flash>
      ) : permisoDenegado ? (
        <Flash variant="warning">
          Has bloqueado las notificaciones de esta web en el navegador. Para recibir avisos, permítelas en los ajustes del sitio y vuelve
          aquí.
        </Flash>
      ) : config === null ? (
        !errorConfig && <CargandoTexto lineas={2} etiqueta="Comprobando…" />
      ) : activo ? (
        <Caja as="section" aria-label="Avisos en este dispositivo">
          <Caja.Cabecera
            titulo={
              <span className="inline-flex items-center gap-2 text-fg-success">
                <CheckCircleIcon />
                Activados en este dispositivo
              </span>
            }
          />
          <Caja.Cuerpo className="flex flex-col gap-3">
            <p className="m-0">
              Recibirás «Tienes un aviso urgente: abre la app» o «Tienes un aviso nuevo: abre la app». Nunca cifras, fechas ni datos tuyos:
              los detalles están dentro de la app.
              {config.dispositivos > 1 && ` Tienes los avisos activados en ${config.dispositivos} dispositivos.`}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={ocupado}
                onClick={() =>
                  void hacer(async () => {
                    const r = await pruebaPush();
                    return r.enviadas > 0 ? "Prueba enviada. Debería llegarte en unos segundos." : "No se ha podido entregar la prueba.";
                  })
                }
              >
                Enviar una prueba
              </Button>
              <Button
                disabled={ocupado}
                onClick={() =>
                  void hacer(async () => {
                    await desactivarPush();
                    return "Desactivados en este dispositivo.";
                  })
                }
              >
                Desactivar en este dispositivo
              </Button>
            </div>
          </Caja.Cuerpo>
        </Caja>
      ) : (
        <Caja as="section" aria-label="Activar los avisos en este dispositivo">
          <Caja.Cuerpo className="flex flex-col gap-3">
            <FormControl>
              <Checkbox checked={acepto} onChange={(e) => setAcepto(e.target.checked)} />
              <FormControl.Label>Acepto recibir avisos en este dispositivo</FormControl.Label>
              <FormControl.Caption>
                El aviso solo dirá «Tienes un aviso urgente: abre la app» (o «un aviso nuevo»), sin cifras ni datos míos, y pasará por el
                servicio de notificaciones de mi navegador (Google, Apple, Mozilla o Microsoft). Puedo retirarlo cuando quiera.
              </FormControl.Caption>
            </FormControl>
            <div>
              <Button
                variant="primary"
                leadingVisual={BellIcon}
                disabled={!acepto || ocupado || !config.clave_publica}
                loading={ocupado}
                onClick={() =>
                  void hacer(async () => {
                    await activarPush(config.clave_publica ?? "");
                    return "Avisos activados en este dispositivo.";
                  })
                }
              >
                Activar en este dispositivo
              </Button>
            </div>
          </Caja.Cuerpo>
        </Caja>
      )}

      {error && <Flash variant="danger">{error}</Flash>}
      {aviso && !error && (
        <Flash variant="success" role="status">
          {aviso}
        </Flash>
      )}

      {config?.consentimiento && (
        <ZonaPeligro>
          <FilaPeligro
            titulo="Retirar mi consentimiento"
            descripcion="En todos tus dispositivos: ninguno volverá a recibir avisos hasta que lo aceptes de nuevo."
            accion={
              <Button variant="danger" disabled={ocupado} onClick={() => void retirar()}>
                Retirar
              </Button>
            }
          />
        </ZonaPeligro>
      )}
    </div>
  );
}
