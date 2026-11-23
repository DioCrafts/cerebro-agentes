// Mounts the registry's panels (src/paneles/registro.ts) on demand, each in the frame its definition asks for:
//
//   presentacion "pagina"  → in the main column under its tab (PaginaPanel), instead of the conversation;
//   presentacion "dialogo" → a side sheet over the conversation (Dialogo);
//   no presentacion        → a panel not yet migrated, which draws its own overlay (as before).
//
// The shell renders two hosts: `zona="pagina"` inside the main column and `zona="superpuesto"` at the root.
//
// A panel's code is downloaded the first time it opens (React.lazy: one chunk per panel). Once opened it
// stays mounted while closed, so what the user typed in it survives closing and reopening. Focus: sheets
// trap it themselves (Dialogo); pages take it when they open (PaginaPanel); for the legacy overlays it
// is moved into the dialog and Tab is kept inside it here.

import type {
  ComponentType,
  ErrorInfo,
  LazyExoticComponent,
  ReactNode,
  RefObject,
} from "react";
import {
  Component,
  Suspense,
  createElement,
  lazy,
  useEffect,
  useRef,
  useState,
} from "react";

import type {
  ContextoPanel,
  DefinicionPanel,
  PropsPanel,
} from "../paneles/tipos";
import {
  Button,
  CargandoTexto,
  Dialogo,
  Flash,
  NivelTitulo,
  PaginaPanel,
} from "../ui";

// Everything Tab can land on. `summary` matters: a panel ending in a <details> («Más datos (opcional)», in
// Autónomos) let the focus escape the dialog, because the trap took the last input for the last stop.
const ENFOCABLES =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [contenteditable]:not([contenteditable="false"]), [tabindex]:not([tabindex="-1"])';

/** Whether Tab can reach it now: rendered, not inert, and not inside a closed <details> (its summary is). */
function alcanzable(el: HTMLElement): boolean {
  if (el.closest("[hidden], [inert]")) return false;
  for (let d = el.closest("details:not([open])"); d; d = d.parentElement?.closest("details:not([open])") ?? null) {
    const resumen = el.closest("summary");
    if (!resumen || resumen.parentElement !== d) return false;
  }
  if (getComputedStyle(el).visibility === "hidden") return false;
  for (let n: HTMLElement | null = el; n; n = n.parentElement) {
    if (getComputedStyle(n).display === "none") return false;
  }
  return true;
}

const perezosos = new Map<
  string,
  LazyExoticComponent<ComponentType<PropsPanel>>
>();

function componenteDe(
  panel: DefinicionPanel,
): LazyExoticComponent<ComponentType<PropsPanel>> {
  let componente = perezosos.get(panel.id);
  if (!componente) {
    componente = lazy(panel.cargar);
    perezosos.set(panel.id, componente);
  }
  return componente;
}

function dialogoActivo(contenedor: HTMLElement | null): HTMLElement | null {
  const dialogos = contenedor?.querySelectorAll<HTMLElement>('[role="dialog"]');
  return dialogos && dialogos.length > 0
    ? (dialogos[dialogos.length - 1] ?? null)
    : null;
}

/** Legacy panels only: focus into their dialog, and Tab kept inside it. */
function FocoEnDialogo({
  open,
  contenedor,
}: {
  open: boolean;
  contenedor: RefObject<HTMLDivElement | null>;
}) {
  useEffect(() => {
    if (!open) return;
    const dialogo = dialogoActivo(contenedor.current);
    if (dialogo && !dialogo.contains(document.activeElement)) {
      const primero = Array.from(dialogo.querySelectorAll<HTMLElement>(ENFOCABLES)).find(alcanzable);
      if (primero) primero.focus();
      else {
        dialogo.tabIndex = -1;
        dialogo.focus();
      }
    }
    const atrapar = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const actual = dialogoActivo(contenedor.current);
      if (!actual) return;
      const enfocables = Array.from(
        actual.querySelectorAll<HTMLElement>(ENFOCABLES),
      ).filter(alcanzable);
      if (enfocables.length === 0) return;
      const primero = enfocables[0];
      const ultimo = enfocables.at(-1);
      if (!primero || !ultimo) return;
      if (!actual.contains(document.activeElement)) {
        e.preventDefault();
        primero.focus();
      } else if (e.shiftKey && document.activeElement === primero) {
        e.preventDefault();
        ultimo.focus();
      } else if (!e.shiftKey && document.activeElement === ultimo) {
        e.preventDefault();
        primero.focus();
      }
    };
    document.addEventListener("keydown", atrapar);
    return () => document.removeEventListener("keydown", atrapar);
  }, [open, contenedor]);
  return null;
}

const MENSAJE_FALLO =
  "No se ha podido cargar esta parte de la aplicación. Comprueba tu conexión y vuelve a intentarlo; si acabas de recibir una versión nueva, recarga la página.";

function AvisoFallo() {
  return (
    <Flash variant="danger">
      <p className="mb-3">{MENSAJE_FALLO}</p>
      <Button onClick={() => window.location.reload()}>Recargar</Button>
    </Flash>
  );
}

interface FalloProps {
  open: boolean;
  onClose: () => void;
  /** Legacy panels have no frame of their own until their code arrives: the failure gets a sheet. */
  enHoja: boolean;
  children: ReactNode;
}

/** A panel whose code cannot be downloaded (offline before it was ever opened, or a new deploy). */
class FalloDeCarga extends Component<FalloProps, { fallo: boolean }> {
  state = { fallo: false };

  static getDerivedStateFromError() {
    return { fallo: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("panel failed to load", error, info.componentStack);
  }

  render() {
    if (!this.state.fallo) return this.props.children;
    if (!this.props.enHoja) return <AvisoFallo />;
    return (
      <Dialogo
        open={this.props.open}
        onClose={this.props.onClose}
        titulo="No se pudo abrir"
      >
        <AvisoFallo />
      </Dialogo>
    );
  }
}

interface RanuraProps {
  panel: DefinicionPanel;
  open: boolean;
  ctx: ContextoPanel;
  onClose: () => void;
}

// Set once the shell has painted for the first time (see useTrasElPrimerPintado).
let shellPintado = false;

/**
 * False until the frame after the first paint of the shell. A panel open on load (`/?panel=<id>`) only asks
 * for its chunk then: the header, the tabs and the conversation (or the panel's own header) paint first,
 * without waiting for the panel's code and what it imports (about 100 KiB with gzip for most panels). On a
 * phone, Lighthouse counted those downloads before the first content and put the LCP at about 4 s.
 */
function useTrasElPrimerPintado(): boolean {
  const [listo, setListo] = useState(shellPintado);
  useEffect(() => {
    if (listo) return;
    let espera: ReturnType<typeof setTimeout> | undefined;
    const fotograma = requestAnimationFrame(() => {
      espera = setTimeout(() => {
        shellPintado = true;
        setListo(true);
      });
    });
    return () => {
      cancelAnimationFrame(fotograma);
      clearTimeout(espera);
    };
  }, [listo]);
  return listo;
}

function Contenido({ panel, open, ctx, onClose }: RanuraProps) {
  const listo = useTrasElPrimerPintado();
  if (!listo) return panel.presentacion ? cargando(panel) : null;
  // The lazy component is cached per panel id (componenteDe), so it is the same on every render.
  const props: PropsPanel = { ctx, open, onClose };
  return createElement(componenteDe(panel), props);
}

// `data-cargando-codigo`: the panel's code is still downloading (the measurement harness waits for it).
const cargando = (panel: DefinicionPanel) => (
  <div data-cargando-codigo>
    <CargandoTexto lineas={4} etiqueta={`Cargando ${panel.etiqueta}…`} />
  </div>
);

function RanuraPagina(props: RanuraProps) {
  const { panel, open, onClose } = props;
  return (
    <div hidden={!open} data-ranura={panel.id}>
      <PaginaPanel
        titulo={panel.titulo}
        descripcion={panel.descripcion}
        icono={panel.icono}
        activa={open}
      >
        <NivelTitulo nivel={2}>
          <FalloDeCarga open={open} onClose={onClose} enHoja={false}>
            <Suspense fallback={cargando(panel)}>
              <Contenido {...props} />
            </Suspense>
          </FalloDeCarga>
        </NivelTitulo>
      </PaginaPanel>
    </div>
  );
}

function RanuraDialogo(props: RanuraProps) {
  const { panel, open, onClose } = props;
  return (
    <Dialogo
      open={open}
      onClose={onClose}
      titulo={panel.titulo}
      subtitulo={panel.descripcion}
      icono={panel.icono}
      ancho={panel.ancho}
    >
      <div data-ranura={panel.id}>
        <FalloDeCarga open={open} onClose={onClose} enHoja={false}>
          <Suspense fallback={cargando(panel)}>
            <Contenido {...props} />
          </Suspense>
        </FalloDeCarga>
      </div>
    </Dialogo>
  );
}

function RanuraLegado(props: RanuraProps) {
  const { panel, open, onClose } = props;
  const contenedor = useRef<HTMLDivElement | null>(null);
  return (
    <div ref={contenedor} className="contents" data-ranura={panel.id}>
      <FalloDeCarga open={open} onClose={onClose} enHoja>
        <Suspense
          fallback={
            open ? (
              <Dialogo
                open
                onClose={onClose}
                titulo={panel.titulo}
                icono={panel.icono}
              >
                {cargando(panel)}
              </Dialogo>
            ) : null
          }
        >
          <Contenido {...props} />
          <FocoEnDialogo open={open} contenedor={contenedor} />
        </Suspense>
      </FalloDeCarga>
    </div>
  );
}

interface PanelHostProps {
  /** The panels offered right now (one that stops being offered is unmounted). */
  paneles: DefinicionPanel[];
  abierto: string | null;
  ctx: ContextoPanel;
  onClose: () => void;
  /** "pagina": the page panels, in the main column; "superpuesto": sheets and legacy overlays, at the root. */
  zona: "pagina" | "superpuesto";
}

function enZona(panel: DefinicionPanel, zona: PanelHostProps["zona"]): boolean {
  return zona === "pagina"
    ? panel.presentacion === "pagina"
    : panel.presentacion !== "pagina";
}

export function PanelHost({
  paneles,
  abierto,
  ctx,
  onClose,
  zona,
}: PanelHostProps) {
  // The panels of this zone opened at least once (in order of first opening).
  const [montados, setMontados] = useState<string[]>([]);
  const deLaZona = paneles.filter((p) => enZona(p, zona));
  if (
    abierto !== null &&
    !montados.includes(abierto) &&
    deLaZona.some((p) => p.id === abierto)
  ) {
    setMontados([...montados, abierto]);
  }
  return (
    <>
      {montados.map((id) => {
        const panel = deLaZona.find((p) => p.id === id);
        if (!panel) return null;
        const props = { panel, open: abierto === id, ctx, onClose };
        if (panel.presentacion === "pagina")
          return <RanuraPagina key={id} {...props} />;
        if (panel.presentacion === "dialogo")
          return <RanuraDialogo key={id} {...props} />;
        return <RanuraLegado key={id} {...props} />;
      })}
    </>
  );
}
