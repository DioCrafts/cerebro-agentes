.PHONY: format lint type-check imports test test-rapido test-completo \
	frontend-test frontend-lint frontend-typecheck frontend quality

format:
	uv run ruff format .
	uv run ruff check . --fix

lint:
	uv run ruff format --check .
	uv run ruff check .

type-check:
	uv run mypy asistente

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

frontend-test:
	npm --prefix frontend run --if-present test

frontend-lint:
	npm --prefix frontend run lint

frontend-typecheck:
	npm --prefix frontend run --if-present typecheck

frontend: frontend-lint frontend-typecheck frontend-test

quality: format lint type-check imports test frontend
