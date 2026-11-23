/* ui-legado: pendiente de migrar a src/ui (docs/ui/sistema-de-diseno.md) */
// «Capacidades» inside «Módulos»: the means any module can use (mail, browser, documents, research), apart from the
// modules themselves, with the user's consent to each. A capability that is also a module (mail, browser) keeps its
// one switch on its module card; here it only says so. Read when the panel opens, so a change made elsewhere shows.

import { Plug } from "lucide-react";
import clsx from "clsx";
import { useEffect, useId, useState } from "react";

import { ApiError } from "../api";
import type { CapacidadInfo, CapacidadesResponse } from "../apiModulos";
import { getCapacidades, setCapacidad } from "../apiModulos";

function message(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "No hay conexión con el servicio.";
}

/** The consent state of a capability, in words. */
function estadoCapacidad(c: CapacidadInfo, nombres: Record<string, string>): string {
  if (!c.requiere_consentimiento) return "No necesita tu permiso: solo usa lo que tú le das en cada momento.";
  if (c.modulo) {
    const modulo = nombres[c.modulo] ?? c.modulo;
    return c.activa ? `Activada con el módulo «${modulo}».` : `Desactivada: se activa con el módulo «${modulo}».`;
  }
  return c.activa ? "Activada: le has dado permiso." : "Desactivada: no la usará ningún módulo sin tu permiso.";
}

export function CapacidadesSeccion({ nombres, disabled }: { nombres: Record<string, string>; disabled: boolean }) {
  const [datos, setDatos] = useState<CapacidadesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const titulo = useId();

  useEffect(() => {
    const controller = new AbortController();
    getCapacidades(controller.signal).then(
      (resp) => {
        setDatos(resp);
        setError(null);
      },
      (err: unknown) => {
        // An older backend without capabilities: the section is simply not shown.
        if (!controller.signal.aborted && !(err instanceof ApiError && err.status === 404)) {
          setError(`No se pudieron cargar las capacidades. ${message(err)}`);
        }
      },
    );
    return () => controller.abort();
  }, []);

  if (!datos && !error) return null;
  if (datos && datos.capacidades.length === 0) return null;

  async function cambiar(c: CapacidadInfo) {
    setBusy(c.clave);
    try {
      setDatos(await setCapacidad(c.clave, !c.activa));
      setError(null);
    } catch (err) {
      setError(`No se pudo cambiar la capacidad. ${message(err)}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-labelledby={titulo} className="space-y-3 border-t border-line pt-4">
      <div>
        <h3 id={titulo} className="flex items-center gap-2 text-[15px] font-bold text-ink">
          <Plug size={16} aria-hidden className="text-primary-dark" />
          Capacidades
        </h3>
        <p className="mt-0.5 text-[12.5px] leading-snug text-muted">
          Medios que cualquier módulo puede usar. Lo que actúa fuera de la aplicación nunca se ejecuta sin tu confirmación.
        </p>
      </div>
      {error && (
        <p role="alert" className="rounded-lg border border-risk-high-fg/30 bg-risk-high-bg px-3 py-2 text-[13px] text-ink-soft">
          {error}
        </p>
      )}
      {datos?.capacidades.map((c) => {
        const conInterruptor = datos.configurable && c.requiere_consentimiento && !c.modulo;
        return (
          <article
            key={c.clave}
            aria-label={c.nombre}
            className={clsx("rounded-xl border bg-surface px-4 py-3 shadow-card", c.activa ? "border-primary/40" : "border-line")}
          >
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <h4 className="text-[14.5px] font-bold text-ink">{c.nombre}</h4>
                <p className="mt-0.5 text-[12.5px] leading-snug text-ink-soft">{c.descripcion}</p>
              </div>
              {conInterruptor && (
                <button
                  type="button"
                  role="switch"
                  aria-checked={c.activa}
                  aria-label={`${c.activa ? "Desactivar" : "Activar"} ${c.nombre}`}
                  disabled={disabled || busy !== null}
                  onClick={() => void cambiar(c)}
                  className={clsx(
                    "relative h-7 w-12 shrink-0 rounded-pill transition disabled:cursor-not-allowed disabled:opacity-60",
                    c.activa ? "bg-primary" : "bg-line",
                  )}
                >
                  <span className={clsx("absolute top-1 h-5 w-5 rounded-full bg-white shadow-sm transition-all", c.activa ? "left-6" : "left-1")} />
                </button>
              )}
            </div>
            <p className="mt-1.5 text-[12.5px] font-semibold text-ink-soft">{estadoCapacidad(c, nombres)}</p>
            {c.usada_por.length > 0 && (
              <p className="mt-0.5 text-[12px] text-muted">La usan: {c.usada_por.map((m) => nombres[m] ?? m).join(", ")}.</p>
            )}
          </article>
        );
      })}
      {datos && !datos.configurable && (
        <p className="rounded-lg bg-line/50 px-3 py-2 text-[12.5px] leading-snug text-ink-soft">
          Sin cuentas de usuario, las capacidades las activa el operador del servicio.
        </p>
      )}
    </section>
  );
}
