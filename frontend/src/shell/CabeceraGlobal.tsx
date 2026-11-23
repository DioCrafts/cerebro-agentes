// The shell's global header (ui/CabeceraApp): menu (phone), mark, "HaciendaGPT / conversation", the
// command palette (/ or Ctrl+K), theme, notices and account; under it, the section tabs (PestanasNavegacion,
// GitHub's UnderlineNav): «Conversación» and every panel of the registry. A page panel's tab is the current
// one while it is shown; a sheet opens over the conversation and leaves the tab where it was. The tabs that
// do not fit go into «Más».

import { useEffect, useRef, useState } from "react";

import type { Theme } from "../lib/theme";
import type { DefinicionPanel, Insignias } from "../paneles/tipos";
import type { Comando, Pestana } from "../ui";
import {
  ActionList,
  ActionMenu,
  BotonCabecera,
  BotonComandos,
  CabeceraApp,
  IconButton,
  Iniciales,
  Migas,
  PaletaComandos,
  SeparadorCabecera,
  PestanasNavegacion,
} from "../ui";
import {
  BellIcon,
  CommentDiscussionIcon,
  MoonIcon,
  PlusIcon,
  ShieldLockIcon,
  SignOutIcon,
  SunIcon,
  ThreeBarsIcon,
} from "../ui/iconos";
import { Monogram } from "../components/Brand";
import type { AccountControls } from "./Asesor";

interface CabeceraGlobalProps {
  title: string;
  /** Whether the conversation has started (the breadcrumb shows its title). */
  conTitulo: boolean;
  theme: Theme;
  onToggleTheme: () => void;
  paneles: DefinicionPanel[];
  insignias: Insignias;
  /** The panel open now. */
  abierto: string | null;
  onAbrir: (id: string) => void;
  onCerrar: () => void;
  onAbrirConsultas: () => void;
  onNuevaConsulta: () => void;
  /** Extra commands for the palette (the conversations). */
  comandosExtra: Comando[];
  account?: AccountControls;
}

function enlace(id: string | null): string {
  const url = new URL(window.location.href);
  if (id === null) url.searchParams.delete("panel");
  else url.searchParams.set("panel", id);
  return `${url.pathname}${url.search}`;
}

function escribiendo(objetivo: EventTarget | null): boolean {
  const el = objetivo as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

export function CabeceraGlobal(props: CabeceraGlobalProps) {
  const { title, conTitulo, theme, onToggleTheme, paneles, insignias, abierto, onAbrir, onCerrar, onAbrirConsultas, onNuevaConsulta, comandosExtra, account } = props;
  const [paleta, setPaleta] = useState(false);
  const disparadorPaleta = useRef<HTMLElement | null>(null);

  // «/» (outside a text box) and Ctrl/⌘+K open the palette, as on GitHub.
  useEffect(() => {
    const atajo = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey);
      const barra = e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && !escribiendo(e.target);
      if (!k && !barra) return;
      e.preventDefault();
      disparadorPaleta.current = document.activeElement as HTMLElement | null;
      setPaleta(true);
    };
    document.addEventListener("keydown", atajo);
    return () => document.removeEventListener("keydown", atajo);
  }, []);

  const pagina = paneles.find((p) => p.id === abierto && p.presentacion === "pagina") ?? null;
  const hayAvisos = paneles.some((p) => p.id === "avisos");

  const comandos: Comando[] = [
    { id: "nueva", grupo: "Acciones", etiqueta: "Nueva consulta", icono: PlusIcon, palabras: "empezar pregunta", ejecutar: onNuevaConsulta },
    {
      id: "tema",
      grupo: "Acciones",
      etiqueta: theme === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro",
      icono: theme === "dark" ? SunIcon : MoonIcon,
      palabras: "tema oscuro claro modo",
      ejecutar: onToggleTheme,
    },
    { id: "conversacion", grupo: "Secciones", etiqueta: "Conversación", icono: CommentDiscussionIcon, ejecutar: onCerrar },
    ...paneles.map((p) => ({
      id: `panel-${p.id}`,
      grupo: "Secciones",
      etiqueta: p.etiqueta,
      descripcion: p.titulo,
      icono: p.icono,
      ejecutar: () => onAbrir(p.id),
    })),
    ...comandosExtra,
  ];

  const pestanas: Pestana[] = [
    { id: "conversacion", etiqueta: "Conversación", href: enlace(null), icono: CommentDiscussionIcon, actual: pagina === null, alElegir: onCerrar },
    ...paneles.map((p) => ({
      id: p.id,
      etiqueta: p.etiqueta,
      titulo: p.titulo,
      href: enlace(p.id),
      icono: p.icono,
      contador: p.insignia?.(insignias)?.valor,
      actual: pagina?.id === p.id,
      alElegir: () => onAbrir(p.id),
    })),
  ];

  return (
    <>
      <CabeceraApp
        saltarA="contenido"
        inicio={
          <span className="md:hidden">
            <IconButton icon={ThreeBarsIcon} aria-label="Abrir las consultas" variant="invisible" onClick={onAbrirConsultas} />
          </span>
        }
        marca={<Monogram size={32} />}
        contexto={<Migas raiz="HaciendaGPT" actual={conTitulo ? title : undefined} />}
        busqueda={
          <BotonComandos
            onClick={() => {
              disparadorPaleta.current = document.activeElement as HTMLElement | null;
              setPaleta(true);
            }}
          />
        }
        acciones={
          <>
            <SeparadorCabecera />
            <BotonCabecera
              icono={theme === "dark" ? SunIcon : MoonIcon}
              etiqueta={theme === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro"}
              onClick={onToggleTheme}
            />
            {hayAvisos && (
              <BotonCabecera
                icono={BellIcon}
                etiqueta={insignias.avisosSinLeer > 0 ? `Avisos: ${insignias.avisosSinLeer} sin leer` : "Avisos"}
                punto={insignias.avisosSinLeer > 0}
                onClick={() => onAbrir("avisos")}
              />
            )}
            {account && <MenuCuenta account={account} />}
          </>
        }
      >
        <PestanasNavegacion aria-label="Secciones" pestanas={pestanas} />
      </CabeceraApp>
      <PaletaComandos open={paleta} onClose={() => setPaleta(false)} comandos={comandos} returnFocusRef={disparadorPaleta} />
    </>
  );
}

function MenuCuenta({ account }: { account: AccountControls }) {
  return (
    <ActionMenu>
      <ActionMenu.Anchor>
        <button
          type="button"
          aria-label={`Tu cuenta (${account.email})`}
          className="flex rounded-full focus-visible:outline-2 focus-visible:outline-offset-2"
        >
          <Iniciales texto={account.email} tamano={32} />
        </button>
      </ActionMenu.Anchor>
      <ActionMenu.Overlay width="medium">
        <p className="m-0 border-b border-border-muted px-4 py-3 text-xs text-fg-muted">
          Sesión iniciada como <strong className="block truncate text-fg-default">{account.email}</strong>
        </p>
        <ActionList>
          <ActionList.Item onSelect={account.onOpenPrivacy}>
            <ActionList.LeadingVisual>
              <ShieldLockIcon />
            </ActionList.LeadingVisual>
            Privacidad y datos
          </ActionList.Item>
          <ActionList.Divider />
          <ActionList.Item variant="danger" onSelect={account.onLogout}>
            <ActionList.LeadingVisual>
              <SignOutIcon />
            </ActionList.LeadingVisual>
            Cerrar sesión
          </ActionList.Item>
        </ActionList>
      </ActionMenu.Overlay>
    </ActionMenu>
  );
}
