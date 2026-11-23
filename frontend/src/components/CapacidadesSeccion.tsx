// «Capacidades» inside «Módulos»: the means any module can use (mail, browser, documents, research), apart from the
// modules themselves, with the user's consent to each. A capability that is also a module (mail, browser) keeps its
// one switch on its module; here it only says so. Read each time the page is shown (`activa`), so a change made
// elsewhere shows.

import { useEffect, useId, useState } from "react";

import { ApiError } from "../api";
import type { CapacidadInfo, CapacidadesResponse } from "../apiModulos";
import { getCapacidades, setCapacidad } from "../apiModulos";
import { Caja, CargandoLista, Flash, ToggleSwitch } from "../ui";

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

interface CapacidadesSeccionProps {
  nombres: Record<string, string>;
  disabled: boolean;
  /** The page is on screen: the capabilities are read again each time it becomes so. */
  activa?: boolean;
}

export function CapacidadesSeccion({ nombres, disabled, activa = true }: CapacidadesSeccionProps) {
  const [datos, setDatos] = useState<CapacidadesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sinSoporte, setSinSoporte] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!activa) return;
    const controller = new AbortController();
    getCapacidades(controller.signal).then(
      (resp) => {
        setDatos(resp);
        setError(null);
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        // An older backend without capabilities: the section says so instead of an error.
        if (err instanceof ApiError && err.status === 404) setSinSoporte(true);
        else setError(`No se pudieron cargar las capacidades. ${message(err)}`);
      },
    );
    return () => controller.abort();
  }, [activa]);

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
    <div className="flex flex-col gap-4">
      <p className="m-0 text-fg-muted">
        Medios que cualquier módulo puede usar. Lo que actúa fuera de la aplicación nunca se ejecuta sin tu confirmación.
      </p>
      {error && <Flash variant="danger">{error}</Flash>}
      {sinSoporte && <Flash>Este servicio todavía no gestiona las capacidades por separado.</Flash>}
      {!datos && !error && !sinSoporte && <CargandoLista filas={3} etiqueta="Cargando las capacidades" />}
      {datos && !datos.configurable && <Flash>Sin cuentas de usuario, las capacidades las activa el operador del servicio.</Flash>}
      {datos && datos.capacidades.length > 0 && (
        <Caja as="section" aria-label="Capacidades">
          <Caja.Cabecera titulo="Capacidades" />
          <Caja.Lista>
            {datos.capacidades.map((c) => (
              <FilaCapacidad
                key={c.clave}
                c={c}
                nombres={nombres}
                conInterruptor={datos.configurable && c.requiere_consentimiento && !c.modulo}
                disabled={disabled || busy !== null}
                cargando={busy === c.clave}
                onCambiar={() => void cambiar(c)}
              />
            ))}
          </Caja.Lista>
        </Caja>
      )}
      {datos && datos.capacidades.length === 0 && <p className="m-0 text-fg-muted">Este servicio no ofrece capacidades adicionales.</p>}
    </div>
  );
}

interface FilaCapacidadProps {
  c: CapacidadInfo;
  nombres: Record<string, string>;
  conInterruptor: boolean;
  disabled: boolean;
  cargando: boolean;
  onCambiar: () => void;
}

function FilaCapacidad({ c, nombres, conInterruptor, disabled, cargando, onCambiar }: FilaCapacidadProps) {
  const id = useId();
  return (
    <Caja.Fila className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <h4 id={`${id}-nombre`} className="m-0 text-sm font-semibold">
          {c.nombre}
        </h4>
        <p className="m-0 text-fg-muted">{c.descripcion}</p>
        <p className="m-0 mt-1 text-xs">
          {estadoCapacidad(c, nombres)}
        </p>
        {c.usada_por.length > 0 && (
          <p className="m-0 text-xs text-fg-muted">La usan: {c.usada_por.map((m) => nombres[m] ?? m).join(", ")}.</p>
        )}
      </div>
      {conInterruptor && (
        <ToggleSwitch
          aria-labelledby={`${id}-nombre`}
          size="small"
          checked={c.activa}
          disabled={disabled}
          loading={cargando}
          loadingLabel="Guardando"
          buttonLabelOn="Activada"
          buttonLabelOff="Desactivada"
          onClick={onCambiar}
        />
      )}
    </Caja.Fila>
  );
}
