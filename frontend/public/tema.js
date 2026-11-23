// Apply the saved (or OS) theme before first paint to avoid a flash. A file of its own, not inline,
// so the Content-Security-Policy can be `script-src 'self'` (docs/despliegue/Caddyfile).
// Same attributes as aplicarTema() in src/lib/theme.ts: the `dark` class and Primer's colour mode.
try {
  var t = localStorage.getItem("haciendagpt_theme");
  var oscuro = t === "dark" || (!t && matchMedia("(prefers-color-scheme: dark)").matches);
  var html = document.documentElement;
  if (oscuro) html.classList.add("dark");
  html.setAttribute("data-color-mode", oscuro ? "dark" : "light");
  html.setAttribute("data-light-theme", "light");
  html.setAttribute("data-dark-theme", "dark");
} catch (e) {}
