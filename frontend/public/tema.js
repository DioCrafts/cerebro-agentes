// Apply the saved (or OS) theme before first paint to avoid a flash. A file of its own, not inline,
// so the Content-Security-Policy can be `script-src 'self'` (docs/despliegue/Caddyfile).
try {
  var t = localStorage.getItem("haciendagpt_theme");
  if (t === "dark" || (!t && matchMedia("(prefers-color-scheme: dark)").matches)) {
    document.documentElement.classList.add("dark");
  }
} catch (e) {}
