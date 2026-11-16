// The advisor's shell: sidebar (history) + main column (top bar, conversation, composer) + the panels of the
// registry (src/paneles/registro.ts), opened from the navigation or a link (`/?panel=<id>`).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getCase } from "../api";
import { AuthAccountBar } from "../components/AuthAccountBar";
import { AvisoInstalar } from "../components/AvisoInstalar";
import { Composer } from "../components/Composer";
import { Hilo } from "../components/Hilo";
import { PanelHost } from "../components/PanelHost";
import type { SimulatorSeed } from "../components/SimulatorPanel";
import { Sidebar } from "../components/Sidebar";
import { TopBar } from "../components/TopBar";
import { newId } from "../lib/id";
import { useNavegadorActivo } from "../lib/navegador";
import { abrirPanel, panelEnUrl, quitarParametro, usePanelUrl } from "../lib/panelUrl";
import { sincronizarPush } from "../lib/push";
import { admiteServiceWorker } from "../lib/pwa";
import { useTheme } from "../lib/theme";
import { modulosActivos, panelesVisibles } from "../paneles/registro";
import type { ContextoPanel, Insignias } from "../paneles/tipos";
import { useAvisos } from "./useAvisos";
import { useConsulta } from "./useConsulta";
import { useModulos } from "./useModulos";

// The signed-in account, when the backend has accounts.
export interface AccountControls {
  email: string;
  onOpenPrivacy: () => void;
  onLogout: () => void;
}

// Back from Google's consent screen (mail module, `?correo=…`): «Módulos» shows the result.
function abrirModulosAlVolverDeGoogle() {
  try {
    if (panelEnUrl() === null && new URLSearchParams(window.location.search).has("correo")) abrirPanel("modulos", { reemplazar: true });
  } catch {
    // Nothing to reopen.
  }
}

export function Asesor({ account }: { account?: AccountControls }) {
  const [theme, toggleTheme] = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  // Figures the optimiser hands to the simulator ("Usar este plan en el simulador"): the simulator is
  // remounted with them, for the conversation they were computed in.
  const [simulatorSeed, setSimulatorSeed] = useState<{ id: number; conversationId: string; seed: SimulatorSeed } | null>(null);
  const [panel, abrir, cerrar] = usePanelUrl();
  // The element that opened the panel gets the focus back when it closes.
  const disparador = useRef<HTMLElement | null>(null);
  const panelAnterior = useRef<string | null>(panel);

  const consulta = useConsulta({
    alCambiarDeConversacion: () => {
      setSimulatorSeed(null);
      setMenuOpen(false);
    },
    alPrepararAcciones: () => modulos.recargarAcciones(),
  });
  const { active } = consulta;
  const avisos = useAvisos(active.caseId, active.facts);
  const modulos = useModulos(active.caseId, { aplicarCaso: consulta.applyCase, alCambiar: avisos.recargar });
  const navegadorActivo = useNavegadorActivo(true);

  useEffect(() => {
    abrirModulosAlVolverDeGoogle();
    void sincronizarPush();
  }, []);

  // «Módulos» re-reads the modules and actions each time it opens (a change made elsewhere shows up).
  const recargarModulos = modulos.recargar;
  useEffect(() => {
    if (panel === "modulos") recargarModulos();
  }, [panel, recargarModulos]);

  // Focus back to whatever opened the panel.
  useEffect(() => {
    if (panelAnterior.current !== null && panel === null) {
      const destino = disparador.current;
      if (destino && document.contains(destino)) destino.focus();
      disparador.current = null;
    }
    panelAnterior.current = panel;
  }, [panel]);

  const abrirDesde = useCallback(
    (id: string) => {
      if (panelEnUrl() === null) disparador.current = document.activeElement as HTMLElement | null;
      abrir(id);
    },
    [abrir],
  );
  const cerrarYLimpiar = useCallback(() => {
    // `?correo=` is read once by the mail section; it must not reopen «Módulos» on a reload.
    if (panelEnUrl() === "modulos") quitarParametro("correo");
    cerrar();
  }, [cerrar]);

  const paneles = useMemo(
    () =>
      panelesVisibles({
        modulosActivos: modulosActivos(modulos.datos),
        flags: { navegador: navegadorActivo, pwa: admiteServiceWorker() },
        conCuenta: account !== undefined,
      }),
    [modulos.datos, navegadorActivo, account],
  );
  // A link to a panel that is not offered (module off, flag off, unknown id) just shows the conversation.
  const abierto = panel !== null && paneles.some((p) => p.id === panel) ? panel : null;

  const visibleFactCount = active.facts.filter(
    (f) => f.name !== "periodo_fiscal" && f.name !== "menciona_ingresos" && !modulos.hechos.has(f.name),
  ).length;
  const insignias: Insignias = {
    avisosSinLeer: avisos.sinLeer,
    accionesPendientes: modulos.pendientes,
    datosEnExpediente: visibleFactCount,
  };

  const activeId = consulta.activeId;
  const ctx: ContextoPanel = {
    conversacionId: activeId,
    caso: { id: active.caseId, facts: active.facts, taxPeriod: active.taxPeriod, obligations: active.obligations },
    ocupado: consulta.busy,
    cambiarDato: consulta.changeFact,
    guardandoDato: consulta.removingFact,
    errorCaso: consulta.caseError,
    datosDeModulos: modulos.hechos,
    aplicarCaso: consulta.applyCase,
    recargarCaso: () => {
      if (active.caseId) void getCase({ caseId: active.caseId }).then(consulta.applyCase, () => undefined);
    },
    modulos: { datos: modulos.datos, error: modulos.error, cambiar: modulos.cambiar, nombres: modulos.nombres },
    acciones: { items: modulos.acciones, decidir: modulos.decidir, recargar: modulos.recargarAcciones },
    avisos: {
      items: avisos.items,
      cargando: avisos.cargando,
      error: avisos.error,
      marcarLeido: avisos.marcarLeido,
      recargar: avisos.recargar,
    },
    preguntar: (texto) => {
      cerrarYLimpiar();
      void consulta.submit(texto);
    },
    abrir: abrirDesde,
    semillaSimulador: simulatorSeed?.conversationId === activeId ? { id: simulatorSeed.id, seed: simulatorSeed.seed } : null,
    usarEnSimulador: (seed) => {
      setSimulatorSeed((prev) => ({ id: (prev?.id ?? 0) + 1, conversationId: activeId, seed }));
      abrir("simulador");
    },
  };

  return (
    <div className="flex h-[100dvh] w-screen overflow-hidden">
      <Sidebar
        conversations={consulta.conversations.filter((c) => c.turns.length > 0)}
        activeId={activeId}
        onSelect={consulta.switchTo}
        onDelete={consulta.remove}
        onNewConsultation={() => consulta.switchTo(newId())}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
      />
      <main className="flex h-full min-w-0 flex-1 flex-col bg-paper">
        {account && <AuthAccountBar email={account.email} onOpenPrivacy={account.onOpenPrivacy} onLogout={account.onLogout} />}
        <TopBar
          title={active.title}
          onOpenMenu={() => setMenuOpen(true)}
          theme={theme}
          onToggleTheme={toggleTheme}
          paneles={paneles}
          insignias={insignias}
          abierto={abierto}
          onAbrir={abrirDesde}
        />
        <AvisoInstalar />
        <div className="flex-1 overflow-y-auto px-3 py-5 sm:px-8 sm:py-6">
          <Hilo
            active={active}
            pending={consulta.pending}
            busy={consulta.busy}
            highlightedId={consulta.highlightedId}
            onSubmit={(text, replaceTurnId) => void consulta.submit(text, replaceTurnId)}
            onCite={consulta.handleCite}
            lastTurnRef={consulta.lastTurnRef}
            bottomRef={consulta.bottomRef}
          />
        </div>
        <div className="border-t border-line bg-paper px-3 pb-[max(env(safe-area-inset-bottom),12px)] pt-3 sm:px-8">
          <div className="mx-auto max-w-3xl">
            {modulos.pendientes > 0 && (
              <div role="status" className="mb-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-risk-high-fg/40 bg-risk-high-bg px-3 py-2 text-[13px] text-ink">
                <span>
                  {modulos.pendientes === 1 ? "Tienes 1 acción pendiente" : `Tienes ${modulos.pendientes} acciones pendientes`} de tu confirmación. No se hará
                  nada hasta que la confirmes.
                </span>
                <button
                  type="button"
                  onClick={() => abrirDesde("modulos")}
                  className="rounded-lg bg-surface px-2.5 py-1 text-[12.5px] font-semibold text-ink shadow-card hover:bg-line/60"
                >
                  Revisar
                </button>
              </div>
            )}
            <Composer
              key={`${activeId}-${consulta.composerSeed.id}`}
              initialValue={consulta.composerSeed.text}
              onSubmit={(text) => void consulta.submit(text)}
              onStop={consulta.stop}
              busy={consulta.busy}
            />
          </div>
        </div>
      </main>
      <PanelHost paneles={paneles} abierto={abierto} ctx={ctx} onClose={cerrarYLimpiar} />
    </div>
  );
}
