.PHONY: dev format lint type-check imports test test-rapido test-completo \
	tipos tipos-check frontend-test frontend-lint frontend-typecheck frontend quality

# Levanta API e interfaz en http://localhost:5173 (WORKER=1 añade el worker). Ctrl+C para todo.
dev:
	./scripts/dev.sh

format:
	uv run ruff format .
	uv run ruff check . --fix

lint:
	uv run ruff format --check .
	uv run ruff check .

type-check:
	uv run ty check asistente

# Contrato de capas (pyproject, [tool.importlinter]). Hoy solo informa: no corta `make quality` hasta que se
# paguen las importaciones que lo incumplen.
imports:
	-uv run lint-imports

# Lo que se pasa antes de cada commit: sin tests lentos, sin navegador y sin red.
test-rapido:
	uv run pytest -q -m "not lento and not navegador and not en_vivo"

# Toda la batería salvo lo que sale a la red (en_vivo se lanza a mano: `uv run pytest -m en_vivo`).
test-completo:
	uv run pytest -q -m "not en_vivo"

test: test-completo

# Tipos del frontend a partir del OpenAPI de la API: vuelca el esquema (importa la aplicación, no arranca el
# servidor ni carga modelos) y genera frontend/src/api/esquema.d.ts con openapi-typescript.
tipos:
	uv run python -m asistente.plataforma.cli.openapi > frontend/openapi.json.tmp
	mv frontend/openapi.json.tmp frontend/openapi.json
	npm --prefix frontend run tipos

# Falla si un modelo de la API cambió y nadie regeneró los tipos, o si alguien tocó a mano los generados.
TIPOS_GENERADOS := frontend/openapi.json frontend/src/api/esquema.d.ts

tipos-check: tipos
	@git --no-pager diff --exit-code --stat -- $(TIPOS_GENERADOS) || \
		{ echo "Los tipos del frontend no coinciden con la API: ejecuta 'make tipos' y versiona el resultado."; exit 1; }
	@test -z "$$(git ls-files --others --exclude-standard -- $(TIPOS_GENERADOS))" || \
		{ echo "Tipos generados sin versionar: añade $(TIPOS_GENERADOS) a git."; exit 1; }

frontend-test:
	npm --prefix frontend run --if-present test

frontend-lint:
	npm --prefix frontend run lint

frontend-typecheck:
	npm --prefix frontend run --if-present typecheck

frontend: frontend-lint frontend-typecheck frontend-test

quality: format lint type-check imports test tipos-check frontend
