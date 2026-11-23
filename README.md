# Cerebro personal fiable

Asistente del hogar que responde con fuentes. Cada afirmación lleva su cita y cada cifra sale de un cálculo
que se puede repetir. Empezó como «HaciendaGPT», un RAG sobre la Agencia Tributaria; hoy el módulo fiscal es uno
más, junto a la luz, la hipoteca, las ayudas, las caducidades, las suscripciones, el correo (Gmail, solo lectura)
y un agente con navegador.

El código vive en `asistente/`:

- `nucleo/`: el agente, sus herramientas, la memoria de decisiones y la evaluación.
- `conocimiento/`: ingesta con Docling, índice FAISS más BM25 y modelos locales en MLX.
- `ambitos/`: los dominios (fiscal, hipoteca…) con sus reglas y sus datos de la norma.
- `capacidades/`: correo, navegador y otras acciones fuera del índice.
- `plataforma/`: API (FastAPI), UI (Streamlit), CLI y configuración.

## Arranque

Hace falta Python 3.13 y [uv](https://docs.astral.sh/uv/). Los modelos locales solo corren en Mac con Apple
Silicon; en Linux el resto funciona y esas rutas quedan desactivadas.

```bash
uv sync
uv run uvicorn asistente.plataforma.api.api:app --host 127.0.0.1 --port 8000
uv run streamlit run asistente/plataforma/ui/app.py
```

El proveedor de LLM por defecto es Claude (`ANTHROPIC_API_KEY`). Las demás variables, el índice y los corpus
están en la referencia de operación.

## Comprobaciones

```bash
make test-rapido     # sin tests lentos, sin navegador y sin red
make test-completo   # todo salvo los marcados en_vivo
make quality         # formato, ruff, mypy, contrato de capas (informe), tests y frontend
```

Los tests no salen de 127.0.0.1, no ven claves reales y usan su propia base de casos. Lo que necesite red se
marca con `@pytest.mark.en_vivo` y se lanza a mano.

## Documentación

- [Referencia de operación](docs/referencia.md): corpus, índice, API, módulos, agente y calculadoras.
- [Operación diaria](docs/operacion.md) y [despliegue](docs/despliegue.md).
- [Seguridad](docs/seguridad.md) y [privacidad](docs/privacidad.md).
- [Decisiones de arquitectura](docs/adr/).
- [Plan original para Claude Code](docs/historico/ROADMAP_CLAUDE_CODE.md), de la época de hacienda-gpt.
