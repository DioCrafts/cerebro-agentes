import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// Files of public/ that belong to the app shell (precached by the service worker with the build output).
const PUBLICOS = [
  "manifest.webmanifest",
  "favicon.svg",
  "tema.js",
  "icons/icono.svg",
  "icons/icono-192.png",
  "icons/icono-512.png",
  "icons/icono-maskable-512.png",
  "icons/apple-touch-icon.png",
];

/**
 * Writes dist/sw.js from pwa/sw.js with the list of files to precache: the build output (HTML, JS, CSS,
 * lazy panel chunks) plus the public shell files above. Nothing under the API's paths can ever be listed:
 * the list is the build's own output. The version is a hash of every precached file, so each build that
 * changes something installs a new worker («Hay una versión nueva»).
 */
function serviceWorker(): Plugin {
  return {
    name: "haciendagpt-service-worker",
    apply: "build",
    enforce: "post",
    generateBundle(_options, bundle) {
      const hash = createHash("sha256");
      const salida: string[] = [];
      for (const [nombre, fichero] of Object.entries(bundle).sort(([a], [b]) => a.localeCompare(b))) {
        if (nombre.endsWith(".map") || nombre === "sw.js") continue;
        salida.push(nombre);
        hash.update(nombre);
        hash.update(fichero.type === "chunk" ? fichero.code : fichero.source);
      }
      for (const nombre of PUBLICOS) {
        hash.update(nombre);
        hash.update(readFileSync(new URL(`./public/${nombre}`, import.meta.url)));
      }
      const precache = [...salida, ...PUBLICOS];
      if (!precache.includes("index.html")) this.error("index.html is missing from the build output");
      const plantilla = readFileSync(new URL("./pwa/sw.js", import.meta.url), "utf8");
      this.emitFile({
        type: "asset",
        fileName: "sw.js",
        source: plantilla
          .replace('"__VERSION__"', JSON.stringify(hash.digest("hex").slice(0, 16)))
          .replace("__PRECACHE__", JSON.stringify(precache)),
      });
    },
  };
}

// Vite proxies /api/* to the FastAPI backend so the React client and
// the Python API can share the same origin in development (no CORS
// preflight, no environment-specific URL). Set ``VITE_API_BASE`` if
// you need to override. ``VITE_API_PROXY`` changes where the dev and
// preview servers send /api (default: the API on 127.0.0.1:8000).
const proxy = {
  "/api": {
    target: process.env.VITE_API_PROXY || "http://127.0.0.1:8000",
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss(), serviceWorker()],
  server: {
    port: 5173,
    proxy,
  },
  preview: {
    proxy,
  },
});
