// Cifras resumen de una medición y tabla de antes y después entre dos mediciones.

const mediana = (lista) => {
  const v = lista.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
};
const maximo = (lista) => {
  const v = lista.filter((x) => Number.isFinite(x));
  return v.length ? Math.max(...v) : null;
};

/** Las cifras que se comparan: [grupo, nombre, valor, sentido] con sentido -1 = menos es mejor. */
export function cifras(r) {
  const filas = [];
  const add = (grupo, nombre, valor, sentido = -1, unidad = "") => filas.push({ grupo, nombre, valor, sentido, unidad });
  const lh = (r.lighthouse ?? []).filter((f) => !f.error);
  for (const vp of r.viewports) {
    const de = lh.filter((f) => f.viewport === vp);
    if (!de.length) continue;
    add("Rendimiento", `Lighthouse rendimiento (mediana, ${vp})`, mediana(de.map((f) => f.rendimiento)), 1);
    add("Rendimiento", `LCP mediana (${vp})`, mediana(de.map((f) => f.lcpMs)), -1, "ms");
    add("Rendimiento", `LCP peor pantalla (${vp})`, maximo(de.map((f) => f.lcpMs)), -1, "ms");
    add("Rendimiento", `CLS peor pantalla (${vp})`, maximo(de.map((f) => f.cls)), -1);
    add("Rendimiento", `TBT mediana (${vp})`, mediana(de.map((f) => f.tbtMs)), -1, "ms");
    add("Accesibilidad", `Lighthouse accesibilidad (mediana, ${vp})`, mediana(de.map((f) => f.accesibilidad)), 1);
  }
  for (const vp of r.viewports) {
    const inp = r.contextos.filter((c) => c.viewport === vp).flatMap((c) => c.pantallas.map((p) => p.inpMs));
    add("Rendimiento", `INP aproximado, peor interacción (${vp})`, maximo(inp), -1, "ms");
    const pregunta = r.contextos.filter((c) => c.viewport === vp).map((c) => c.flujos.preguntar?.msPrimerTexto);
    add("Rendimiento", `Primer texto del asesor tras «Enviar» (${vp})`, mediana(pregunta), -1, "ms");
  }
  const t = r.bundle.totales;
  add("Bundle", "JS total (gzip)", Math.round(t.jsGzip / 102.4) / 10, -1, "KiB");
  add("Bundle", "Carga inicial JS+CSS (gzip)", Math.round(t.inicialGzip / 102.4) / 10, -1, "KiB");
  add("Bundle", "CSS (gzip)", Math.round(t.cssGzip / 102.4) / 10, -1, "KiB");
  add("Bundle", "Chunks JS", t.numeroChunks, 0);

  const axe = r.analisis.axe;
  for (const imp of ["critical", "serious", "moderate", "minor"]) add("Accesibilidad", `Nodos con violaciones axe: ${imp}`, axe.porImpacto[imp] ?? 0);
  add("Accesibilidad", "Reglas axe distintas incumplidas", axe.reglas.length);
  add("Accesibilidad", "Nodos con contraste insuficiente", axe.contraste);
  const focos = r.contextos.flatMap((c) => c.pantallas.map((p) => p.foco).filter(Boolean));
  add("Accesibilidad", "Paradas de foco sin indicador visible", focos.reduce((s, f) => s + f.sinIndicador.length, 0));
  add("Accesibilidad", "Saltos atrás en el orden del foco", focos.reduce((s, f) => s + f.saltosAtras, 0));
  add("Accesibilidad", "Foco que se escapa de un diálogo modal", focos.reduce((s, f) => s + (f.escapaDelDialogo ?? 0), 0));
  add("Accesibilidad", "Paradas de foco fuera de pantalla", focos.reduce((s, f) => s + f.fueraDePantalla, 0));
  const flujosAbrir = r.contextos.flatMap((c) => Object.entries(c.flujos).filter(([k]) => k.startsWith("abrir-")).map(([, v]) => v));
  add("Accesibilidad", "Paneles que no devuelven el foco al cerrar", flujosAbrir.filter((f) => !f.focoVuelve).length);
  for (const [vp, tk] of Object.entries(r.teclado ?? {})) {
    add("Accesibilidad", `Teclado: Tab hasta la caja de consulta (${vp})`, tk.tabsHastaLaCaja ?? null);
    add("Accesibilidad", `Teclado: Tab hasta «Tu expediente» (${vp})`, tk.tabsHastaExpediente ?? null);
    add("Accesibilidad", `Teclado: flujo completo sin ratón (${vp})`, tk.envia && tk.abrePanel && (tk.cierraConTeclado ?? tk.cierraConEscape) ? 1 : 0, 1);
  }
  add("Accesibilidad", "Controles con solo icono", r.analisis.textos.porMotivo["Solo icono (depende de aria-label/tooltip)"] ?? 0);

  const e = r.analisis.estilos;
  const n = (k) => e[k]?.distintos ?? 0;
  add("Consistencia visual", "Tamaños de fuente distintos", n("tamanoFuente"));
  add("Consistencia visual", "Pesos de fuente distintos", n("pesoFuente"));
  add("Consistencia visual", "Alturas de línea distintas", n("alturaLinea"));
  add("Consistencia visual", "Valores de espaciado distintos", n("espaciado"));
  add("Consistencia visual", "Radios de borde distintos", n("radio"));
  add("Consistencia visual", "Sombras distintas", n("sombra"));
  add("Consistencia visual", "Alturas de botón distintas", n("alturaBoton"));
  add("Consistencia visual", "Alturas de campo distintas", n("alturaCampo"));
  for (const vp of r.viewports) {
    const t = {};
    for (const c of r.contextos.filter((x) => x.viewport === vp && x.tema === "claro"))
      for (const p of c.pantallas) for (const [k, v] of Object.entries(p.inventario.objetivoTactil ?? {})) t[k] = (t[k] ?? 0) + v;
    add("Accesibilidad", `Controles de menos de 24 px (${vp}, WCAG 2.5.8)`, t["<24px"] ?? 0);
    if (vp === "movil") add("Accesibilidad", "Controles de menos de 44 px en móvil", (t["<24px"] ?? 0) + (t["24-43px"] ?? 0));
  }
  for (const tema of r.temas) {
    const et = r.analisis.estilosPorTema[tema];
    add("Consistencia visual", `Colores de texto distintos (${tema})`, et.colorTexto?.distintos ?? 0);
    add("Consistencia visual", `Colores de fondo distintos (${tema})`, et.colorFondo?.distintos ?? 0);
    add("Consistencia visual", `Colores de borde distintos (${tema})`, et.colorBorde?.distintos ?? 0);
  }

  for (const vp of r.viewports) {
    const c = r.contextos.find((x) => x.viewport === vp && x.tema === "claro") ?? r.contextos.find((x) => x.viewport === vp);
    if (!c) continue;
    const f = c.flujos;
    const pasos = (k) => (f[k] ? f[k].clics + f[k].campos : null);
    add("UX", `Entrar: clics + campos (${vp})`, pasos("entrar"));
    add("UX", `Preguntar: clics + campos (${vp})`, pasos("preguntar"));
    add("UX", `Guardar un dato en el expediente: clics + campos (${vp})`, pasos("guardar-expediente"));
    add("UX", `Ver avisos y marcar uno: clics (${vp})`, f["ver-avisos"]?.clics ?? null);
    add("UX", `Ver avisos: el aviso queda marcado como leído (${vp})`, f["ver-avisos"] ? (f["ver-avisos"].marcadoComoLeido ? 1 : 0) : null, 1);
    const abrir = Object.entries(f).filter(([k]) => k.startsWith("abrir-"));
    add("UX", `Clics medios para abrir un panel (${vp})`, abrir.length ? Math.round((abrir.reduce((s, [, v]) => s + v.clics, 0) / abrir.length) * 100) / 100 : null);
    add("UX", `Paneles escondidos tras «Más» (${vp})`, c.navegacion?.enMenu?.length ?? null);
  }
  if (r.estados) {
    // Los formularios que no piden nada al abrirse (optimizador, planificar) no tienen carga ni error propios.
    const lista = Object.values(r.estados).filter((s) => !s.noSeOfrece && !(s.pideDatosAlAbrir === false && !s.carga?.indicador && !s.error?.indicador));
    add("UX", "Pantallas sin indicador de carga", lista.filter((s) => !s.carga?.indicador).length);
    add("UX", "Pantallas sin mensaje de error", lista.filter((s) => !s.error?.indicador).length);
    add("UX", "Errores sin opción de reintentar", lista.filter((s) => !s.error?.reintentar).length);
    add("UX", "Pantallas vacías sin explicación", lista.filter((s) => !s.vacio?.explicado).length);
  }
  add("UX", "Textos poco claros detectados", r.analisis.textos.total);
  add("UX", "Errores de consola durante el recorrido", r.contextos.reduce((s, c) => s + c.errores.length, 0));
  const orden = ["Rendimiento", "Bundle", "Accesibilidad", "Consistencia visual", "UX"];
  return filas
    .map((f, i) => ({ ...f, i, nombre: f.nombre.replace(/\bmovil\b/g, "móvil") }))
    .sort((a, b) => orden.indexOf(a.grupo) - orden.indexOf(b.grupo) || a.i - b.i);
}

const fmt = (v, unidad) => (v === null || v === undefined || Number.isNaN(v) ? "—" : `${typeof v === "number" ? Math.round(v * 1000) / 1000 : v}${unidad ? ` ${unidad}` : ""}`);

export function compararResultados(antes, despues) {
  const a = cifras(antes);
  const d = new Map(cifras(despues).map((f) => [`${f.grupo}|${f.nombre}`, f]));
  const lineas = [
    `# Antes y después: ${antes.nombre} → ${despues.nombre}`,
    "",
    `Medición «${antes.nombre}» del ${antes.fecha.slice(0, 10)} frente a «${despues.nombre}» del ${despues.fecha.slice(0, 10)}.`,
    "Flecha ▲ = mejora, ▼ = empeora, = sin cambios. En las cifras de Lighthouse «más es mejor»; en el resto, «menos es mejor» salvo que se indique.",
    "",
  ];
  let grupo = null;
  for (const f of a) {
    if (f.grupo !== grupo) {
      grupo = f.grupo;
      lineas.push(`\n## ${grupo}\n`, "| Cifra | Antes | Después | Diferencia | |", "|---|---:|---:|---:|:-:|");
    }
    const g = d.get(`${f.grupo}|${f.nombre}`);
    const vd = g?.valor ?? null;
    let dif = "—";
    let marca = "";
    if (typeof f.valor === "number" && typeof vd === "number") {
      const delta = vd - f.valor;
      dif = `${delta > 0 ? "+" : ""}${Math.round(delta * 1000) / 1000}`;
      if (f.valor !== 0) dif += ` (${delta > 0 ? "+" : ""}${Math.round((delta / Math.abs(f.valor)) * 100)} %)`;
      marca = delta === 0 || f.sentido === 0 ? "=" : Math.sign(delta) === f.sentido ? "▲" : "▼";
    }
    lineas.push(`| ${f.nombre} | ${fmt(f.valor, f.unidad)} | ${fmt(vd, f.unidad)} | ${dif} | ${marca} |`);
  }
  return `${lineas.join("\n")}\n`;
}
