// Top bar: menu (mobile), the conversation title, the navigation built from the panel registry
// (src/paneles/registro.ts) and the theme switch.

import { Menu, Moon, Sun } from "lucide-react";

import type { DefinicionPanel, Insignias } from "../paneles/tipos";
import type { Theme } from "../lib/theme";
import { Navegacion } from "./Navegacion";

interface TopBarProps {
  title: string;
  onOpenMenu: () => void;
  theme: Theme;
  onToggleTheme: () => void;
  /** The panels offered right now, in order. */
  paneles: DefinicionPanel[];
  insignias: Insignias;
  /** The panel open now (its button reads as expanded). */
  abierto: string | null;
  onAbrir: (id: string) => void;
}

export function TopBar({ title, onOpenMenu, theme, onToggleTheme, paneles, insignias, abierto, onAbrir }: TopBarProps) {
  return (
    <header className="flex items-center gap-1.5 border-b border-line bg-paper px-3 py-3 sm:gap-2 sm:px-6">
      <button type="button" onClick={onOpenMenu} aria-label="Abrir el menú" className="rounded-lg p-2 text-ink-soft hover:bg-line/60 lg:hidden">
        <Menu size={20} />
      </button>
      <h1 className="min-w-0 flex-1 truncate font-serif text-[18px] font-bold text-ink sm:text-[20px]">{title}</h1>
      <Navegacion paneles={paneles} insignias={insignias} abierto={abierto} onAbrir={onAbrir} />
      <button
        type="button"
        onClick={onToggleTheme}
        aria-label={theme === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro"}
        title={theme === "dark" ? "Tema claro" : "Tema oscuro"}
        className="rounded-lg p-2 text-ink-soft hover:bg-line/60"
      >
        {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
      </button>
    </header>
  );
}
