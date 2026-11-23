import postcss from "postcss";
import { describe, expect, it } from "vitest";

import { podarVariablesDeTema, type UsosDeVariables } from "./podarVariables";

const TEMAS = `
[data-color-mode="light"][data-light-theme="light"],
[data-color-mode="auto"][data-light-theme="light"] {
  --fgColor-default: #1f2328;
  --fgColor-sinUso: #000;
  --button-bg: var(--bgColor-base);
  --bgColor-base: #fff;
  --spacing-normal: 16px;
  --ansi-cyan: #1b7c83;
}
@media (prefers-color-scheme: dark) {
  [data-color-mode="auto"][data-dark-theme="dark"] {
    --fgColor-default: #f0f6fc;
  }
}
:root {
  --propia-sin-uso: 1px;
}
.algo {
  color: var(--fgColor-default);
}
`;

async function podar(usos: UsosDeVariables): Promise<string> {
  const r = await postcss([podarVariablesDeTema(usos)]).process(TEMAS, { from: undefined });
  return r.css;
}

describe("podarVariablesDeTema", () => {
  it("quita de los temas las variables que nadie lee y deja las que sí", async () => {
    const css = await podar({ nombres: new Set(["button-bg"]), prefijos: new Set(["spacing-"]) });
    // Read by a rule of the stylesheet.
    expect(css).toContain("--fgColor-default: #1f2328");
    // Named in the sources, and what it reads in turn.
    expect(css).toContain("--button-bg: var(--bgColor-base)");
    expect(css).toContain("--bgColor-base: #fff");
    // Built at runtime with a known prefix (`var(--spacing-${…})`).
    expect(css).toContain("--spacing-normal: 16px");
    // Nobody reads them.
    expect(css).not.toContain("--fgColor-sinUso");
    expect(css).not.toContain("--ansi-cyan");
  });

  it("no toca lo que no es un bloque de tema", async () => {
    const css = await podar({ nombres: new Set(), prefijos: new Set() });
    expect(css).toContain("--propia-sin-uso: 1px");
    expect(css).toContain("color: var(--fgColor-default)");
  });

  it("quita las copias del modo auto, que la app nunca usa", async () => {
    const css = await podar({ nombres: new Set(), prefijos: new Set() });
    expect(css).not.toContain("auto");
    expect(css).not.toContain("prefers-color-scheme");
    expect(css).toContain('[data-color-mode="light"][data-light-theme="light"]');
  });
});
