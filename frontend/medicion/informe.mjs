// Tablas en Markdown a partir de un resultado: node medicion/informe.mjs base > medicion/resultados/base.md
// El informe razonado (docs/ui/medicion-base.md) cita estas tablas; esto solo da las cifras.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { cifras } from "./comparar.mjs";

const AQUI = dirname(fileURLToPath(import.meta.url));

const kib = (b) => `${(b / 1024).toFixed(1)}`;
const tabla = (cabecera, filas) =>
  [`| ${cabecera.join(" | ")} |`, `|${cabecera.map((c, i) => (i === 0 ? "---" : "---:")).join("|")}|`, ...filas.map((f) => `| ${f.join(" | ")} |`)].join("\n");

/** Tokens de color por tema y cuánto se usa cada uno (texto, fondo y borde de los elementos visibles). */
export function paleta(r) {
  const ctx = (tema) => r.contextos.find((c) => c.tema === tema && c.viewport === "escritorio") ?? r.contextos.find((c) => c.tema === tema);
  const claro = ctx("claro")?.tokens ?? {};
  const oscuro = ctx("oscuro")?.tokens ?? {};
  const usos = (tema) => {
    const e = r.analisis.estilosPorTema[tema];
    const total = {};
    for (const k of ["colorTexto", "colorFondo", "colorBorde"]) for (const [hex, n] of e[k]?.valores ?? []) total[hex.slice(0, 7)] = (total[hex.slice(0, 7)] ?? 0) + n;
    return total;
  };
  const usoClaro = usos("claro");
  const usoOscuro = usos("oscuro");
  const nombres = [...new Set([...Object.keys(claro), ...Object.keys(oscuro)])].sort();
  const filas = nombres.map((n) => ({
    token: n,
    claro: claro[n]?.hex ?? "—",
    oscuro: oscuro[n]?.hex ?? "—",
    usosClaro: claro[n] ? (usoClaro[claro[n].hex.slice(0, 7)] ?? 0) : 0,
    usosOscuro: oscuro[n] ? (usoOscuro[oscuro[n].hex.slice(0, 7)] ?? 0) : 0,
  }));
  const conocidos = (tokens) => new Set(Object.values(tokens).map((t) => t.hex.slice(0, 7)));
  const fuera = (uso, tokens) =>
    Object.entries(uso)
      .filter(([hex]) => !conocidos(tokens).has(hex))
      .sort((a, b) => b[1] - a[1]);
  return { filas, fueraClaro: fuera(usoClaro, claro), fueraOscuro: fuera(usoOscuro, oscuro) };
}

export function informe(r) {
  const s = [];
  s.push(`# Medición «${r.nombre}» (${r.fecha.slice(0, 10)})`, "", `Navegador: Chromium ${r.navegador}. Duración: ${r.duracionS} s.`, "");

  s.push("## Cifras resumen", "", tabla(["Grupo", "Cifra", "Valor"], cifras(r).map((f) => [f.grupo, f.nombre, f.valor === null ? "—" : `${f.valor}${f.unidad ? ` ${f.unidad}` : ""}`])), "");

  if (r.lighthouse?.length) {
    s.push("## Lighthouse por pantalla", "", "Modo navegación, throttling simulado por defecto (móvil: 4G lento y CPU ×4; escritorio: preset de escritorio). INP no existe en modo navegación: ver «INP aproximado».", "");
    s.push(
      tabla(
        ["Pantalla", "Viewport", "Rend.", "Acc.", "FCP ms", "LCP ms", "CLS", "TBT ms", "SI ms", "JS sin usar KiB"],
        r.lighthouse.map((f) =>
          f.error ? [f.pantalla, f.viewport, "error", "", "", "", "", "", "", ""] : [f.pantalla, f.viewport, f.rendimiento, f.accesibilidad, f.fcpMs, f.lcpMs, f.cls, f.tbtMs, f.siMs, kib(f.jsSinUsarBytes)],
        ),
      ),
      "",
    );
  }

  s.push("## Bundle por chunk", "", tabla(["Chunk", "Tipo", "Inicial", "Bytes", "gzip", "brotli"], r.bundle.chunks.map((c) => [c.chunk, c.tipo, c.inicial ? "sí" : "", c.bytes, c.gzip, c.brotli])), "");

  s.push("## Accesibilidad: reglas de axe incumplidas", "", "Nodos sumados sobre todas las pantallas, viewports y temas.", "");
  s.push(tabla(["Regla", "Impacto", "Nodos", "Pantallas", "Ejemplo"], r.analisis.axe.reglas.map((x) => [x.id, x.impacto, x.nodos, x.pantallas.length, `\`${(x.ejemplo?.objetivo ?? "").replace(/\|/g, "\\|").slice(0, 70)}\``])), "");

  s.push("## Orden del foco (tema claro)", "");
  const filasFoco = [];
  for (const c of r.contextos.filter((x) => x.tema === "claro")) {
    for (const p of c.pantallas.filter((x) => x.foco)) {
      filasFoco.push([p.clave, c.viewport, p.foco.paradas, p.foco.sinIndicador.length, p.foco.saltosAtras, p.foco.fueraDePantalla, p.foco.escapaDelDialogo ?? "—"]);
    }
  }
  s.push(tabla(["Pantalla", "Viewport", "Paradas", "Sin indicador", "Saltos atrás", "Fuera de pantalla", "Escapa del diálogo"], filasFoco), "");
  s.push("### Solo con teclado", "", "```json", JSON.stringify(r.teclado, null, 1), "```", "");

  s.push("## Consistencia visual (todas las pantallas)", "");
  for (const [k, v] of Object.entries(r.analisis.estilos)) {
    if (k.startsWith("color")) continue;
    const top = v.valores.slice(0, k === "sombra" ? 6 : 24).map(([val, n]) => `\`${val.length > 60 ? `${val.slice(0, 60)}…` : val}\` ×${n}`);
    s.push(`- **${k}** (${v.distintos} distintos): ${top.join(", ")}`);
  }
  s.push("");
  for (const tema of r.temas) {
    s.push(`### Colores usados, tema ${tema}`, "");
    for (const k of ["colorTexto", "colorFondo", "colorBorde"]) {
      const v = r.analisis.estilosPorTema[tema][k];
      s.push(`- **${k}** (${v.distintos}): ${v.valores.slice(0, 20).map(([val, n]) => `\`${val}\` ×${n}`).join(", ")}`);
    }
    s.push("");
  }

  const p = paleta(r);
  s.push("## Paleta: tokens de color con su valor claro y oscuro", "", tabla(["Token", "Claro", "Oscuro", "Usos (claro)", "Usos (oscuro)"], p.filas.map((f) => [`\`${f.token}\``, `\`${f.claro}\``, `\`${f.oscuro}\``, f.usosClaro, f.usosOscuro])), "");
  s.push("Colores usados que no son ningún token (opacidades, blanco fijo, colores de Tailwind):", "");
  s.push(`- Claro: ${p.fueraClaro.slice(0, 20).map(([h, n]) => `\`${h}\` ×${n}`).join(", ")}`);
  s.push(`- Oscuro: ${p.fueraOscuro.slice(0, 20).map(([h, n]) => `\`${h}\` ×${n}`).join(", ")}`, "");

  s.push("## Flujos: clics, campos y pasos", "");
  const filasFlujo = [];
  for (const c of r.contextos.filter((x) => x.tema === "claro")) {
    for (const [k, v] of Object.entries(c.flujos)) filasFlujo.push([k, c.viewport, v.clics, v.campos, v.pasos, v.ms ?? v.msRespuestaCompleta ?? "", v.focoVuelve === undefined ? "" : v.focoVuelve ? "sí" : "no"]);
  }
  s.push(tabla(["Flujo", "Viewport", "Clics", "Campos", "Pasos", "ms", "Foco vuelve"], filasFlujo), "");

  if (r.estados) {
    const si = (x) => (x === null || x === undefined ? "n/a" : x ? "sí" : "**no**");
    s.push("## Estados de carga, error y vacío", "", "Con la API simulada lenta (2,5 s), caída (500) o sin datos; escritorio, tema claro.", "");
    s.push(
      tabla(
        ["Pantalla", "Pide datos al abrir", "Carga", "Error", "Reintentar", "Vacío explicado", "Texto de error / vacío"],
        Object.entries(r.estados).map(([id, e]) => [id, si(e.pideDatosAlAbrir), si(e.carga?.indicador), si(e.error?.indicador), si(e.error?.reintentar), si(e.vacio?.explicado), `${e.error?.texto ?? e.error?.nota ?? ""} / ${e.vacio?.texto ?? ""}`.replace(/\|/g, "/").slice(0, 150)]),
      ),
      "",
    );
  }

  s.push("## Textos poco claros (detección automática)", "", tabla(["Motivo", "Casos"], Object.entries(r.analisis.textos.porMotivo).map(([m, n]) => [m, n])), "");
  for (const h of r.analisis.textos.hallazgos.filter((x) => !x.motivo.startsWith("Solo icono"))) s.push(`- ${h.motivo}: «${h.texto.replace(/\|/g, "/")}» (${h.donde})`);
  s.push("");
  const errores = r.contextos.flatMap((c) => c.errores.map((e) => `${c.viewport}/${c.tema}: ${e.split("\n")[0]}`));
  if (errores.length) s.push("## Errores de consola", "", ...[...new Set(errores)].map((e) => `- ${e}`), "");
  if (Object.keys(r.rutasNoSimuladas ?? {}).length) s.push("## Rutas que la API simulada no conoce", "", "```json", JSON.stringify(r.rutasNoSimuladas, null, 1), "```", "");
  return s.join("\n");
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const nombre = process.argv[2] ?? "base";
  console.log(informe(JSON.parse(readFileSync(join(AQUI, "resultados", `${nombre}.json`), "utf8"))));
}
