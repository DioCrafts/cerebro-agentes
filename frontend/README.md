# Interfaz web de HaciendaGPT

Aplicación de una página en React 19, TypeScript y Vite. Habla con la API de FastAPI bajo `/api`: en desarrollo, Vite reenvía `/api/*` a `http://127.0.0.1:8000` (cámbialo con `VITE_API_PROXY`); en producción lo hace Caddy (`docs/despliegue/Caddyfile`). Es instalable como PWA: el service worker sale de `pwa/sw.js` al compilar.

Necesita Node 22.12 o posterior.

```bash
npm ci
npm run dev         # http://localhost:5173
npm run typecheck   # tsc -b
npm run lint        # eslint con reglas que usan tipos y jsx-a11y
npm test            # vitest (jsdom + Testing Library)
npm run build       # dist/
```

## Dónde está cada cosa

- `src/App.tsx`: la puerta de cuentas (con `AUTH_MODE=required` pide iniciar sesión) alrededor de `src/shell/Asesor.tsx`.
- `src/shell/`: el estado de la aplicación en hooks. `useConsulta.ts` lleva las conversaciones y el turno en curso; ese turno vive en un almacén externo (`turnoEnCurso.ts`) para que cada token repinte solo su burbuja.
- `src/paneles/`: el registro de paneles laterales (`registro.ts`) y un fichero por panel; `components/PanelHost.tsx` los monta.
- `src/api*.ts`: un cliente por router del backend. Todos validan la respuesta con los guardas de `src/lib/guards.ts` antes de usarla.
- `src/lib/`: funciones puras, cada una con sus tests al lado (`*.test.ts`). `formulario.ts` lee las cifras escritas («1.500,50») y lo comparten el simulador y el optimizador; `lib/optimizador/` construye la petición a `/optimize` y lo que se guarda en el expediente; `lib/simulador/` tiene las casillas y el detalle de cada hijo.
- `src/components/optimizador/` y `src/components/simulador/`: las piezas de esos dos paneles y sus hooks (`useOptimizador`, `useSimulacion`).

## Notas

- La sesión va en una cookie `HttpOnly` que pone el servidor; el cliente no guarda el token. Las peticiones usan `credentials: "same-origin"` y llevan `X-Requested-With`, que el servidor exige a las escrituras autenticadas por la cookie.
- El progreso del agente, del consejo y de las tareas del navegador llega por Server-Sent Events (`src/lib/sse.ts`), con reconexión por `Last-Event-ID` y espera creciente.
- La CSP de producción es `script-src 'self'`: no añadas scripts en línea a `index.html` (el tema se aplica desde `public/tema.js`).
