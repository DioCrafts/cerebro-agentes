// Código que se ejecuta DENTRO de la página (page.evaluate): inventario de estilos, tokens de color,
// textos visibles y descriptor del elemento con el foco. Sin dependencias: se serializa con toString().

export function inventarioEstilos(selectorRaiz) {
  const raiz = (selectorRaiz && document.querySelector(selectorRaiz)) || document.body;
  const lienzo = document.createElement("canvas");
  lienzo.width = lienzo.height = 1;
  const g = lienzo.getContext("2d", { willReadFrequently: true });
  const cache = new Map();
  // Cualquier formato (rgb, oklab, color-mix…) a #rrggbb[aa], pintándolo en un píxel.
  function color(valor) {
    if (!valor || valor === "transparent" || valor === "rgba(0, 0, 0, 0)") return null;
    if (cache.has(valor)) return cache.get(valor);
    g.clearRect(0, 0, 1, 1);
    g.fillStyle = "#000";
    g.fillStyle = valor;
    g.fillRect(0, 0, 1, 1);
    const [r, gr, b, a] = g.getImageData(0, 0, 1, 1).data;
    const hex = (n) => n.toString(16).padStart(2, "0");
    const salida = a === 0 ? null : `#${hex(r)}${hex(gr)}${hex(b)}${a < 255 ? hex(a) : ""}`;
    cache.set(valor, salida);
    return salida;
  }
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none" && Number(cs.opacity) > 0;
  };
  const inv = {
    tamanoFuente: {},
    pesoFuente: {},
    alturaLinea: {},
    familia: {},
    espaciado: {},
    radio: {},
    sombra: {},
    alturaBoton: {},
    alturaCampo: {},
    objetivoTactil: {},
    colorTexto: {},
    colorFondo: {},
    colorBorde: {},
  };
  const sumar = (mapa, clave) => {
    if (clave === null || clave === undefined || clave === "") return;
    mapa[clave] = (mapa[clave] ?? 0) + 1;
  };
  // Tailwind 4 apila sombras y anillos transparentes en todos los elementos: solo cuentan las capas visibles.
  const sombra = (v) => {
    if (!v || v === "none") return null;
    const capas = v.split(/,(?![^(]*\))/).map((c) => c.trim()).filter((c) => !/rgba\([^)]*,\s*0\)/.test(c) && !/^transparent/.test(c));
    return capas.length ? capas.join(", ") : null;
  };
  const radio = (v) => (parseFloat(v) >= 999 ? "pill (≥999px)" : px(v));
  const px = (v) => (v && v.endsWith("px") && parseFloat(v) !== 0 ? `${Math.round(parseFloat(v) * 10) / 10}px` : null);
  let elementos = 0;
  for (const el of raiz.querySelectorAll("*")) {
    if (el.closest("svg") && el.tagName.toLowerCase() !== "svg") continue;
    if (!visible(el)) continue;
    elementos++;
    const cs = getComputedStyle(el);
    const conTexto = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (conTexto) {
      sumar(inv.tamanoFuente, px(cs.fontSize));
      sumar(inv.pesoFuente, cs.fontWeight);
      sumar(inv.alturaLinea, cs.lineHeight === "normal" ? "normal" : px(cs.lineHeight));
      sumar(inv.familia, cs.fontFamily.split(",")[0].replace(/["']/g, "").trim());
      sumar(inv.colorTexto, color(cs.color));
    }
    for (const p of ["paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "marginTop", "marginRight", "marginBottom", "marginLeft", "rowGap", "columnGap"]) {
      sumar(inv.espaciado, px(cs[p]));
    }
    sumar(inv.radio, radio(cs.borderTopLeftRadius));
    sumar(inv.sombra, sombra(cs.boxShadow));
    sumar(inv.colorFondo, color(cs.backgroundColor));
    for (const lado of ["Top", "Right", "Bottom", "Left"]) {
      if (parseFloat(cs[`border${lado}Width`]) > 0 && cs[`border${lado}Style`] !== "none") {
        sumar(inv.colorBorde, color(cs[`border${lado}Color`]));
        break;
      }
    }
    const caja = el.getBoundingClientRect();
    const alto = `${Math.round(caja.height)}px`;
    // Fuera los fondos que cierran un panel al pulsarlos (botones a pantalla completa) y los campos ocultos.
    const fondo = caja.height > window.innerHeight * 0.6;
    const esBoton = el.matches("button, [role=button], [role=tab], [role=menuitem], a[class*='rounded'], input[type=submit], input[type=button]");
    if (esBoton && !fondo) sumar(inv.alturaBoton, alto);
    else if (!esBoton && caja.height >= 8 && el.matches("input:not([type=checkbox]):not([type=radio]):not([type=hidden]), select, textarea")) sumar(inv.alturaCampo, alto);
    if (!fondo && el.matches("button, a[href], [role=button], [role=tab], [role=menuitem], input, select, textarea, summary")) {
      const lado = Math.min(caja.width, caja.height);
      sumar(inv.objetivoTactil, lado < 24 ? "<24px" : lado < 44 ? "24-43px" : "≥44px");
    }
  }
  return { elementos, inv };
}

/** Las variables CSS que definen las hojas de estilo, resueltas en <html> con el tema actual. */
export function tokensDeColor() {
  const nombres = new Set();
  const recorrer = (reglas) => {
    for (const regla of reglas) {
      if (regla.cssRules) recorrer(regla.cssRules);
      if (regla.style) for (const prop of regla.style) if (prop.startsWith("--")) nombres.add(prop);
    }
  };
  for (const hoja of document.styleSheets) {
    try {
      recorrer(hoja.cssRules);
    } catch {
      // hoja de otro origen
    }
  }
  const lienzo = document.createElement("canvas");
  lienzo.width = lienzo.height = 1;
  const g = lienzo.getContext("2d", { willReadFrequently: true });
  const aHex = (valor) => {
    const probe = document.createElement("i");
    probe.style.color = "";
    probe.style.color = valor;
    if (!probe.style.color) return null;
    g.clearRect(0, 0, 1, 1);
    g.fillStyle = "#000";
    g.fillStyle = valor;
    g.fillRect(0, 0, 1, 1);
    const [r, gr, b, a] = g.getImageData(0, 0, 1, 1).data;
    const hex = (n) => n.toString(16).padStart(2, "0");
    return `#${hex(r)}${hex(gr)}${hex(b)}${a < 255 ? hex(a) : ""}`;
  };
  const cs = getComputedStyle(document.documentElement);
  const tokens = {};
  for (const nombre of [...nombres].sort()) {
    const crudo = cs.getPropertyValue(nombre).trim();
    if (!crudo) continue;
    // Tripletes «r g b» (tokens para usar con rgb(var(--x) / alfa)).
    const valor = /^\d{1,3}\s+\d{1,3}\s+\d{1,3}$/.test(crudo) ? `rgb(${crudo})` : crudo;
    const hex = aHex(valor);
    if (hex) tokens[nombre] = { hex, crudo };
  }
  return tokens;
}

/** Textos visibles de controles, títulos y etiquetas, para revisar si se entienden. */
export function textosVisibles(selectorRaiz) {
  const raiz = (selectorRaiz && document.querySelector(selectorRaiz)) || document.body;
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
  };
  const salida = [];
  for (const el of raiz.querySelectorAll("button, a[href], [role=button], [role=tab], [role=menuitem], label, h1, h2, h3, h4, input[placeholder], textarea[placeholder], [role=alert], [role=status]")) {
    if (!visible(el)) continue;
    salida.push({
      tipo: el.getAttribute("role") || el.tagName.toLowerCase(),
      texto: (el.innerText || "").replace(/\s+/g, " ").trim().slice(0, 200),
      ariaLabel: el.getAttribute("aria-label"),
      title: el.getAttribute("title"),
      placeholder: el.getAttribute("placeholder"),
    });
  }
  return salida;
}

/** El elemento con el foco: qué es, dónde está y si se ve el indicador de foco. */
export function descriptorFoco() {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return { tipo: "body" };
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const indicador =
    (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) ||
    (cs.boxShadow && cs.boxShadow !== "none") ||
    // Un indicador que dibuja el contenedor (p. ej. el compositor con focus-within).
    (() => {
      const padre = el.parentElement?.closest("*:focus-within");
      if (!padre || padre === document.body) return false;
      const pcs = getComputedStyle(padre);
      return (pcs.outlineStyle !== "none" && parseFloat(pcs.outlineWidth) > 0) || (pcs.boxShadow && pcs.boxShadow !== "none");
    })();
  // Solo el diálogo modal a la vista: las hojas laterales cerradas siguen montadas, ocultas.
  const dialogo = [...document.querySelectorAll("[role=dialog][aria-modal=true]")].reverse().find((d) => d.getClientRects().length > 0) ?? null;
  return {
    tipo: el.getAttribute("role") || el.tagName.toLowerCase(),
    nombre: (el.getAttribute("aria-label") || el.innerText || el.getAttribute("placeholder") || el.getAttribute("title") || "").replace(/\s+/g, " ").trim().slice(0, 80),
    x: Math.round(r.left),
    y: Math.round(r.top + window.scrollY),
    ancho: Math.round(r.width),
    alto: Math.round(r.height),
    scrollY: Math.round(window.scrollY),
    enPantalla: r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth && r.width > 0,
    indicador: Boolean(indicador),
    fueraDelDialogo: Boolean(dialogo && !dialogo.contains(el)),
  };
}
