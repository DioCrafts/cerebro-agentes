// Prunes the colour themes of @primer/primitives down to the variables something can read.
//
// light.css and dark.css declare about 1,900 custom properties each (38 KiB gzip of the 41 KiB of the
// app's main CSS). The app and Primer React read a few hundred. This PostCSS plugin removes, only inside
// the theme blocks (`[data-color-mode…]`, `[data-light-theme…]`, `[data-dark-theme…]`), the declarations of
// variables nobody names.
//
// A variable is kept when:
//   - its name appears anywhere in the sources that can read it: src/ and the whole of @primer/react
//     (JS and CSS, used components or not, so adding a Primer component never needs this list to change);
//   - it matches a prefix that Primer builds at runtime (`var(--spacing-${gap})`, `--bgColor-${…}`): every
//     `--prefix-${` found in those sources keeps all the variables with that prefix;
//   - a regular declaration of the same stylesheet reads it (`color: var(--x)`);
//   - a kept variable reads it (`--a: var(--b)` keeps `--b`).
// The list is computed from files on disk when the config loads, so the result depends only on the
// sources and the build hashes it as usual (no stale cache when a component starts using a new token).
//
// It also drops the `[data-color-mode="auto"]` copies of both themes (half of each file, inside
// `@media (prefers-color-scheme: …)`): the app always sets `light` or `dark` (public/tema.js, lib/theme.ts,
// and Primer's ThemeProvider with `day`/`night`), never `auto`.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const NOMBRE = /--([a-zA-Z][\w-]*)/g;
const PREFIJO = /--([a-zA-Z][\w-]*-)\$\{/g;
const SELECTOR_TEMA = /data-(?:color-mode|light-theme|dark-theme)/;
const MODO_AUTO = /data-color-mode=["']?auto["']?\]/;

// The minimal PostCSS shapes this plugin uses (postcss is Vite's dependency, not ours).
interface Declaracion {
  prop: string;
  value: string;
  remove(): void;
}
interface Nodo {
  type: string;
  nodes?: unknown[];
  parent?: Nodo;
  remove(): void;
}
interface Regla extends Nodo {
  selector: string;
  walkDecls(cb: (d: Declaracion) => void): void;
}
interface Raiz {
  walkDecls(cb: (d: Declaracion) => void): void;
  walkRules(cb: (r: Regla) => void): void;
}

export interface UsosDeVariables {
  nombres: Set<string>;
  prefijos: Set<string>;
}

function* ficheros(dir: string): Generator<string> {
  for (const nombre of readdirSync(dir)) {
    const ruta = join(dir, nombre);
    if (statSync(ruta).isDirectory()) yield* ficheros(ruta);
    else if (/\.(m?js|css|tsx?|html)$/.test(nombre) && !nombre.endsWith(".d.ts")) yield ruta;
  }
}

/** Every `--name` and every runtime-built `--prefix-${` written in these directories. */
export function usosEn(directorios: string[]): UsosDeVariables {
  const nombres = new Set<string>();
  const prefijos = new Set<string>();
  for (const dir of directorios) {
    for (const ruta of ficheros(dir)) {
      const texto = readFileSync(ruta, "utf8");
      for (const m of texto.matchAll(NOMBRE)) nombres.add(m[1]);
      for (const m of texto.matchAll(PREFIJO)) prefijos.add(m[1]);
    }
  }
  return { nombres, prefijos };
}

/** The variables of the theme blocks that survive, given what the stylesheet itself reads. */
export function variablesVivas(
  definiciones: Map<string, string[]>,
  leidasEnLaHoja: Iterable<string>,
  usos: UsosDeVariables,
): Set<string> {
  const vivas = new Set<string>();
  const pendientes = [...usos.nombres, ...leidasEnLaHoja];
  for (const nombre of definiciones.keys()) {
    for (const p of usos.prefijos) if (nombre.startsWith(p)) pendientes.push(nombre);
  }
  while (pendientes.length) {
    const nombre = pendientes.pop() as string;
    if (vivas.has(nombre)) continue;
    vivas.add(nombre);
    for (const valor of definiciones.get(nombre) ?? []) {
      for (const m of valor.matchAll(NOMBRE)) pendientes.push(m[1]);
    }
  }
  return vivas;
}

export function podarVariablesDeTema(usos: UsosDeVariables) {
  return {
    postcssPlugin: "podar-variables-de-tema",
    OnceExit(raiz: Raiz) {
      const definiciones = new Map<string, string[]>();
      const leidas = new Set<string>();
      raiz.walkDecls((d) => {
        if (d.prop.startsWith("--")) {
          const lista = definiciones.get(d.prop.slice(2)) ?? [];
          lista.push(d.value);
          definiciones.set(d.prop.slice(2), lista);
        } else {
          for (const m of d.value.matchAll(NOMBRE)) leidas.add(m[1]);
        }
      });
      const vivas = variablesVivas(definiciones, leidas, usos);
      raiz.walkRules((regla) => {
        if (!SELECTOR_TEMA.test(regla.selector)) return;
        const selectores = regla.selector.split(",").filter((s) => !MODO_AUTO.test(s));
        if (selectores.length > 0) {
          regla.selector = selectores.join(",");
          regla.walkDecls((d) => {
            if (d.prop.startsWith("--") && !vivas.has(d.prop.slice(2))) d.remove();
          });
        }
        if (selectores.length === 0 || !regla.nodes?.length) {
          const padre = regla.parent;
          regla.remove();
          if (padre && padre.type === "atrule" && !padre.nodes?.length) padre.remove();
        }
      });
    },
  };
}
