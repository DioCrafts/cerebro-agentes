// Medición repetible de la UI y la UX (npm run medir). Ver medicion/README.md.
//
//   npm run medir -- --nombre base           mide y guarda resultados/base.json y capturas/base/
//   npm run medir -- --comparar base         mide («despues») y saca la tabla de antes y después
//
// Opciones: --nombre <n>, --comparar <n>, --sin-lighthouse, --sin-build, --sin-estados, --solo-escritorio.
// Todo corre con la API simulada (api-simulada.mjs) detrás de `vite preview`: ni backend ni modelos.
// Un solo navegador headless a la vez; Lighthouse se ejecuta de uno en uno al final.

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { brotliCompressSync, gzipSync } from "node:zlib";

import AxeBuilder from "@axe-core/playwright";
import { chromium } from "playwright";

import { arrancarApiSimulada } from "./api-simulada.mjs";
import { compararResultados } from "./comparar.mjs";
import { informe } from "./informe.mjs";
import { descriptorFoco, inventarioEstilos, textosVisibles, tokensDeColor } from "./navegador.mjs";

const AQUI = dirname(fileURLToPath(import.meta.url));
const FRONTEND = dirname(AQUI);
const PUERTO_API = Number(process.env.MEDIR_PUERTO_API ?? 8799);
const PUERTO_WEB = Number(process.env.MEDIR_PUERTO_WEB ?? 4179);
const BASE_URL = `http://localhost:${PUERTO_WEB}`;
const API_URL = `http://127.0.0.1:${PUERTO_API}`;

// ---------- argumentos ----------
const args = process.argv.slice(2);
const opcion = (nombre) => {
  const i = args.indexOf(`--${nombre}`);
  return i === -1 ? null : (args[i + 1] ?? null);
};
const bandera = (nombre) => args.includes(`--${nombre}`);
const COMPARAR = opcion("comparar");
const NOMBRE = opcion("nombre") ?? (COMPARAR ? "despues" : "actual");
if (!/^[a-z0-9_-]+$/i.test(NOMBRE)) throw new Error(`Nombre no válido: ${NOMBRE}`);
const CAPTURAS = join(AQUI, "capturas", NOMBRE);
const RESULTADOS = join(AQUI, "resultados");

const VIEWPORTS = [
  { id: "movil", width: 375, height: 812, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
  { id: "escritorio", width: 1440, height: 900, isMobile: false, hasTouch: false, deviceScaleFactor: 1 },
].filter((v) => !bandera("solo-escritorio") || v.id === "escritorio");
const TEMAS = ["claro", "oscuro"];

// Selectores de la UI. Se aceptan los de la UI anterior y los de la UI con Primer, para que la misma medición
// valga antes y después: el botón de enviar se llamaba «Enviar» y ahora «Preguntar»; el de parar, «Detener la
// respuesta» y ahora «Detener»; la barra «Herramientas» con botones pasa a las pestañas «Secciones» con enlaces.
const ENVIAR = { name: /^(Enviar|Preguntar)$/ };
const DETENER = { name: /^(Detener la respuesta|Detener)$/ };
const NAV = 'nav[aria-label="Secciones"]';
const BOTON_MAS = `${NAV} button[aria-haspopup]`;
const ENLACE_PANEL = (id) => `${NAV} a[data-panel="${id}"]`;
// Un panel abierto: una hoja lateral (diálogo modal visible) o una página en la columna principal.
const HOJA_VISIBLE = "[role=dialog][aria-modal=true]:visible";
const PAGINA_VISIBLE = "main [data-ranura]:not([hidden])";

const PREGUNTA = "¿Tengo que hacer la declaración de la renta si gano 34.000 € con un solo pagador?";
const log = (...a) => console.log(`[medir ${new Date().toISOString().slice(11, 19)}]`, ...a);

// ---------- utilidades de procesos ----------
function ejecutar(cmd, argumentos, opciones = {}) {
  return new Promise((resolve, reject) => {
    const hijo = spawn(cmd, argumentos, { cwd: FRONTEND, stdio: "inherit", ...opciones });
    hijo.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`${cmd} ${argumentos.join(" ")} → ${code}`))));
  });
}

async function esperarUrl(url, ms = 30_000) {
  const fin = Date.now() + ms;
  while (Date.now() < fin) {
    try {
      const r = await fetch(url);
      if (r.ok) return;
    } catch {
      // aún no
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`No responde ${url}`);
}

const control = (cuerpo) =>
  fetch(`${API_URL}/__control`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) });

/** Chromium de Playwright; si su revisión no está descargada, la más reciente de la caché o Chrome. */
function rutaChromium() {
  if (process.env.MEDIR_CHROMIUM) return process.env.MEDIR_CHROMIUM;
  if (existsSync(chromium.executablePath())) return undefined;
  const cache = process.env.PLAYWRIGHT_BROWSERS_PATH || join(homedir(), process.platform === "darwin" ? "Library/Caches/ms-playwright" : ".cache/ms-playwright");
  const candidatos = existsSync(cache)
    ? readdirSync(cache)
        .filter((d) => /^chromium-\d+$/.test(d))
        .sort((a, b) => Number(b.split("-")[1]) - Number(a.split("-")[1]))
    : [];
  for (const dir of candidatos) {
    for (const sub of [
      "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
      "chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
      "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
      "chrome-linux64/chrome",
      "chrome-linux/chrome",
    ]) {
      const ruta = join(cache, dir, sub);
      if (existsSync(ruta)) return ruta;
    }
  }
  const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  if (existsSync(chrome)) return chrome;
  throw new Error("No hay Chromium: ejecuta `npx playwright install chromium` o define MEDIR_CHROMIUM.");
}

// ---------- bundle ----------
function medirBundle() {
  const dist = join(FRONTEND, "dist");
  const html = readFileSync(join(dist, "index.html"), "utf8");
  const entrada = /<script[^>]+src="\/assets\/([^"]+\.js)"/.exec(html)?.[1];
  const css = [...html.matchAll(/href="\/assets\/([^"]+\.css)"/g)].map((m) => m[1]);
  const precargas = [...html.matchAll(/rel="modulepreload"[^>]*href="\/assets\/([^"]+)"/g)].map((m) => m[1]);
  const chunks = [];
  for (const fichero of readdirSync(join(dist, "assets"))) {
    if (!/\.(js|css)$/.test(fichero)) continue;
    const contenido = readFileSync(join(dist, "assets", fichero));
    chunks.push({
      fichero,
      chunk: fichero.replace(/-[A-Za-z0-9_-]{8}\.(js|css)$/, ".$1"),
      tipo: fichero.endsWith(".css") ? "css" : "js",
      bytes: contenido.length,
      gzip: gzipSync(contenido, { level: 9 }).length,
      brotli: brotliCompressSync(contenido).length,
      inicial: fichero === entrada || css.includes(fichero) || precargas.includes(fichero),
    });
  }
  chunks.sort((a, b) => b.bytes - a.bytes);
  const suma = (lista, k) => lista.reduce((s, c) => s + c[k], 0);
  const js = chunks.filter((c) => c.tipo === "js");
  const inicial = chunks.filter((c) => c.inicial);
  return {
    chunks,
    totales: {
      jsBytes: suma(js, "bytes"),
      jsGzip: suma(js, "gzip"),
      cssBytes: suma(chunks.filter((c) => c.tipo === "css"), "bytes"),
      cssGzip: suma(chunks.filter((c) => c.tipo === "css"), "gzip"),
      inicialGzip: suma(inicial, "gzip"),
      inicialBytes: suma(inicial, "bytes"),
      numeroChunks: js.length,
    },
  };
}

// ---------- medidas por pantalla ----------
const INIT_SCRIPT = (tema) => `
  try { localStorage.setItem("haciendagpt_theme", ${JSON.stringify(tema === "oscuro" ? "dark" : "light")}); } catch (e) {}
  window.__interacciones = [];
  try {
    new PerformanceObserver((lista) => {
      for (const e of lista.getEntries()) if (e.interactionId) window.__interacciones.push({ nombre: e.name, duracion: e.duration });
    }).observe({ type: "event", durationThreshold: 16, buffered: true });
  } catch (e) {}
`;

class Contador {
  constructor() {
    this.clics = 0;
    this.teclas = 0;
    this.campos = 0;
    this.pasos = [];
  }
  paso(texto) {
    this.pasos.push(texto);
  }
  resumen() {
    return { clics: this.clics, teclas: this.teclas, campos: this.campos, pasos: this.pasos.length, detalle: this.pasos };
  }
}

async function clic(page, locator, contador, paso) {
  await locator.click();
  contador.clics++;
  contador.paso(paso);
}

async function rellenar(locator, texto, contador, paso) {
  await locator.fill(texto);
  contador.campos++;
  contador.paso(paso);
}

async function esperarCalma(page, ms = 350) {
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => undefined);
  await page.waitForTimeout(ms);
}

async function inpDesde(page) {
  const lista = await page.evaluate(() => {
    const l = window.__interacciones ?? [];
    window.__interacciones = [];
    return l;
  });
  return lista.length ? Math.max(...lista.map((e) => e.duracion)) : null;
}

async function recorrerFoco(page, maximo = 70) {
  await page.evaluate(() => {
    document.activeElement?.blur?.();
    window.scrollTo(0, 0);
  });
  const dialogo = await page.locator(HOJA_VISIBLE).count();
  const paradas = [];
  for (let i = 0; i < maximo; i++) {
    await page.keyboard.press("Tab");
    const d = await page.evaluate(descriptorFoco);
    if (paradas.length && d.tipo !== "body" && paradas[0].nombre === d.nombre && paradas[0].x === d.x && paradas[0].y === d.y) break;
    paradas.push(d);
  }
  let saltosAtras = 0;
  for (let i = 1; i < paradas.length; i++) {
    const a = paradas[i - 1];
    const b = paradas[i];
    if (a.tipo === "body" || b.tipo === "body") continue;
    // Retrocede más de una línea en vertical sin ser el paso natural de columna (sidebar → contenido).
    if (b.y < a.y - 48 && !(b.x > a.x + 150)) saltosAtras++;
  }
  return {
    paradas: paradas.length,
    sinIndicador: paradas.filter((p) => p.tipo !== "body" && !p.indicador).map((p) => `${p.tipo}: ${p.nombre}`),
    fueraDePantalla: paradas.filter((p) => p.tipo !== "body" && !p.enPantalla).length,
    alBody: paradas.filter((p) => p.tipo === "body").length,
    saltosAtras,
    escapaDelDialogo: dialogo ? paradas.filter((p) => p.fueraDelDialogo).length : null,
    orden: paradas.slice(0, 40).map((p) => `${p.tipo}:${p.nombre}`),
  };
}

async function medirPantalla(page, contexto, clave, { raiz = null, foco = false } = {}) {
  const inicio = Date.now();
  const id = `${clave}--${contexto.viewport.id}--${contexto.tema}`;
  await page.screenshot({ path: join(CAPTURAS, `${id}.png`), fullPage: !raiz, animations: "disabled" });
  const axe = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"])
    .analyze()
    .catch((err) => ({ error: String(err), violations: [] }));
  const violaciones = axe.violations.map((v) => ({
    id: v.id,
    impacto: v.impact ?? "minor",
    nodos: v.nodes.length,
    ayuda: v.help,
    ejemplos: v.nodes.slice(0, 3).map((n) => ({ objetivo: n.target.join(" "), resumen: (n.failureSummary ?? "").slice(0, 240) })),
  }));
  const { elementos, inv } = await page.evaluate(inventarioEstilos, raiz);
  const textos = await page.evaluate(textosVisibles, raiz);
  // Si la app rechaza una respuesta de la simulación, la medición no vale: se anota como error.
  const rechazos = await page.getByText("formato inesperado").count();
  if (rechazos) contexto.errores.push(`Simulación incompleta en ${clave}: la app muestra «formato inesperado» (${rechazos})`);
  const recorrido = foco ? await recorrerFoco(page) : null;
  const inp = await inpDesde(page);
  contexto.pantallas.push({ clave, id, captura: `capturas/${NOMBRE}/${id}.png`, elementos, inventario: inv, violaciones, textos, foco: recorrido, inpMs: inp });
  log(`  ${id}: ${violaciones.reduce((s, v) => s + v.nodos, 0)} nodos con violaciones axe (${Date.now() - inicio} ms)`);
}

// ---------- flujos ----------
async function entrar(page, contexto) {
  const c = new Contador();
  const t0 = Date.now();
  await page.goto(BASE_URL);
  c.paso("Abrir la aplicación");
  await page.locator("input[type=email]").waitFor();
  await esperarCalma(page);
  await medirPantalla(page, contexto, "entrar", { foco: contexto.tema === "claro" });
  await rellenar(page.locator("input[type=email]"), "ana.lopez@example.com", c, "Escribir el correo");
  await rellenar(page.locator("input[type=password]"), "una-clave-de-prueba", c, "Escribir la contraseña");
  await clic(page, page.locator("form button[type=submit]"), c, "Pulsar «Entrar»");
  await page.getByRole("textbox", { name: "Escribe tu consulta" }).waitFor();
  await esperarCalma(page);
  contexto.flujos.entrar = { ...c.resumen(), ms: Date.now() - t0 };
  await medirPantalla(page, contexto, "inicio", { foco: contexto.tema === "claro" });
}

async function preguntar(page, contexto) {
  const c = new Contador();
  const caja = page.getByRole("textbox", { name: "Escribe tu consulta" });
  await clic(page, caja, c, "Pulsar en la caja de la consulta");
  await rellenar(caja, PREGUNTA, c, "Escribir la pregunta");
  const t0 = Date.now();
  await clic(page, page.getByRole("button", ENVIAR), c, "Pulsar «Enviar»");
  // Primer texto en streaming: aparece un trozo de la respuesta.
  await page.getByText("Sí, tienes que presentar", { exact: false }).first().waitFor({ timeout: 15000 });
  const primerTexto = Date.now() - t0;
  await page.waitForTimeout(250);
  await page.screenshot({ path: join(CAPTURAS, `respondiendo--${contexto.viewport.id}--${contexto.tema}.png`), animations: "disabled" });
  // Fin: vuelve el botón «Enviar» (desaparece «Detener la respuesta»).
  await page.getByRole("button", DETENER).waitFor({ state: "detached", timeout: 20000 });
  const total = Date.now() - t0;
  await esperarCalma(page, 800);
  contexto.flujos.preguntar = { ...c.resumen(), msPrimerTexto: primerTexto, msRespuestaCompleta: total };
  await medirPantalla(page, contexto, "conversacion", { foco: contexto.tema === "claro" });
}

/** Abre un panel como lo haría una persona: su pestaña o, si no está a la vista, «Más». */
async function abrirPanel(page, id, c) {
  const directo = page.locator(ENLACE_PANEL(id)).first();
  if (await directo.isVisible().catch(() => false)) {
    await clic(page, directo, c, `Pulsar la pestaña del panel «${id}»`);
  } else {
    const mas = page.locator(BOTON_MAS).first();
    if (!(await mas.isVisible().catch(() => false))) return null;
    await clic(page, mas, c, "Abrir «Más»");
    await page.locator("[role=menu]").waitFor();
    const item = page.locator(`[role=menu] [data-panel="${id}"]`);
    if (!(await item.count())) {
      // El panel no se ofrece ahora (p. ej. depende de una respuesta del servicio que falta).
      await page.keyboard.press("Escape");
      return null;
    }
    await clic(page, item.first(), c, `Elegir «${id}» en el menú`);
  }
  const panel = await panelAbierto(page, id);
  // El código del panel se descarga la primera vez (React.lazy): se espera a que llegue, no a sus datos.
  await page
    .waitForFunction(() => !document.querySelector("[data-cargando-codigo]"), null, { timeout: 10000 })
    .catch(() => undefined);
  return panel;
}

/** El panel en pantalla: { locator, raiz } (raiz es un selector CSS para medirPantalla). */
async function panelAbierto(page, id, timeout = 10000) {
  const hoja = page.locator(HOJA_VISIBLE).last();
  const pagina = page.locator(`main [data-ranura="${id}"]:not([hidden])`);
  await hoja.or(pagina).first().waitFor({ timeout });
  if (await pagina.isVisible().catch(() => false)) return { locator: pagina, raiz: `main [data-ranura="${id}"]`, tipo: "pagina" };
  return { locator: hoja, raiz: null, tipo: "hoja" };
}

async function cerrarPanel(page) {
  if (await page.locator(HOJA_VISIBLE).count()) {
    await page.keyboard.press("Escape");
    await page.waitForTimeout(200);
    if (await page.locator(HOJA_VISIBLE).count()) {
      const cerrar = page.locator(`${HOJA_VISIBLE} button[aria-label=Cerrar]`).first();
      if (await cerrar.isVisible().catch(() => false)) await cerrar.click();
      else await page.keyboard.press("Escape");
    }
    await page.locator(HOJA_VISIBLE).first().waitFor({ state: "detached", timeout: 5000 }).catch(() => undefined);
  } else if (await page.locator(PAGINA_VISIBLE).count()) {
    // Una página se cierra volviendo a la pestaña «Conversación», como en GitHub.
    await page.locator(ENLACE_PANEL("conversacion")).first().click();
    await page.locator(PAGINA_VISIBLE).first().waitFor({ state: "hidden", timeout: 5000 }).catch(() => undefined);
  }
  await page.waitForTimeout(150);
}

async function panelesOfrecidos(page) {
  // Las pestañas a la vista (la lista oculta que mide lo que cabe no tiene enlaces) menos «Conversación».
  const enBarra = (await page.locator(`${NAV} a[data-panel]`).evaluateAll((els) => els.filter((e) => e.offsetParent !== null).map((e) => e.dataset.panel))).filter(
    (id) => id !== "conversacion",
  );
  const mas = page.locator(BOTON_MAS).first();
  let enMenu = [];
  if (await mas.isVisible().catch(() => false)) {
    await mas.click();
    await page.locator("[role=menu]").waitFor();
    enMenu = await page.locator("[role=menu] [data-panel]").evaluateAll((els) => els.map((e) => e.dataset.panel));
    await medirPantalla(page, contextoActual, "menu-mas");
    await page.keyboard.press("Escape");
  }
  return { enBarra, enMenu };
}
let contextoActual = null;

async function recorrerPaneles(page, contexto) {
  const { enBarra, enMenu } = await panelesOfrecidos(page);
  contexto.navegacion = { enBarra, enMenu };
  for (const id of [...new Set([...enBarra, ...enMenu])]) {
    const c = new Contador();
    const t0 = Date.now();
    const disparador = await page.evaluate(() => document.activeElement?.outerHTML?.slice(0, 80));
    const panel = await abrirPanel(page, id, c);
    await esperarCalma(page);
    const msAbrir = Date.now() - t0;
    await medirPantalla(page, contexto, `panel-${id}`, { foco: contexto.tema === "claro" });
    await cerrarPanel(page);
    // ¿Vuelve el foco al control que abrió el panel?
    const focoTrasCerrar = await page.evaluate(() => {
      const el = document.activeElement;
      return el && el !== document.body ? { panel: el.getAttribute("data-panel"), titulo: el.getAttribute("title") } : null;
    });
    contexto.flujos[`abrir-${id}`] = { ...c.resumen(), ms: msAbrir, focoVuelve: Boolean(focoTrasCerrar), disparadorPrevio: disparador, presentacion: panel?.tipo ?? null };
  }
}

async function guardarEnExpediente(page, contexto) {
  const c = new Contador();
  const panel = await abrirPanel(page, "expediente", c);
  await clic(page, page.getByRole("button", { name: "Editar «Salario bruto anual»" }), c, "Pulsar el lápiz de «Salario bruto anual»");
  // La UI anterior le daba un id al campo; la de Primer lo nombra con su FormControl dentro del formulario.
  const campo = page
    .locator("#fact-rendimiento_trabajo_bruto")
    .or(page.getByRole("form", { name: "Editar «Salario bruto anual»" }).getByRole("textbox"))
    .first();
  await rellenar(campo, "36000", c, "Escribir el nuevo importe");
  await page.waitForTimeout(150);
  const raiz = panel.raiz ?? "[role=dialog]";
  await medirPantalla(page, contexto, "expediente-editando", { raiz });
  await clic(page, panel.locator.getByRole("button", { name: "Guardar", exact: true }), c, "Pulsar «Guardar»");
  await campo.waitFor({ state: "detached", timeout: 8000 });
  await esperarCalma(page);
  const guardado = await panel.locator.innerText();
  contexto.flujos["guardar-expediente"] = { ...c.resumen(), confirmado: /36[.\s]?000/.test(guardado) };
  await medirPantalla(page, contexto, "expediente-guardado", { raiz });
  await cerrarPanel(page);
}

async function verAvisos(page, contexto) {
  const c = new Contador();
  const pestana = page.locator(ENLACE_PANEL("avisos")).first();
  const insignia = (await pestana.isVisible().catch(() => false))
    ? await pestana.evaluate((el) => (el.textContent ?? "").replace(/\s+/g, " ").trim())
    : null;
  const panel = await abrirPanel(page, "avisos", c);
  const dialogo = panel.locator;
  await esperarCalma(page);
  const sinLeerAntes = await dialogo.getByRole("button", { name: /marcar como leído/i }).count();
  let marcar = dialogo.getByRole("button", { name: /leído|leida/i }).first();
  let marcado = false;
  // En la UI anterior, tras las correcciones, «Marcar como leído» estaba dentro de «Más acciones».
  if (!(await marcar.isVisible().catch(() => false))) {
    const mas = dialogo.getByRole("button", { name: /más acciones/i }).first();
    if (await mas.isVisible().catch(() => false)) {
      await clic(page, mas, c, "Abrir «Más acciones» del aviso");
      marcar = page.getByRole("menuitem", { name: /leído|leida/i }).or(page.getByRole("button", { name: /marcar como leído/i })).first();
    }
  }
  if (await marcar.isVisible().catch(() => false)) {
    await clic(page, marcar, c, "Marcar un aviso como leído");
    await esperarCalma(page);
    // Cuenta como marcado si el servidor lo apunta y la pantalla lo refleja (un botón «Marcar como leído» menos).
    const sinLeerDespues = await dialogo.getByRole("button", { name: /marcar como leído/i }).count();
    marcado = sinLeerDespues < sinLeerAntes;
  }
  const foco = await page.evaluate(() => {
    const el = document.activeElement;
    return el && el !== document.body ? (el.getAttribute("aria-label") || el.textContent || el.tagName).trim().slice(0, 80) : null;
  });
  contexto.flujos["ver-avisos"] = { ...c.resumen(), etiquetaInsignia: insignia, marcadoComoLeido: marcado, focoTrasMarcar: foco };
  await medirPantalla(page, contexto, "avisos-tras-leer");
  await cerrarPanel(page);
}

/** Solo con teclado: llegar a la caja, preguntar y abrir «Tu expediente»; cuenta pulsaciones. */
async function recorridoTeclado(browser, viewport) {
  const ctx = await nuevoContexto(browser, viewport, "claro");
  const page = await ctx.newPage();
  const r = { pulsaciones: 0, llegaALaCaja: false, envia: false, abrePanel: false, cierraConEscape: false, focoVuelve: false, saltoAlContenido: false };
  try {
    await page.goto(BASE_URL);
    await page.locator("input[type=email]").waitFor();
    // Entrar con teclado.
    for (let i = 0; i < 15 && !(await page.evaluate(() => document.activeElement?.type === "email")); i++) {
      await page.keyboard.press("Tab");
      r.pulsaciones++;
    }
    await page.keyboard.type("ana.lopez@example.com");
    await page.keyboard.press("Tab");
    await page.keyboard.type("una-clave-de-prueba");
    await page.keyboard.press("Enter");
    r.pulsaciones += 3;
    await page.getByRole("textbox", { name: "Escribe tu consulta" }).waitFor();
    await esperarCalma(page);
    await page.keyboard.press("Tab");
    r.pulsaciones++;
    r.saltoAlContenido = await page.evaluate(() => /saltar|ir al contenido/i.test(document.activeElement?.innerText ?? ""));
    for (let i = 0; i < 60; i++) {
      if (await page.evaluate(() => document.activeElement?.getAttribute("aria-label") === "Escribe tu consulta")) {
        r.llegaALaCaja = true;
        break;
      }
      await page.keyboard.press("Tab");
      r.pulsaciones++;
    }
    r.tabsHastaLaCaja = r.pulsaciones;
    if (r.llegaALaCaja) {
      await page.keyboard.type(PREGUNTA);
      await page.keyboard.press("Enter");
      r.pulsaciones++;
      r.envia = await page
        .getByRole("button", DETENER)
        .waitFor({ state: "detached", timeout: 20000 })
        .then(() => page.getByText("Sí, tienes que presentar").first().isVisible())
        .catch(() => false);
    }
    // Hasta el botón de «Tu expediente».
    await esperarCalma(page);
    const antes = r.pulsaciones;
    for (let i = 0; i < 80; i++) {
      await page.keyboard.press("Tab");
      r.pulsaciones++;
      if (await page.evaluate(() => document.activeElement?.getAttribute("data-panel") === "expediente")) {
        await page.keyboard.press("Enter");
        r.pulsaciones++;
        const abierto = await panelAbierto(page, "expediente", 5000).catch(() => null);
        r.abrePanel = abierto !== null;
        r.presentacion = abierto?.tipo ?? null;
        await page.waitForTimeout(500);
        break;
      }
    }
    r.tabsHastaExpediente = r.pulsaciones - antes;
    if (r.abrePanel && r.presentacion === "pagina") {
      // Una página no se cierra con Escape (como en GitHub): se vuelve a «Conversación» con Mayús+Tab e Intro.
      const antesDeCerrar = r.pulsaciones;
      for (let i = 0; i < 80; i++) {
        await page.keyboard.press("Shift+Tab");
        r.pulsaciones++;
        if (await page.evaluate(() => document.activeElement?.getAttribute("data-panel") === "conversacion")) {
          await page.keyboard.press("Enter");
          r.pulsaciones++;
          break;
        }
      }
      r.tabsHastaCerrar = r.pulsaciones - antesDeCerrar;
      await page.waitForTimeout(300);
      r.cierraConTeclado = (await page.locator(PAGINA_VISIBLE).count()) === 0;
      r.cierraConEscape = null;
      r.focoVuelve = await page.evaluate(() => document.activeElement?.getAttribute("data-panel") === "expediente");
    } else if (r.abrePanel) {
      await page.keyboard.press("Escape");
      r.pulsaciones++;
      await page.waitForTimeout(300);
      r.cierraConEscape = (await page.locator(HOJA_VISIBLE).count()) === 0;
      r.cierraConTeclado = r.cierraConEscape;
      r.focoVuelve = await page.evaluate(() => document.activeElement?.getAttribute("data-panel") === "expediente");
    }
  } catch (err) {
    r.error = String(err).slice(0, 300);
  }
  await ctx.close();
  return r;
}

/** Estados de carga, error y vacío de cada panel y del asesor (solo escritorio claro: es lógica, no estilo). */
async function medirEstados(browser, paneles) {
  const viewport = VIEWPORTS.find((v) => v.id === "escritorio") ?? VIEWPORTS[0];
  const ctx = await nuevoContexto(browser, viewport, "claro");
  const page = await ctx.newPage();
  await control({ reiniciar: true });
  await page.goto(BASE_URL);
  await page.locator("input[type=email]").fill("ana.lopez@example.com");
  await page.locator("input[type=password]").fill("una-clave-de-prueba");
  await page.locator("form button[type=submit]").click();
  const caja = page.getByRole("textbox", { name: "Escribe tu consulta" });
  await caja.waitFor();
  await caja.fill(PREGUNTA);
  await page.getByRole("button", ENVIAR).click();
  await page.getByRole("button", DETENER).waitFor({ state: "detached", timeout: 20000 });
  await esperarCalma(page);
  const sin = new Contador();
  const estados = {};
  const RE_CARGA = /cargando|consultando|comprobando|preparando|buscando|un momento|leyendo|actualizando/i;
  const RE_ERROR = /error|no se pudo|no está disponible|inténtalo|ha fallado|falló|no hay conexión/i;
  const RE_VACIO = /todavía no|aún no|no hay|no tengo nada|nada que|nada pendiente|ningún|ninguna|sin datos|vacío|empieza|cuando .* aparecer/i;
  const RE_REINTENTO = /reintentar|volver a intentar|reintenta|probar de nuevo|recargar/i;
  // Cada estado se mide como lo viviría alguien que abre la app con el servicio lento, caído o sin datos:
  // se fija el modo de la API, se recarga (la conversación y su caso siguen) y se abre el panel.
  // Peticiones a la API mientras se abre el panel: si no pide nada, no tiene estados de carga ni de error propios.
  let peticiones = 0;
  page.on("request", (req) => {
    if (new URL(req.url()).pathname.startsWith("/api/")) peticiones++;
  });
  async function abrirCon(modo, id) {
    await control({ modo, latenciaMs: 2500 });
    await page.reload();
    await caja.waitFor();
    // La UI anterior abría una consulta nueva al recargar: se vuelve a la que tiene el caso desde la lista.
    // Sin esperar a la red: con el servicio lento, los datos del panel tienen que llegar aún al abrirlo.
    await page.waitForTimeout(150);
    if (!(await page.getByText("Sí, tienes que presentar").first().isVisible().catch(() => false))) {
      const reciente = page
        .locator('nav[aria-label="Consultas recientes"] button, nav[aria-labelledby="titulo-consultas"] li :is(a, button):not([aria-label^="Eliminar"])')
        .first();
      await reciente.click();
      await page.waitForTimeout(150);
    }
    peticiones = 0;
    const panel = await abrirPanel(page, id, sin);
    return panel?.locator ?? null;
  }
  const RE_NO = { indicador: null, nota: "El panel no se ofrece en este estado del servicio" };
  for (const id of paneles) {
    const e = {};
    const d1 = await abrirCon("lento", id);
    if (!d1) {
      // Mientras el servicio no responde el panel no está en la navegación: se anota y se sigue.
      estados[id] = { carga: RE_NO, error: RE_NO, vacio: RE_NO, noSeOfrece: true };
      log(`  estados ${id}: no se ofrece mientras el servicio no responde`);
      continue;
    }
    await page.waitForTimeout(400);
    const textoCarga = await d1.innerText().catch(() => "");
    e.pideDatosAlAbrir = peticiones > 0;
    e.carga = {
      indicador:
        (await d1.locator("[role=status], [aria-busy=true], .animate-spin, .animate-pulse").count()) > 0 || RE_CARGA.test(textoCarga),
      texto: (textoCarga.match(RE_CARGA) ?? [null])[0],
    };
    await page.screenshot({ path: join(CAPTURAS, `estado-carga--panel-${id}.png`), animations: "disabled" });
    await cerrarPanel(page);
    const d2 = await abrirCon("error", id);
    if (!d2) {
      e.error = RE_NO;
    } else {
    await esperarCalma(page, 800);
    const textoError = await d2.innerText().catch(() => "");
    e.error = {
      indicador: (await d2.locator("[role=alert]").count()) > 0 || RE_ERROR.test(textoError),
      conRoleAlert: (await d2.locator("[role=alert]").count()) > 0,
      reintentar: RE_REINTENTO.test(textoError),
      texto: (textoError.split("\n").find((l) => RE_ERROR.test(l)) ?? null)?.slice(0, 160) ?? null,
    };
    await page.screenshot({ path: join(CAPTURAS, `estado-error--panel-${id}.png`), animations: "disabled" });
    await cerrarPanel(page);
    }
    const d3 = await abrirCon("vacio", id);
    if (!d3) {
      estados[id] = { ...e, vacio: RE_NO };
      continue;
    }
    await esperarCalma(page, 500);
    const textoVacio = await d3.innerText().catch(() => "");
    e.vacio = {
      explicado: RE_VACIO.test(textoVacio),
      texto: (textoVacio.split("\n").find((l) => RE_VACIO.test(l)) ?? null)?.slice(0, 160) ?? null,
      longitudTexto: textoVacio.length,
    };
    await page.screenshot({ path: join(CAPTURAS, `estado-vacio--panel-${id}.png`), animations: "disabled" });
    await cerrarPanel(page);
    estados[id] = e;
    log(`  estados ${id}: carga=${e.carga.indicador} error=${e.error.indicador} vacío=${e.vacio.explicado}`);
  }
  await control({ modo: "normal" });
  await page.reload();
  await caja.waitFor();
  await esperarCalma(page);
  // El asesor: carga (cola lenta) y error.
  await control({ modo: "lento", latenciaMs: 3000 });
  await caja.fill("¿Y si además alquilo un piso?");
  await page.getByRole("button", ENVIAR).click();
  await page.waitForTimeout(800);
  const textoCargaChat = await page.locator("main").innerText().catch(() => "");
  await page.screenshot({ path: join(CAPTURAS, "estado-carga--asesor.png"), animations: "disabled" });
  const cargaChat = { indicador: (await page.locator("main [role=status], main [aria-busy=true], main .animate-spin, main .animate-pulse").count()) > 0 || RE_CARGA.test(textoCargaChat) };
  await page.getByRole("button", DETENER).waitFor({ state: "detached", timeout: 30000 }).catch(() => undefined);
  await control({ modo: "error" });
  await caja.fill("¿Y la deducción por alquiler?");
  await page.getByRole("button", ENVIAR).click();
  await page.getByRole("button", DETENER).waitFor({ state: "detached", timeout: 30000 }).catch(() => undefined);
  await esperarCalma(page, 500);
  const textoErrorChat = await page.locator("main").innerText().catch(() => "");
  await page.screenshot({ path: join(CAPTURAS, "estado-error--asesor.png"), fullPage: true, animations: "disabled" });
  estados.asesor = {
    carga: cargaChat,
    error: {
      indicador: (await page.locator("[role=alert]").count()) > 0 || RE_ERROR.test(textoErrorChat),
      reintentar: RE_REINTENTO.test(textoErrorChat),
      texto: (textoErrorChat.split("\n").find((l) => /no está disponible|error|no se pudo/i.test(l)) ?? null)?.slice(0, 160) ?? null,
    },
    vacio: { explicado: true, texto: "La pantalla de inicio sin consultas (ver captura inicio--*)" },
  };
  await control({ modo: "normal" });
  await ctx.close();
  return estados;
}

async function nuevoContexto(browser, viewport, tema) {
  const ctx = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    deviceScaleFactor: viewport.deviceScaleFactor,
    isMobile: viewport.isMobile,
    hasTouch: viewport.hasTouch,
    colorScheme: tema === "oscuro" ? "dark" : "light",
    locale: "es-ES",
    timezoneId: "Europe/Madrid",
    serviceWorkers: "block",
    reducedMotion: "reduce",
  });
  await ctx.addInitScript(INIT_SCRIPT(tema));
  ctx.setDefaultTimeout(15000);
  return ctx;
}

// ---------- Lighthouse ----------
async function medirLighthouse(paneles) {
  const { default: lighthouse } = await import("lighthouse");
  const { default: configEscritorio } = await import("lighthouse/core/config/desktop-config.js");
  const puerto = 9334;
  const urls = [
    { pantalla: "entrar", url: `${BASE_URL}/`, cookie: false },
    { pantalla: "inicio", url: `${BASE_URL}/`, cookie: true },
    ...paneles.map((id) => ({ pantalla: `panel-${id}`, url: `${BASE_URL}/?panel=${id}`, cookie: true })),
  ];
  // Un Chromium nuevo por ejecución (más lento, pero un fallo no arrastra a las siguientes) y un reintento.
  async function unaVez(u, vp) {
    const browser = await chromium.launch({ executablePath: rutaChromium(), args: [`--remote-debugging-port=${puerto}`] });
    try {
      await control({ reiniciar: true });
      const flags = {
        port: puerto,
        output: "json",
        logLevel: "error",
        onlyCategories: ["performance", "accessibility", "best-practices"],
        extraHeaders: u.cookie ? { Cookie: "sesion_simulada=1" } : undefined,
        screenEmulation: { mobile: vp.isMobile, width: vp.width, height: vp.height, deviceScaleFactor: vp.deviceScaleFactor, disabled: false },
        formFactor: vp.isMobile ? "mobile" : "desktop",
      };
      const r = await lighthouse(u.url, flags, vp.isMobile ? undefined : configEscritorio);
      if (r.lhr.runtimeError) throw new Error(r.lhr.runtimeError.code);
      return r;
    } finally {
      await browser.close().catch(() => undefined);
    }
  }
  const salida = [];
  for (const vp of VIEWPORTS) {
    for (const u of urls) {
      let r = null;
      let ultimoError = null;
      for (let intento = 0; intento < 2 && !r; intento++) {
        try {
          r = await unaVez(u, vp);
        } catch (err) {
          ultimoError = err;
          log(`  lighthouse ${u.pantalla} ${vp.id}: fallo (${String(err).slice(0, 80)}), intento ${intento + 1}`);
        }
      }
      if (!r) {
        salida.push({ pantalla: u.pantalla, viewport: vp.id, error: String(ultimoError).slice(0, 200) });
        continue;
      }
      const a = r.lhr.audits;
      const fila = {
        pantalla: u.pantalla,
        viewport: vp.id,
        rendimiento: Math.round((r.lhr.categories.performance?.score ?? 0) * 100),
        accesibilidad: Math.round((r.lhr.categories.accessibility?.score ?? 0) * 100),
        buenasPracticas: Math.round((r.lhr.categories["best-practices"]?.score ?? 0) * 100),
        fcpMs: Math.round(a["first-contentful-paint"]?.numericValue ?? NaN),
        lcpMs: Math.round(a["largest-contentful-paint"]?.numericValue ?? NaN),
        cls: Math.round((a["cumulative-layout-shift"]?.numericValue ?? NaN) * 1000) / 1000,
        tbtMs: Math.round(a["total-blocking-time"]?.numericValue ?? NaN),
        siMs: Math.round(a["speed-index"]?.numericValue ?? NaN),
        ttiMs: Math.round(a["interactive"]?.numericValue ?? NaN),
        bytesTotales: a["total-byte-weight"]?.numericValue ?? null,
        jsSinUsarBytes: a["unused-javascript"]?.details?.overallSavingsBytes ?? 0,
        lcpElemento: a["largest-contentful-paint-element"]?.details?.items?.[0]?.items?.[0]?.node?.snippet?.slice(0, 120) ?? null,
        clsElementos: (a["layout-shifts"]?.details?.items ?? []).slice(0, 3).map((i) => i.node?.snippet?.slice(0, 100) ?? null),
      };
      salida.push(fila);
      log(`  lighthouse ${u.pantalla} ${vp.id}: perf ${fila.rendimiento} LCP ${fila.lcpMs} ms CLS ${fila.cls} TBT ${fila.tbtMs} ms`);
    }
  }
  return salida;
}

// ---------- análisis ----------
const RE_JERGA = /\b(LLM|tokens?|corpus|fragmentos?|API|SSE|JSON|endpoint|backend|prompt|USD|PWA|push|guion|proveedor|modelo de lenguaje|ids?|caso|hechos?|determinista|síntesis|especialistas?)\b/i;
const RE_GENERICO = /^(ok|aceptar|ver|más|abrir|ir|continuar|aquí|sí|no|vale|hecho|listo)$/i;
const RE_INGLES = /\b(the|loading|submit|settings|click|save|cancel|error code|undefined|null|NaN)\b/;

function analizarTextos(pantallas) {
  const vistos = new Map();
  for (const p of pantallas) {
    for (const t of p.textos) {
      const texto = t.texto || t.ariaLabel || t.placeholder || "";
      const clave = `${t.tipo}|${texto}`;
      if (!vistos.has(clave)) vistos.set(clave, { ...t, pantallas: new Set() });
      vistos.get(clave).pantallas.add(p.clave);
    }
  }
  const hallazgos = [];
  for (const t of vistos.values()) {
    const visible = t.texto;
    const nombre = t.ariaLabel || visible;
    const donde = [...t.pantallas].slice(0, 3).join(", ");
    const esControl = ["button", "a", "tab", "menuitem"].includes(t.tipo);
    if (esControl && !visible && !t.ariaLabel && !t.title) hallazgos.push({ motivo: "Control sin texto ni nombre accesible", texto: "(vacío)", donde });
    if (esControl && !visible && t.ariaLabel) hallazgos.push({ motivo: "Solo icono (depende de aria-label/tooltip)", texto: t.ariaLabel, donde });
    if (esControl && visible && RE_GENERICO.test(visible)) hallazgos.push({ motivo: "Etiqueta genérica", texto: visible, donde });
    if (esControl && nombre && nombre.length > 60) hallazgos.push({ motivo: "Nombre de control muy largo", texto: nombre, donde });
    if (visible && RE_JERGA.test(visible)) hallazgos.push({ motivo: "Jerga técnica o interna", texto: visible.slice(0, 120), donde });
    if (visible && RE_INGLES.test(visible)) hallazgos.push({ motivo: "Texto en inglés o valor técnico", texto: visible.slice(0, 120), donde });
    if (esControl && visible && t.ariaLabel && !t.ariaLabel.toLowerCase().includes(visible.toLowerCase().split(" ")[0])) {
      hallazgos.push({ motivo: "La etiqueta visible no coincide con el nombre accesible (WCAG 2.5.3)", texto: `«${visible}» / «${t.ariaLabel}»`, donde });
    }
  }
  const porMotivo = {};
  for (const h of hallazgos) porMotivo[h.motivo] = (porMotivo[h.motivo] ?? 0) + 1;
  return { total: hallazgos.length, porMotivo, hallazgos };
}

function agregarInventario(pantallas) {
  const total = {};
  for (const p of pantallas) {
    for (const [prop, mapa] of Object.entries(p.inventario)) {
      total[prop] ??= {};
      for (const [valor, n] of Object.entries(mapa)) total[prop][valor] = (total[prop][valor] ?? 0) + n;
    }
  }
  const resumen = {};
  for (const [prop, mapa] of Object.entries(total)) {
    resumen[prop] = { distintos: Object.keys(mapa).length, valores: Object.entries(mapa).sort((a, b) => b[1] - a[1]) };
  }
  return resumen;
}

function resumirAxe(pantallas) {
  const porImpacto = { critical: 0, serious: 0, moderate: 0, minor: 0 };
  const porRegla = {};
  for (const p of pantallas) {
    for (const v of p.violaciones) {
      porImpacto[v.impacto] = (porImpacto[v.impacto] ?? 0) + v.nodos;
      porRegla[v.id] ??= { id: v.id, impacto: v.impacto, ayuda: v.ayuda, nodos: 0, pantallas: new Set(), ejemplo: v.ejemplos[0] };
      porRegla[v.id].nodos += v.nodos;
      porRegla[v.id].pantallas.add(p.clave);
    }
  }
  const reglas = Object.values(porRegla)
    .map((r) => ({ ...r, pantallas: [...r.pantallas] }))
    .sort((a, b) => b.nodos - a.nodos);
  return { porImpacto, reglas, contraste: porRegla["color-contrast"]?.nodos ?? 0 };
}

// ---------- principal ----------
async function principal() {
  const t0 = Date.now();
  rmSync(CAPTURAS, { recursive: true, force: true });
  mkdirSync(CAPTURAS, { recursive: true });
  mkdirSync(RESULTADOS, { recursive: true });

  if (!bandera("sin-build")) {
    log("Construyendo el frontend (vite build)…");
    await ejecutar("npx", ["vite", "build", "--logLevel", "warn"]);
  }
  const bundle = medirBundle();
  log(`Bundle: ${bundle.totales.numeroChunks} chunks JS, inicial ${(bundle.totales.inicialGzip / 1024).toFixed(1)} KiB gzip`);

  // Otra medición (u otra sesión) en el mismo puerto haría medir su build, no este: mejor parar.
  const ocupado = await fetch(BASE_URL).then(() => true, () => false);
  if (ocupado) throw new Error(`El puerto ${PUERTO_WEB} ya está en uso: define MEDIR_PUERTO_WEB y MEDIR_PUERTO_API con otros libres.`);
  const api = await arrancarApiSimulada(PUERTO_API);
  const web = spawn("npx", ["vite", "preview", "--port", String(PUERTO_WEB), "--strictPort", "--host", "localhost"], {
    cwd: FRONTEND,
    env: { ...process.env, VITE_API_PROXY: API_URL },
    stdio: "ignore",
  });
  const resultados = { nombre: NOMBRE, fecha: new Date().toISOString(), viewports: VIEWPORTS.map((v) => v.id), temas: TEMAS, bundle, contextos: [] };
  try {
    await esperarUrl(BASE_URL);
    log(`vite preview en ${BASE_URL} → API simulada en ${API_URL}`);
    const browser = await chromium.launch({ executablePath: rutaChromium() });
    resultados.navegador = browser.version();
    try {
      let paneles = [];
      for (const viewport of VIEWPORTS) {
        for (const tema of TEMAS) {
          log(`Contexto ${viewport.id} (${viewport.width}×${viewport.height}), tema ${tema}`);
          await control({ reiniciar: true });
          const ctx = await nuevoContexto(browser, viewport, tema);
          const page = await ctx.newPage();
          const errores = [];
          page.on("pageerror", (e) => errores.push(String(e).slice(0, 200)));
          // Los 4xx esperados (sesión sin iniciar, repaso inexistente) los pinta Chrome como «Failed to load resource».
          page.on("console", (m) => m.type() === "error" && !m.text().startsWith("Failed to load resource") && errores.push(m.text().slice(0, 200)));
          const contexto = { viewport, tema, pantallas: [], flujos: {}, errores };
          contextoActual = contexto;
          try {
            await entrar(page, contexto);
            await preguntar(page, contexto);
            await recorrerPaneles(page, contexto);
            await guardarEnExpediente(page, contexto);
            await verAvisos(page, contexto);
          } catch (err) {
            contexto.fallo = String(err).slice(0, 500);
            log(`  FALLO: ${contexto.fallo}`);
            await page.screenshot({ path: join(CAPTURAS, `fallo--${viewport.id}--${tema}.png`) }).catch(() => undefined);
          }
          contexto.tokens = await page.evaluate(tokensDeColor).catch(() => ({}));
          if (contexto.navegacion) paneles = [...new Set([...paneles, ...contexto.navegacion.enBarra, ...contexto.navegacion.enMenu])];
          resultados.contextos.push({ ...contexto, viewport: viewport.id });
          await ctx.close();
        }
      }
      log("Recorrido solo con teclado…");
      resultados.teclado = {};
      for (const vp of VIEWPORTS) resultados.teclado[vp.id] = await recorridoTeclado(browser, vp);
      if (!bandera("sin-estados")) {
        log("Estados de carga, error y vacío…");
        resultados.estados = await medirEstados(browser, paneles);
      }
      resultados.paneles = paneles;
    } finally {
      await browser.close();
    }
    writeFileSync(join(RESULTADOS, `${NOMBRE}.parcial.json`), JSON.stringify(resultados));
    if (!bandera("sin-lighthouse")) {
      log("Lighthouse (de uno en uno)…");
      resultados.lighthouse = await medirLighthouse(resultados.paneles);
    }
    resultados.rutasNoSimuladas = await fetch(`${API_URL}/__peticiones`).then((r) => r.json());
  } finally {
    web.kill();
    api.close();
  }

  const todas = resultados.contextos.flatMap((c) => c.pantallas.map((p) => ({ ...p, viewport: c.viewport, tema: c.tema })));
  resultados.analisis = {
    axe: resumirAxe(todas),
    estilos: agregarInventario(todas),
    estilosPorTema: Object.fromEntries(TEMAS.map((t) => [t, agregarInventario(todas.filter((p) => p.tema === t))])),
    textos: analizarTextos(todas),
  };
  // Las listas de textos por pantalla ya están analizadas: fuera del JSON para que sea legible.
  for (const c of resultados.contextos) for (const p of c.pantallas) delete p.textos;
  resultados.duracionS = Math.round((Date.now() - t0) / 1000);
  writeFileSync(join(RESULTADOS, `${NOMBRE}.json`), JSON.stringify(resultados, null, 1));
  writeFileSync(join(RESULTADOS, `${NOMBRE}.md`), informe(resultados));
  rmSync(join(RESULTADOS, `${NOMBRE}.parcial.json`), { force: true });
  log(`Resultados en medicion/resultados/${NOMBRE}.json y ${NOMBRE}.md (${resultados.duracionS} s)`);

  if (COMPARAR) {
    const ruta = join(RESULTADOS, `${COMPARAR}.json`);
    if (!existsSync(ruta)) throw new Error(`No existe ${ruta}`);
    const antes = JSON.parse(readFileSync(ruta, "utf8"));
    const tabla = compararResultados(antes, resultados);
    const destino = join(RESULTADOS, `comparacion-${COMPARAR}-${NOMBRE}.md`);
    writeFileSync(destino, tabla);
    console.log(`\n${tabla}`);
    log(`Tabla en medicion/resultados/comparacion-${COMPARAR}-${NOMBRE}.md`);
  }
  const fallos = resultados.contextos.filter((c) => c.fallo);
  if (fallos.length) {
    log(`${fallos.length} contexto(s) con fallos: revisa capturas/${NOMBRE}/fallo--*.png`);
    process.exitCode = 1;
  }
}

// Lighthouse deja a veces promesas rechazadas sin capturar (TARGET_CRASHED): que no tumben la medición.
process.on("unhandledRejection", (err) => log(`Rechazo sin capturar (ignorado): ${String(err).slice(0, 160)}`));

if (process.argv.includes("--solo-comparar")) {
  // Compara dos resultados ya guardados sin volver a medir: --solo-comparar base despues
  const i = process.argv.indexOf("--solo-comparar");
  const [a, b] = [process.argv[i + 1], process.argv[i + 2]];
  const leer = (n) => JSON.parse(readFileSync(join(RESULTADOS, `${n}.json`), "utf8"));
  console.log(compararResultados(leer(a), leer(b)));
} else {
  await principal();
}

