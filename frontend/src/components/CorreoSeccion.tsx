// «Tu Gmail» inside the mail module's section of «Módulos»: connect/disconnect Gmail (read-only), sync, the
// week's important mail, phishing warnings with their reasons, and the data the mail proposes to other
// modules, which nothing writes until the user presses «Confirmar» here.
//
// Text that comes from e-mails (subjects, snippets) is rendered as plain text by React, never as HTML.
// Connecting goes only to Google's consent screen: any other address the server returns is refused.

import { useCallback, useEffect, useState } from "react";

import { ApiError, getCase } from "../api";
import type { CaseState } from "../api";
import type { ElementoCorreo, EstadoCorreo, PropuestaCorreo, ResumenCorreo } from "../apiCorreo";
import {
  decidirPropuesta,
  desconectarCorreo,
  getEstadoCorreo,
  getPropuestasCorreo,
  getResumenCorreo,
  iniciarConexion,
  silenciar,
  sincronizarCorreo,
} from "../apiCorreo";
import { recordarCasoConectando } from "../lib/correo";
import { esConsentimientoGoogle } from "../lib/urls";
import { ActionList, ActionMenu, Button, Caja, CargandoTexto, CounterLabel, Flash, IconButton, Label, SegmentedControl } from "../ui";
import { AlertIcon, BellSlashIcon, CheckIcon, LinkIcon, MailIcon, SyncIcon, XIcon } from "../ui/iconos";
import { DialogoEleccion, FilaPeligro, ZonaPeligro } from "./ZonaPeligro";

const DESTINOS: Record<string, string> = {
  caducidades: "Caducidades y garantías",
  luz: "Luz",
  suscripciones: "Suscripciones",
  fiscal: "Impuestos",
  ayudas: "Ayudas",
};

const MENSAJES_RETORNO: Record<string, string> = {
  conectado: "Gmail conectado en solo lectura. Pulsa «Sincronizar ahora» para leer el correo reciente.",
  cancelado: "Has cancelado la conexión con Gmail. No se ha guardado nada.",
  error: "No se pudo conectar Gmail. Vuelve a intentarlo.",
};

function message(err: unknown): string {
  return err instanceof ApiError ? err.message : "No hay conexión con el servicio.";
}

function fecha(iso?: string | null): string {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}

function eur(valor: string): string {
  const n = Number(valor);
  return Number.isFinite(n) ? n.toLocaleString("es-ES", { style: "currency", currency: "EUR" }) : valor;
}

function valorCampo(campo: string, valor: string): string {
  if (campo === "importe") return eur(valor);
  if (campo.startsWith("fecha") || campo.startsWith("periodo") || campo === "plazo") return fecha(valor);
  if (campo === "potencia_kw") return `${valor.replace(".", ",")} kW`;
  return valor;
}

interface Datos {
  estado: EstadoCorreo;
  resumen: ResumenCorreo | null;
  propuestas: PropuestaCorreo[];
}

async function leer(caseId: string, periodo: "hoy" | "semana", signal?: AbortSignal): Promise<Datos> {
  const estado = await getEstadoCorreo(caseId, signal);
  if (estado.elementos === 0 && !estado.conectado) return { estado, resumen: null, propuestas: [] };
  const [resumen, propuestas] = await Promise.all([getResumenCorreo(caseId, periodo, signal), getPropuestasCorreo(caseId, signal)]);
  return { estado, resumen, propuestas };
}

/** Reads `?correo=` (set by the API after Google's consent screen) once, and removes it from the URL. */
function useRetornoDeGoogle(): string | null {
  const [texto] = useState(() => {
    try {
      const valor = new URLSearchParams(window.location.search).get("correo");
      if (!valor) return null;
      const url = new URL(window.location.href);
      url.searchParams.delete("correo");
      window.history.replaceState(null, "", url.toString());
      return MENSAJES_RETORNO[valor] ?? null;
    } catch {
      return null;
    }
  });
  return texto;
}

interface CorreoSeccionProps {
  caseId: string;
  onCaseUpdated?: (c: CaseState) => void;
  disabled: boolean;
  /** The page is on screen: the mail state is read again each time it becomes so. */
  activa?: boolean;
}

export function CorreoSeccion({ caseId, onCaseUpdated, disabled, activa = true }: CorreoSeccionProps) {
  const retorno = useRetornoDeGoogle();
  const [estado, setEstado] = useState<EstadoCorreo | null>(null);
  const [resumen, setResumen] = useState<ResumenCorreo | null>(null);
  const [propuestas, setPropuestas] = useState<PropuestaCorreo[]>([]);
  const [periodo, setPeriodo] = useState<"hoy" | "semana">("semana");
  const [busy, setBusy] = useState(false);
  const [aviso, setAviso] = useState<string | null>(retorno);
  const [error, setError] = useState<string | null>(null);
  const [desconectando, setDesconectando] = useState(false);

  const aplicar = useCallback((d: Datos) => {
    setEstado(d.estado);
    setResumen(d.resumen);
    setPropuestas(d.propuestas);
  }, []);

  const cargar = useCallback(async () => aplicar(await leer(caseId, periodo)), [aplicar, caseId, periodo]);

  useEffect(() => {
    if (!activa) return;
    const ctrl = new AbortController();
    leer(caseId, periodo, ctrl.signal).then(aplicar, (err: unknown) => {
      if (!ctrl.signal.aborted) setError(message(err));
    });
    return () => ctrl.abort();
  }, [aplicar, caseId, periodo, activa]);

  async function accion(fn: () => Promise<unknown>, exito?: string) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      if (exito) setAviso(exito);
      await cargar();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  async function conectar() {
    setBusy(true);
    setError(null);
    try {
      const url = await iniciarConexion(caseId);
      // Only ever to Google's consent screen: a different address from the server is refused.
      if (!esConsentimientoGoogle(url)) {
        setError("El servidor ha devuelto una dirección de conexión inesperada. No se ha abierto.");
        setBusy(false);
        return;
      }
      recordarCasoConectando(caseId);
      window.location.assign(url);
    } catch (err) {
      setError(message(err));
      setBusy(false);
    }
  }

  function desconectar(borrar: boolean) {
    setDesconectando(false);
    void accion(
      () => desconectarCorreo(caseId, borrar),
      borrar ? "Gmail desconectado y datos del correo borrados." : "Gmail desconectado.",
    );
  }

  const pendientes = propuestas.filter((p) => p.estado === "pendiente");
  const off = busy || disabled;

  return (
    <div className="flex flex-col gap-4">
      <div aria-live="polite" className="flex flex-col gap-2 empty:hidden">
        {aviso && (
          <Flash variant="success" className="flex items-start gap-2">
            <span className="flex-1">{aviso}</span>
            <IconButton icon={XIcon} aria-label="Cerrar el aviso" variant="invisible" size="small" onClick={() => setAviso(null)} />
          </Flash>
        )}
        {error && <Flash variant="danger">{error}</Flash>}
      </div>

      <Caja as="section" aria-label="Tu Gmail">
        <Caja.Cabecera
          titulo={
            <span className="inline-flex items-center gap-2">
              <MailIcon />
              Tu Gmail
            </span>
          }
          nivel={4}
          acciones={estado?.conectado ? <Label variant={estado.estado === "reconectar" ? "attention" : "success"}>{estado.estado === "reconectar" ? "Hay que reconectar" : "Conectado"}</Label> : undefined}
        />
        <Caja.Cuerpo className="flex flex-col gap-3">
          {!estado ? (
            <CargandoTexto lineas={2} etiqueta="Cargando el estado de Gmail" />
          ) : !estado.configurado && !estado.conectado ? (
            <p className="m-0 text-fg-muted">
              El servicio todavía no tiene configurada la conexión con Gmail. Lo tiene que activar quien administra el servicio.
            </p>
          ) : !estado.conectado ? (
            <>
              <p className="m-0">
                Conecta tu Gmail en <strong>solo lectura</strong>: el asistente nunca envía, borra ni mueve correos. Se leen los últimos{" "}
                {estado.ventana_dias} días y después solo el correo nuevo. Del correo solo se guardan datos derivados (fecha, dominio del
                remitente, categoría, importes y fechas con su frase); el texto completo no se guarda.
              </p>
              <div>
                <Button variant="primary" leadingVisual={LinkIcon} disabled={off} loading={busy} onClick={() => void conectar()}>
                  Conectar Gmail
                </Button>
              </div>
            </>
          ) : (
            <>
              {estado.estado === "reconectar" && (
                <Flash variant="warning">Google ya no acepta el permiso (lo retiraste o caducó). Vuelve a conectar Gmail para leer el correo nuevo.</Flash>
              )}
              <p className="m-0">
                Conectado{estado.cuenta ? ` como ${estado.cuenta}` : ""} · solo lectura
                {estado.ultima_sincronizacion
                  ? ` · última lectura ${new Date(estado.ultima_sincronizacion).toLocaleString("es-ES")}`
                  : " · aún sin leer"}
              </p>
              {estado.ultimo_error && <p className="m-0 text-fg-danger">Último error: {estado.ultimo_error}</p>}
              <div>
                {estado.estado === "reconectar" ? (
                  <Button variant="primary" leadingVisual={LinkIcon} disabled={off} loading={busy} onClick={() => void conectar()}>
                    Volver a conectar
                  </Button>
                ) : (
                  <Button
                    leadingVisual={SyncIcon}
                    disabled={off}
                    loading={busy}
                    onClick={() =>
                      void accion(async () => {
                        const r = await sincronizarCorreo(caseId);
                        setAviso(r.nuevos ? `${r.nuevos} correo(s) nuevo(s) clasificados.` : "No hay correo nuevo.");
                      })
                    }
                  >
                    Sincronizar ahora
                  </Button>
                )}
              </div>
            </>
          )}
        </Caja.Cuerpo>
      </Caja>

      {pendientes.length > 0 && (
        <Caja as="section" variante="acento" aria-label="Datos del correo para tus módulos">
          <Caja.Cabecera titulo="Datos del correo para tus módulos" nivel={4} acciones={<CounterLabel>{pendientes.length}</CounterLabel>} />
          <Caja.Cuerpo className="text-fg-muted">No se guarda nada hasta que lo confirmes.</Caja.Cuerpo>
          <Caja.Lista>
            {pendientes.map((p) => (
              <PropuestaItem
                key={p.id}
                p={p}
                disabled={off}
                onDecide={(ok) =>
                  accion(
                    async () => {
                      await decidirPropuesta(p.id, ok);
                      // The destination module's data changed: refresh the case the panel shows.
                      if (ok && onCaseUpdated) onCaseUpdated(await getCase({ caseId }));
                    },
                    ok ? "Guardado." : "Descartado.",
                  )
                }
              />
            ))}
          </Caja.Lista>
        </Caja>
      )}

      {resumen && (
        <Caja as="section" aria-label="Lo importante del correo">
          <Caja.Cabecera
            titulo="Lo importante"
            nivel={4}
            acciones={
              <SegmentedControl aria-label="Periodo del resumen" size="small" onChange={(i) => setPeriodo(i === 0 ? "hoy" : "semana")}>
                <SegmentedControl.Button selected={periodo === "hoy"}>Hoy</SegmentedControl.Button>
                <SegmentedControl.Button selected={periodo === "semana"}>Esta semana</SegmentedControl.Button>
              </SegmentedControl>
            }
          />
          {resumen.secciones.every((s) => s.elementos.length === 0) ? (
            <Caja.Cuerpo className="text-fg-muted">Nada importante en este periodo.</Caja.Cuerpo>
          ) : (
            resumen.secciones
              .filter((s) => s.elementos.length > 0)
              .map((s) => (
                <div key={s.clave}>
                  <h5 className="m-0 border-t border-border-muted bg-canvas-muted px-4 py-2 text-xs font-semibold first:border-t-0">
                    {s.clave === "phishing" ? <span className="text-fg-danger">{s.titulo}</span> : s.titulo}
                  </h5>
                  <Caja.Lista>
                    {s.elementos.map((e) => (
                      <ElementoItem
                        key={`${s.clave}-${e.id}`}
                        e={e}
                        disabled={off}
                        onSilenciar={(tipo, valor) => accion(() => silenciar(caseId, tipo, valor), "Anotado: no te lo volveré a mostrar.")}
                      />
                    ))}
                  </Caja.Lista>
                </div>
              ))
          )}
          {(Object.keys(resumen.otros).length > 0 || resumen.silenciados > 0) && (
            <Caja.Pie className="text-xs text-fg-muted">
              {Object.values(resumen.otros).reduce((a, b) => a + b, 0)} correo(s) sin importancia
              {resumen.silenciados ? ` · ${resumen.silenciados} que dijiste que no te interesan` : ""}.
            </Caja.Pie>
          )}
        </Caja>
      )}

      {estado?.conectado && (
        <ZonaPeligro>
          <FilaPeligro
            titulo="Desconectar Gmail"
            descripcion="Se retira el permiso en Google y se borra de aquí. Puedes conservar o borrar lo que ya se clasificó."
            accion={
              <Button variant="danger" disabled={off} onClick={() => setDesconectando(true)}>
                Desconectar
              </Button>
            }
          />
        </ZonaPeligro>
      )}

      {desconectando && (
        <DialogoEleccion
          titulo="Desconectar Gmail"
          onCerrar={() => setDesconectando(false)}
          botones={[
            { contenido: "Desconectar y conservar", onClick: () => desconectar(false), disabled: off },
            { contenido: "Desconectar y borrar", tipo: "danger", onClick: () => desconectar(true), disabled: off },
          ]}
        >
          Se retirará el permiso en Google y se borrará de aquí. ¿Borrar también lo clasificado (importes, fechas y avisos que salieron
          del correo)?
        </DialogoEleccion>
      )}
    </div>
  );
}

function PropuestaItem({ p, disabled, onDecide }: { p: PropuestaCorreo; disabled: boolean; onDecide: (ok: boolean) => Promise<void> }) {
  return (
    <Caja.Fila className="flex flex-col gap-1">
      <p className="m-0 font-semibold">
        {DESTINOS[p.destino] ?? p.destino}: {p.titulo}
      </p>
      <p className="m-0 text-xs text-fg-muted">
        Correo de {p.origen.dominio} del {fecha(p.origen.fecha)}
        {p.origen.asunto ? ` · «${p.origen.asunto}»` : ""}
      </p>
      <p className="m-0">{p.explicacion}</p>
      {p.evidencias.length > 0 && (
        <ul className="m-0 list-disc pl-5 text-xs text-fg-muted">
          {p.evidencias.map((c) => (
            <li key={c.campo}>
              {c.campo.replace(/_/g, " ")}: {valorCampo(c.campo, c.valor)} — «{c.fragmento}»
            </li>
          ))}
        </ul>
      )}
      {p.resultado && <p className="m-0 text-fg-danger">{p.resultado}</p>}
      <div className="mt-1 flex flex-wrap gap-2">
        <Button size="small" variant="primary" leadingVisual={CheckIcon} disabled={disabled} onClick={() => void onDecide(true)} aria-label={`Confirmar: ${p.titulo}`}>
          Confirmar
        </Button>
        <Button size="small" leadingVisual={XIcon} disabled={disabled} onClick={() => void onDecide(false)} aria-label={`Rechazar: ${p.titulo}`}>
          Rechazar
        </Button>
      </div>
    </Caja.Fila>
  );
}

function ElementoItem({
  e,
  disabled,
  onSilenciar,
}: {
  e: ElementoCorreo;
  disabled: boolean;
  onSilenciar: (tipo: "categoria" | "remitente", valor: string) => Promise<void>;
}) {
  const fraude = e.categoria === "phishing";
  const campos = e.campos.filter((c) => c.campo !== "plazo");
  return (
    <Caja.Fila className="flex flex-col gap-1">
      <p className="m-0 flex flex-wrap items-center gap-2">
        {fraude && (
          <Label variant="danger">
            <AlertIcon size={12} className="mr-1" />
            Posible fraude
          </Label>
        )}
        <span>
          <span className="font-semibold">{e.organismo ?? e.comercio ?? e.dominio}</span>
          {e.organismo && !e.organismo_verificado ? " (remitente no verificado)" : ""} · {fecha(e.fecha)}
          {e.plazo ? <strong> · plazo {fecha(e.plazo)}</strong> : null}
        </span>
      </p>
      {e.asunto && <p className="m-0 text-fg-muted">«{e.asunto}»</p>}
      {campos.length > 0 && <p className="m-0">{campos.map((c) => `${c.campo.replace(/_/g, " ")}: ${valorCampo(c.campo, c.valor)}`).join(" · ")}</p>}
      {fraude && e.motivos.length > 0 && (
        <ul className="m-0 list-disc pl-5 text-xs">
          {e.motivos.map((m) => (
            <li key={m}>{m}</li>
          ))}
          <li>No abras sus enlaces ni adjuntos. No se ha borrado ni movido nada.</li>
        </ul>
      )}
      <div>
        <ActionMenu>
          <ActionMenu.Button size="small" variant="invisible" leadingVisual={BellSlashIcon} disabled={disabled}>
            No me interesa
          </ActionMenu.Button>
          <ActionMenu.Overlay>
            <ActionList>
              <ActionList.Item onSelect={() => void onSilenciar("categoria", e.categoria)}>Nada de «{e.etiqueta}»</ActionList.Item>
              {!fraude && e.dominio && <ActionList.Item onSelect={() => void onSilenciar("remitente", e.dominio)}>Nada de {e.dominio}</ActionList.Item>}
            </ActionList>
          </ActionMenu.Overlay>
        </ActionMenu>
      </div>
    </Caja.Fila>
  );
}
