# Cerebro personal fiable

Asistente del hogar que responde con fuentes. Cada afirmación lleva su cita y cada cifra sale de un cálculo
que se puede repetir. Empezó como «HaciendaGPT», un RAG sobre la Agencia Tributaria; hoy el módulo fiscal es uno
más, junto a la luz, la hipoteca, las ayudas, las caducidades, las suscripciones, el correo (Gmail, solo lectura)
y un agente con navegador.

El código vive en `asistente/`:

- `nucleo/`: el agente, sus herramientas, la memoria de decisiones, la memoria de conversaciones (sobre
  [Hindsight](https://github.com/vectorize-io/hindsight)) y la evaluación.
- `conocimiento/`: ingesta con Docling, índice del corpus en un fichero SQLite (vectores con sqlite-vec y texto con
  FTS5) y modelos locales en MLX.
- `ambitos/`: los dominios (fiscal, hipoteca…) con sus reglas y sus datos de la norma.
- `capacidades/`: correo, navegador y otras acciones fuera del índice.
- `plataforma/`: API (FastAPI), worker de trabajos programados, CLI y configuración.

La interfaz es una PWA en `frontend/` (React, Vite y Primer React).

## Arranque

Hace falta Python 3.14, [uv](https://docs.astral.sh/uv/) y Node 20.19 o posterior. Los modelos locales solo corren
en Mac con Apple Silicon; en Linux el resto funciona y esas rutas quedan desactivadas.

Una vez:

```bash
uv sync
npm --prefix frontend ci
```

Para levantarlo todo con un comando:

```bash
make dev
```

Arranca la API en el puerto 8000 y la interfaz en http://localhost:5173. Con `WORKER=1 make dev` arranca también el
worker. Si existe `.env`, lo lee (ahí puedes dejar `ANTHROPIC_API_KEY`), y si no defines `AUTH_MODE` entra sin
cuentas. Ctrl+C lo para todo menos el demonio de la memoria (abajo). El script es `scripts/dev.sh` y necesita bash 4.3
o posterior; el bash 3.2 que trae macOS no vale, el de Homebrew sí.

Al arrancar la API, la memoria de conversaciones arranca en segundo plano un demonio de Hindsight, con su propio
PostgreSQL en `~/.pg0/`. Sin `ANTHROPIC_API_KEY` no arranca y el asistente funciona sin memoria. La primera vez descarga los modelos locales y tarda uno o dos minutos; mientras tanto el
asistente responde sin memoria. El demonio sigue vivo al parar la app: se para con
`uv run hindsight-embed -p cerebro daemon stop`. Con `MEMORIA_CONVERSACIONES=false` no arranca. Cada mensaje que
recuerda cuesta llamadas a `claude-haiku-5-5` con la misma clave ([docs/cerebro/memoria.md](docs/cerebro/memoria.md)).

Si prefieres arrancar cada pieza por separado, la API va en una terminal. Sin cuentas solo arranca si lo permites de forma explícita:

```bash
PERMITIR_SIN_AUTENTICACION=1 ANTHROPIC_API_KEY=... uv run uvicorn asistente.plataforma.api.api:app --host 127.0.0.1 --port 8000
```

Con `AUTH_MODE=required` en lugar de `PERMITIR_SIN_AUTENTICACION=1`, la interfaz pide darse de alta y entrar, como
en producción. Sin `ANTHROPIC_API_KEY` la interfaz carga, pero el asesor no puede responder.

La interfaz, en otra terminal. Se abre en http://localhost:5173 y Vite manda `/api` a la API del puerto 8000:

```bash
npm --prefix frontend run dev
```

El worker, en una tercera, solo si quieres los trabajos programados (avisos, vigilancia del BOE, correo, consejo):

```bash
uv run python -m asistente.plataforma.cli.plataforma worker
```

Para responder con normativa, el asesor necesita el índice del corpus y el modelo de embeddings. Sin ellos se
abstiene en esas preguntas, aunque las calculadoras y los módulos funcionan. Si ya los tienes, indícalo al arrancar
la API con `INDICE_DIR=<carpeta con indice.sqlite> EMBEDDING_MODEL=<modelo MLX>`. Para crearlos:
`scripts/convert_to_mlx.py` y `python -m asistente.plataforma.cli.processor` (ver la referencia de operación;
cargan mucho la máquina). El proveedor de LLM por defecto es Claude; las demás variables están en
[docs/despliegue.md](docs/despliegue.md).

## Comprobaciones

```bash
make test-rapido     # sin tests lentos, sin navegador y sin red
make test-completo   # todo salvo los marcados en_vivo
make quality         # formato, ruff, ty, contrato de capas (informe), tests, tipos del frontend y frontend
npm --prefix frontend run medir   # medición de la UI con la API simulada (rendimiento, accesibilidad, consistencia)
```

Los tests no salen de 127.0.0.1, no ven claves reales y usan su propia base de casos. Lo que necesite red se
marca con `@pytest.mark.en_vivo` y se lanza a mano.

## Documentación

- [Referencia de operación](docs/referencia.md): corpus, índice, API, módulos, agente y calculadoras.
- [Operación diaria](docs/operacion.md) y [despliegue](docs/despliegue.md).
- [Seguridad](docs/seguridad.md) y [privacidad](docs/privacidad.md).
- [Conectores MCP y servidor MCP](docs/capacidades/conectores.md) y
  [agente con navegador](docs/capacidades/navegador.md).
- [Decisiones de arquitectura](docs/adr/).
- [Plan original para Claude Code](docs/historico/ROADMAP_CLAUDE_CODE.md), de la época de hacienda-gpt.
