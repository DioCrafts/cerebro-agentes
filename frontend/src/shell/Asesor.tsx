// The advisor's shell, laid out like a GitHub page: the global header with the section tabs (CabeceraGlobal),
// then a PageLayout with the consultations in the pane (ListaConsultas) and, in the content, either the
// conversation (Hilo + Composer) or the page panel that is open. The panels of the registry
// (src/paneles/registro.ts) open from the tabs, the palette or a link (`/?panel=<id>`): pages in the content,
// sheets and legacy overlays above it (PanelHost).

import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getCase } from "../api";
import { AvisoInstalar } from "../components/AvisoInstalar";
import { Composer } from "../components/Composer";
import { Portada } from "../components/Portada";
import { AREAS, estadoArea } from "../lib/areas";
import { PanelHost } from "../components/PanelHost";
import type { SimulatorSeed } from "../components/SimulatorPanel";
import { newId } from "../lib/id";
import { useNavegadorActivo } from "../lib/navegador";
import {
  abrirPanel,
  panelEnUrl,
  quitarParametro,
  usePanelUrl,
} from "../lib/panelUrl";
import { sincronizarPush } from "../lib/push";
import { admiteServiceWorker } from "../lib/pwa";
import { useTheme } from "../lib/theme";
import { modulosActivos, panelesVisibles } from "../paneles/registro";
import type { ContextoPanel, Insignias } from "../paneles/tipos";
import type { Comando } from "../ui";
import { Button, CargandoTexto, Flash, PageLayout } from "../ui";
import { CommentDiscussionIcon } from "../ui/iconos";
import { CabeceraGlobal } from "./CabeceraGlobal";
import { ListaConsultas } from "./ListaConsultas";
import { useAvisos } from "./useAvisos";
import { useConsulta } from "./useConsulta";
import { useModulos } from "./useModulos";
import { cargarHilo } from "./precargas";

// The conversation's turns (answers, sources, checks, the council…) are out of the first load: an empty
// conversation shows the home page (Portada), which is light. The chunk downloads with the first keystroke in the
// question box (the answer takes longer than the chunk), or straight away when a conversation with turns
// is on screen (main.tsx asks for it even earlier, see precargas.ts). Not when the browser is idle: on a
// deep-linked panel the browser is idle while the panel's chunk arrives, and the turns then competed with
// it (Lighthouse's LCP on a phone went from 2.7 to 4.1 s on those pages).
const Hilo = lazy(() => cargarHilo().then((m) => ({ default: m.Hilo })));
const precargarHilo = () => void cargarHilo().catch(() => undefined);
// The phone menu (the consultations) is a Primer Dialog, out of the first load until it opens.
const Dialog = lazy(() => import("../ui/DialogoMenu"));

// The signed-in account, when the backend has accounts.
export interface AccountControls {
  email: string;
  onOpenPrivacy: () => void;
  onLogout: () => void;
}

// Back from Google's consent screen (mail module, `?correo=…`): «Módulos» shows the result.
function abrirModulosAlVolverDeGoogle() {
  try {
    if (
      panelEnUrl() === null &&
      new URLSearchParams(window.location.search).has("correo")
    )
      abrirPanel("modulos", { reemplazar: true });
  } catch {
    // Nothing to reopen.
  }
}

export function Asesor({ account }: { account?: AccountControls }) {
  const [theme, toggleTheme] = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  // Figures the optimiser hands to the simulator ("Usar este plan en el simulador"): the simulator is
  // remounted with them, for the conversation they were computed in.
  const [simulatorSeed, setSimulatorSeed] = useState<{
    id: number;
    conversationId: string;
    seed: SimulatorSeed;
  } | null>(null);
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
  const modulos = useModulos(active.caseId, {
    aplicarCaso: consulta.applyCase,
    alCambiar: avisos.recargar,
  });
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
      if (panelEnUrl() === null) {
        const activo = document.activeElement as HTMLElement | null;
        // Chosen in a menu («Más» of the tabs): the item goes away with the menu, so the focus will return
        // to the button that opened the menu.
        const menu = activo?.closest('[role="menu"]') ? document.querySelector<HTMLElement>('[aria-haspopup="true"][aria-expanded="true"]') : null;
        disparador.current = menu ?? activo;
      }
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
  const abierto =
    panel !== null && paneles.some((p) => p.id === panel) ? panel : null;

  const visibleFactCount = active.facts.filter(
    (f) =>
      f.name !== "periodo_fiscal" &&
      f.name !== "menciona_ingresos" &&
      !modulos.hechos.has(f.name),
  ).length;
  const insignias: Insignias = {
    avisosSinLeer: avisos.sinLeer,
    accionesPendientes: modulos.pendientes,
    datosEnExpediente: visibleFactCount,
  };

  const activeId = consulta.activeId;
  const ctx: ContextoPanel = {
    conversacionId: activeId,
    caso: {
      id: active.caseId,
      facts: active.facts,
      taxPeriod: active.taxPeriod,
      obligations: active.obligations,
    },
    ocupado: consulta.busy,
    cambiarDato: consulta.changeFact,
    guardandoDato: consulta.removingFact,
    errorCaso: consulta.caseError,
    datosDeModulos: modulos.hechos,
    aplicarCaso: consulta.applyCase,
    recargarCaso: () => {
      if (active.caseId)
        void getCase({ caseId: active.caseId }).then(
          consulta.applyCase,
          () => undefined,
        );
    },
    modulos: {
      datos: modulos.datos,
      error: modulos.error,
      cambiar: modulos.cambiar,
      nombres: modulos.nombres,
      recargar: modulos.recargar,
    },
    acciones: {
      items: modulos.acciones,
      decidir: modulos.decidir,
      recargar: modulos.recargarAcciones,
    },
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
    semillaSimulador:
      simulatorSeed?.conversationId === activeId
        ? { id: simulatorSeed.id, seed: simulatorSeed.seed }
        : null,
    usarEnSimulador: (seed) => {
      setSimulatorSeed((prev) => ({
        id: (prev?.id ?? 0) + 1,
        conversationId: activeId,
        seed,
      }));
      abrir("simulador");
    },
  };

  const conversaciones = consulta.conversations.filter(
    (c) => c.turns.length > 0,
  );
  const nuevaConsulta = () => {
    cerrarYLimpiar();
    consulta.switchTo(newId());
  };
  const comandosConsultas: Comando[] = conversaciones.map((c) => ({
    id: `consulta-${c.id}`,
    grupo: "Conversaciones",
    etiqueta: c.title,
    icono: CommentDiscussionIcon,
    ejecutar: () => {
      cerrarYLimpiar();
      consulta.switchTo(c.id);
    },
  }));
  const lista = (alElegir?: () => void) => (
    <ListaConsultas
      conversations={conversaciones}
      activeId={activeId}
      onSelect={(id) => {
        alElegir?.();
        cerrarYLimpiar();
        consulta.switchTo(id);
      }}
      onDelete={consulta.remove}
      areas={AREAS.filter((a) => estadoArea(a, modulos.datos?.modulos ?? null) === "lista").map((a) => a.nombre)}
      onNewConsultation={() => {
        alElegir?.();
        nuevaConsulta();
      }}
    />
  );
  // A page panel takes the content's place; the conversation stays mounted underneath (hidden).
  const paginaAbierta = paneles.some(
    (p) => p.id === abierto && p.presentacion === "pagina",
  );

  // An empty conversation shows the home page instead of the thread.
  const portada = active.turns.length === 0 && !consulta.busy;

  // «Saltar a la caja de mensajes»: straight to the question box (a page panel open is closed first).
  const saltarALaConsulta = (e: { preventDefault: () => void }) => {
    e.preventDefault();
    if (paginaAbierta) cerrarYLimpiar();
    requestAnimationFrame(() => document.getElementById("consulta")?.focus());
  };

  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#consulta" className="saltar-enlace" onClick={saltarALaConsulta}>
        Saltar a la caja de mensajes
      </a>
      <CabeceraGlobal
        title={active.title}
        conTitulo={active.turns.length > 0}
        theme={theme}
        onToggleTheme={toggleTheme}
        paneles={paneles}
        insignias={insignias}
        abierto={abierto}
        onAbrir={abrirDesde}
        onCerrar={cerrarYLimpiar}
        onAbrirConsultas={() => setMenuOpen(true)}
        onNuevaConsulta={nuevaConsulta}
        comandosExtra={comandosConsultas}
        account={account}
      />
      <AvisoInstalar />
      <PageLayout
        containerWidth="xlarge"
        padding="normal"
        columnGap="normal"
        className="flex-1"
      >
        <PageLayout.Pane
          position="start"
          width="medium"
          sticky
          hidden={{ narrow: true }}
          aria-label="Tus conversaciones y áreas"
          divider="none"
        >
          {lista()}
        </PageLayout.Pane>
        <PageLayout.Content as="main" width="large">
          <div id="contenido" tabIndex={-1} className="outline-none" />
          <PanelHost
            zona="pagina"
            paneles={paneles}
            abierto={abierto}
            ctx={ctx}
            onClose={cerrarYLimpiar}
          />
          <div hidden={paginaAbierta} className="flex flex-col gap-4">
            {portada ? (
              <Portada
                onPick={(pregunta) => void consulta.submit(pregunta)}
                onAbrir={abrirDesde}
                paneles={paneles}
                modulos={modulos.datos?.modulos ?? null}
                avisosSinLeer={avisos.sinLeer}
                accionesPendientes={modulos.pendientes}
              />
            ) : (
              <>
              {/* The level between the page's h1 (the breadcrumb) and each comment's h3. */}
              <h2 className="sr-only">Conversación</h2>
              <Suspense fallback={<CargandoTexto lineas={3} etiqueta="Cargando la conversación" />}>
            <Hilo
              active={active}
              turno={consulta.turno}
              busy={consulta.busy}
              highlightedId={consulta.highlightedId}
              onSubmit={(text, replaceTurnId) =>
                void consulta.submit(text, replaceTurnId)
              }
              onCite={consulta.handleCite}
              lastTurnRef={consulta.lastTurnRef}
              bottomRef={consulta.bottomRef}
            />
              </Suspense>
              </>
            )}
            <div
              className={`sticky bottom-0 z-[1] -mx-2 flex flex-col gap-2 bg-canvas-default px-2 pb-[max(env(safe-area-inset-bottom),16px)] pt-2 ${active.turns.length > 0 || consulta.busy ? "md:ml-14" : ""}`}
            >
              {/* On the home page the pending actions are already in «Pendiente para ti». */}
              {modulos.pendientes > 0 && !portada && (
                <Flash variant="warning" role="status">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      {modulos.pendientes === 1
                        ? "Tienes 1 acción pendiente"
                        : `Tienes ${modulos.pendientes} acciones pendientes`}{" "}
                      de tu confirmación. No se hará nada hasta que la
                      confirmes.
                    </span>
                    <Button size="small" onClick={() => abrirDesde("modulos")}>
                      Revisar
                    </Button>
                  </div>
                </Flash>
              )}
              <Composer
                key={`${activeId}-${consulta.composerSeed.id}`}
                initialValue={consulta.composerSeed.text}
                onSubmit={(text) => void consulta.submit(text)}
                onStop={() => consulta.stop()}
                alEscribir={precargarHilo}
                busy={consulta.busy}
              />
            </div>
          </div>
        </PageLayout.Content>
      </PageLayout>
      {menuOpen && (
        <Suspense fallback={null}>
        <Dialog
          title="Menú"
          position="left"
          width="medium"
          onClose={() => setMenuOpen(false)}
        >
          {lista(() => setMenuOpen(false))}
        </Dialog>
        </Suspense>
      )}
      <PanelHost
        zona="superpuesto"
        paneles={paneles}
        abierto={abierto}
        ctx={ctx}
        onClose={cerrarYLimpiar}
      />
    </div>
  );
}
