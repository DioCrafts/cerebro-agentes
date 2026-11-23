/* ui-legado: pendiente de migrar a src/ui (docs/ui/sistema-de-diseno.md) */
// «Correo (Gmail)» inside the module's card in «Módulos»: connect/disconnect Gmail (read-only), sync, the
// week's important mail, phishing warnings with their reasons, and the data the mail proposes to other
// modules, which nothing writes until the user presses «Confirmar» here.
//
// Text that comes from e-mails (subjects, snippets) is rendered as plain text by React, never as HTML.

import { AlertTriangle, BellOff, Check, Link2, Link2Off, Mail, RefreshCw, X } from "lucide-react";
import clsx from "clsx";
import { useCallback, useEffect, useId, useState } from "react";

import { ApiError, getCase } from "../api";
import type { CaseState } from "../api";
import type { ElementoCorreo, EstadoCorreo, PropuestaCorreo, ResumenCorreo } from "../apiCorreo";
import { recordarCasoConectando } from "../lib/correo";
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
import { esConsentimientoGoogle } from "../lib/urls";

const BUTTON_PRIMARY =
  "inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[13px] font-semibold text-white transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50";
const BUTTON_QUIET =
  "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[13px] font-semibold text-ink-soft hover:bg-line/60 disabled:cursor-not-allowed disabled:opacity-50";

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

export function CorreoSeccion({ caseId, onCaseUpdated, disabled }: { caseId: string; onCaseUpdated?: (c: CaseState) => void; disabled: boolean }) {
  const titleId = useId();
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
    const ctrl = new AbortController();
    leer(caseId, periodo, ctrl.signal).then(aplicar, (err: unknown) => {
      if (!ctrl.signal.aborted) setError(message(err));
    });
    return () => ctrl.abort();
  }, [aplicar, caseId, periodo]);

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

  const pendientes = propuestas.filter((p) => p.estado === "pendiente");
  const off = busy || disabled;

  return (
    <section aria-labelledby={titleId} className="mt-3 space-y-3 rounded-lg border border-line bg-paper px-3 py-3">
      <h4 id={titleId} className="flex items-center gap-1.5 text-[14px] font-bold text-ink">
        <Mail size={15} aria-hidden className="text-primary-dark" />
        Tu Gmail
      </h4>
      <div aria-live="polite" className="space-y-2">
        {aviso && (
          <p className="flex items-start gap-1.5 rounded-lg bg-risk-low-bg px-2.5 py-1.5 text-[12.5px] text-risk-low-fg">
            <span className="flex-1">{aviso}</span>
            <button type="button" aria-label="Cerrar el aviso" className="shrink-0" onClick={() => setAviso(null)}>
              <X size={13} aria-hidden />
            </button>
          </p>
        )}
        {error && (
          <p role="alert" className="rounded-lg border border-risk-high-fg/30 bg-risk-high-bg px-2.5 py-1.5 text-[12.5px] text-ink-soft">
            {error}
          </p>
        )}
      </div>

      {!estado ? (
        <p className="text-[12.5px] text-muted">Cargando…</p>
      ) : !estado.configurado && !estado.conectado ? (
        <p className="text-[12.5px] text-muted">
          El servicio todavía no tiene configurada la conexión con Gmail. Lo tiene que activar quien administra el servicio.
        </p>
      ) : !estado.conectado ? (
        <div className="space-y-2 text-[12.5px] leading-snug text-ink-soft">
          <p>
            Conecta tu Gmail en <strong>solo lectura</strong>: el asistente nunca envía, borra ni mueve correos. Se leen los últimos{" "}
            {estado.ventana_dias} días y después solo el correo nuevo. Del correo solo se guardan datos derivados (fecha, dominio del
            remitente, categoría, importes y fechas con su frase); el texto completo no se guarda.
          </p>
          <button type="button" className={BUTTON_PRIMARY} disabled={off} onClick={() => void conectar()}>
            <Link2 size={14} aria-hidden />
            Conectar Gmail
          </button>
        </div>
      ) : (
        <div className="space-y-2 text-[12.5px] text-ink-soft">
          {estado.estado === "reconectar" && (
            <p role="alert" className="rounded-lg bg-risk-medium-bg px-2.5 py-1.5 text-risk-medium-fg">
              Google ya no acepta el permiso (lo retiraste o caducó). Vuelve a conectar Gmail para leer el correo nuevo.
            </p>
          )}
          <p>
            Conectado{estado.cuenta ? ` como ${estado.cuenta}` : ""} · solo lectura
            {estado.ultima_sincronizacion
              ? ` · última lectura ${new Date(estado.ultima_sincronizacion).toLocaleString("es-ES")}`
              : " · aún sin leer"}
          </p>
          {estado.ultimo_error && <p className="text-risk-high-fg">Último error: {estado.ultimo_error}</p>}
          <div className="flex flex-wrap gap-2">
            {estado.estado === "reconectar" ? (
              <button type="button" className={BUTTON_PRIMARY} disabled={off} onClick={() => void conectar()}>
                <Link2 size={14} aria-hidden />
                Volver a conectar
              </button>
            ) : (
              <button
                type="button"
                className={BUTTON_PRIMARY}
                disabled={off}
                onClick={() =>
                  void accion(async () => {
                    const r = await sincronizarCorreo(caseId);
                    setAviso(r.nuevos ? `${r.nuevos} correo(s) nuevo(s) clasificados.` : "No hay correo nuevo.");
                  })
                }
              >
                <RefreshCw size={14} aria-hidden className={clsx(busy && "animate-spin")} />
                Sincronizar ahora
              </button>
            )}
            <button type="button" className={BUTTON_QUIET} disabled={off} onClick={() => setDesconectando(true)}>
              <Link2Off size={14} aria-hidden />
              Desconectar
            </button>
          </div>
          {desconectando && (
            <div role="group" aria-label="Desconectar Gmail" className="rounded-lg border border-line px-2.5 py-2">
              <p className="text-ink">Se retirará el permiso en Google y se borrará de aquí. ¿Borrar también lo clasificado?</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  className={BUTTON_PRIMARY}
                  disabled={off}
                  onClick={() => void accion(() => desconectarCorreo(caseId, false), "Gmail desconectado.").then(() => setDesconectando(false))}
                >
                  Desconectar y conservar
                </button>
                <button
                  type="button"
                  className={clsx(BUTTON_QUIET, "text-risk-high-fg")}
                  disabled={off}
                  onClick={() =>
                    void accion(() => desconectarCorreo(caseId, true), "Gmail desconectado y datos del correo borrados.").then(() =>
                      setDesconectando(false),
                    )
                  }
                >
                  Desconectar y borrar
                </button>
                <button type="button" className={BUTTON_QUIET} onClick={() => setDesconectando(false)}>
                  Cancelar
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {pendientes.length > 0 && (
        <div>
          <h5 className="text-[13px] font-bold text-ink">Datos del correo para tus módulos ({pendientes.length})</h5>
          <p className="text-[12px] text-muted">No se guarda nada hasta que lo confirmes.</p>
          <ul className="mt-1.5 space-y-2">
            {pendientes.map((p) => (
              <PropuestaItem key={p.id} p={p} disabled={off} onDecide={(ok) =>
                  accion(async () => {
                    await decidirPropuesta(p.id, ok);
                    // The destination module's data changed: refresh the case the panel shows.
                    if (ok && onCaseUpdated) onCaseUpdated(await getCase({ caseId }));
                  }, ok ? "Guardado." : "Descartado.")
                } />
            ))}
          </ul>
        </div>
      )}

      {resumen && (
        <div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h5 className="text-[13px] font-bold text-ink">Lo importante</h5>
            <div role="radiogroup" aria-label="Periodo del resumen" className="flex gap-1">
              {(["hoy", "semana"] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  role="radio"
                  aria-checked={periodo === v}
                  onClick={() => setPeriodo(v)}
                  className={clsx("rounded-pill px-2.5 py-0.5 text-[12px] font-semibold", periodo === v ? "bg-primary text-white" : "bg-line/60 text-ink-soft")}
                >
                  {v === "hoy" ? "Hoy" : "Esta semana"}
                </button>
              ))}
            </div>
          </div>
          {resumen.secciones.every((s) => s.elementos.length === 0) ? (
            <p className="mt-1 text-[12.5px] text-muted">Nada importante en este periodo.</p>
          ) : (
            resumen.secciones
              .filter((s) => s.elementos.length > 0)
              .map((s) => (
                <div key={s.clave} className="mt-2">
                  <h6 className={clsx("text-[12.5px] font-semibold", s.clave === "phishing" ? "text-risk-high-fg" : "text-ink")}>{s.titulo}</h6>
                  <ul className="mt-1 space-y-1.5">
                    {s.elementos.map((e) => (
                      <ElementoItem
                        key={`${s.clave}-${e.id}`}
                        e={e}
                        disabled={off}
                        onSilenciar={(tipo, valor) => accion(() => silenciar(caseId, tipo, valor), "Anotado: no te lo volveré a mostrar.")}
                      />
                    ))}
                  </ul>
                </div>
              ))
          )}
          {(Object.keys(resumen.otros).length > 0 || resumen.silenciados > 0) && (
            <p className="mt-2 text-[12px] text-muted">
              {Object.values(resumen.otros).reduce((a, b) => a + b, 0)} correo(s) sin importancia
              {resumen.silenciados ? ` · ${resumen.silenciados} que dijiste que no te interesan` : ""}.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function PropuestaItem({ p, disabled, onDecide }: { p: PropuestaCorreo; disabled: boolean; onDecide: (ok: boolean) => Promise<void> }) {
  return (
    <li className="rounded-lg border border-primary/40 bg-surface px-2.5 py-2 text-[12.5px] leading-snug">
      <p className="font-semibold text-ink">
        {DESTINOS[p.destino] ?? p.destino}: {p.titulo}
      </p>
      <p className="text-muted">
        Correo de {p.origen.dominio} del {fecha(p.origen.fecha)}
        {p.origen.asunto ? ` · «${p.origen.asunto}»` : ""}
      </p>
      <p className="mt-0.5 text-ink-soft">{p.explicacion}</p>
      {p.evidencias.length > 0 && (
        <ul className="mt-1 list-disc pl-4 text-[12px] text-muted">
          {p.evidencias.map((c) => (
            <li key={c.campo}>
              {c.campo.replace(/_/g, " ")}: {valorCampo(c.campo, c.valor)} — «{c.fragmento}»
            </li>
          ))}
        </ul>
      )}
      {p.resultado && <p className="mt-0.5 text-risk-high-fg">{p.resultado}</p>}
      <div className="mt-1.5 flex flex-wrap gap-2">
        <button type="button" className={BUTTON_PRIMARY} disabled={disabled} onClick={() => void onDecide(true)} aria-label={`Confirmar: ${p.titulo}`}>
          <Check size={14} aria-hidden />
          Confirmar
        </button>
        <button type="button" className={BUTTON_QUIET} disabled={disabled} onClick={() => void onDecide(false)} aria-label={`Rechazar: ${p.titulo}`}>
          <X size={14} aria-hidden />
          Rechazar
        </button>
      </div>
    </li>
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
  const [menu, setMenu] = useState(false);
  const fraude = e.categoria === "phishing";
  return (
    <li className={clsx("rounded-lg px-2.5 py-1.5 text-[12.5px] leading-snug", fraude ? "border border-risk-high-fg/40 bg-risk-high-bg" : "bg-surface")}>
      <p className="text-ink">
        {fraude && <AlertTriangle size={13} aria-label="Posible fraude" className="mr-1 inline text-risk-high-fg" />}
        <span className="font-semibold">{e.organismo ?? e.comercio ?? e.dominio}</span>
        {e.organismo && !e.organismo_verificado ? " (remitente no verificado)" : ""} · {fecha(e.fecha)}
        {e.plazo ? <strong> · plazo {fecha(e.plazo)}</strong> : null}
      </p>
      {e.asunto && <p className="text-muted">«{e.asunto}»</p>}
      {e.campos.filter((c) => c.campo !== "plazo").length > 0 && (
        <p className="text-ink-soft">
          {e.campos
            .filter((c) => c.campo !== "plazo")
            .map((c) => `${c.campo.replace(/_/g, " ")}: ${valorCampo(c.campo, c.valor)}`)
            .join(" · ")}
        </p>
      )}
      {fraude && e.motivos.length > 0 && (
        <ul className="mt-0.5 list-disc pl-4 text-[12px] text-ink-soft">
          {e.motivos.map((m) => (
            <li key={m}>{m}</li>
          ))}
          <li>No abras sus enlaces ni adjuntos. No se ha borrado ni movido nada.</li>
        </ul>
      )}
      <div className="mt-1">
        {!menu ? (
          <button type="button" className="inline-flex items-center gap-1 text-[12px] text-muted underline" onClick={() => setMenu(true)}>
            <BellOff size={12} aria-hidden />
            No me interesa
          </button>
        ) : (
          <div role="group" aria-label="No me interesa" className="flex flex-wrap gap-2 text-[12px]">
            <button type="button" className={BUTTON_QUIET} disabled={disabled} onClick={() => void onSilenciar("categoria", e.categoria)}>
              Nada de «{e.etiqueta}»
            </button>
            {!fraude && e.dominio && (
              <button type="button" className={BUTTON_QUIET} disabled={disabled} onClick={() => void onSilenciar("remitente", e.dominio)}>
                Nada de {e.dominio}
              </button>
            )}
            <button type="button" className={BUTTON_QUIET} onClick={() => setMenu(false)}>
              Cancelar
            </button>
          </div>
        )}
      </div>
    </li>
  );
}
