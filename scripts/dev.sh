#!/usr/bin/env bash
# Levanta la app en local con un solo comando: API (8000) e interfaz (5173), y el worker con WORKER=1.
# Lee .env si existe. Sin AUTH_MODE, arranca sin cuentas. Ctrl+C para todo.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
	set -a
	# shellcheck disable=SC1091
	source .env
	set +a
fi

if [[ -z "${AUTH_MODE:-}" ]]; then
	export PERMITIR_SIN_AUTENTICACION="${PERMITIR_SIN_AUTENTICACION:-1}"
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
	echo "Aviso: falta ANTHROPIC_API_KEY (en el entorno o en .env). La interfaz carga, pero el asesor no responderá."
fi

[[ -d .venv ]] || uv sync
[[ -d frontend/node_modules ]] || npm --prefix frontend ci

pids=()
parar() {
	trap - INT TERM EXIT
	for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
	wait 2>/dev/null || true
}
trap parar INT TERM EXIT

uv run uvicorn asistente.plataforma.api.api:app --host 127.0.0.1 --port 8000 --reload --reload-dir asistente &
pids+=($!)

npm --prefix frontend run dev &
pids+=($!)

if [[ "${WORKER:-0}" == "1" ]]; then
	uv run python -m asistente.plataforma.cli.plataforma worker &
	pids+=($!)
fi

echo "Abre http://localhost:5173 (Ctrl+C para parar)"
# Si cualquiera de los procesos cae, se para el resto.
wait -n
