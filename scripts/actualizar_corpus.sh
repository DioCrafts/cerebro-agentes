#!/usr/bin/env bash
# Actualiza el corpus fiscal y el índice FAISS de forma incremental.
#
#   1. boe_seed            normas del catálogo (texto consolidado, se vuelve a bajar)
#   2. aeat_manuales_seed  manuales prácticos de la AEAT (reanuda; solo baja lo nuevo)
#   3. dgt_seed            consultas vinculantes de los últimos DGT_MESES meses
#   4. processor --incremental  solo trocea y embebe los ficheros nuevos o cambiados
#
# Todo se descarga en el último snapshot que ya exista bajo $CONTENT_DIR (o en
# uno con la fecha de hoy si no hay ninguno): el processor recorre $CONTENT_DIR
# entero, así que abrir un snapshot nuevo en cada ejecución duplicaría documentos.
#
# Variables (todas opcionales):
#   REPO          checkout con data/ y .env           (por defecto, la raíz de este script)
#   PYTHON        intérprete con el paquete instalado (por defecto, "uv run python")
#   CONTENT_DIR   raíz del corpus                     ($REPO/data/html)
#   INDEX_DIR     índice a actualizar                 ($REPO/data/faiss-v2)
#   SNAPSHOT      carpeta de snapshot a usar          (la última existente)
#   DGT_MESES     meses hacia atrás que se revisan    (3)
#   NUM_WORKERS   procesos de Docling                 (8)
#   SALTAR        pasos a omitir, separados por comas (p. ej. "dgt,aeat")
#
# Uso:  scripts/actualizar_corpus.sh            (ver docs en el README, sección 1.c)
set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
CONTENT_DIR="${CONTENT_DIR:-$REPO/data/html}"
INDEX_DIR="${INDEX_DIR:-$REPO/data/faiss-v2}"
DGT_MESES="${DGT_MESES:-3}"
NUM_WORKERS="${NUM_WORKERS:-8}"
SALTAR=",${SALTAR:-},"
read -r -a PY <<<"${PYTHON:-uv run python}"

LOG_DIR="$REPO/data/logs"
mkdir -p "$LOG_DIR" "$CONTENT_DIR"
LOG="$LOG_DIR/actualizar_corpus-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

if [[ -f "$REPO/.env" ]]; then
  set -a; . "$REPO/.env"; set +a
fi
# faiss y docling enlazan cada uno su libomp en macOS.
export KMP_DUPLICATE_LIB_OK=TRUE
# El modo incremental deserializa $INDEX_DIR (pickle) y se niega sin esta marca
# (docs/seguridad.md, S-17). Aquí el índice es el que construye este mismo script;
# si INDEX_DIR apunta a un índice de otra procedencia, exporta FAISS_TRUSTED_INDEX=false.
export FAISS_TRUSTED_INDEX="${FAISS_TRUSTED_INDEX:-true}"

if [[ -z "${SNAPSHOT:-}" ]]; then
  SNAPSHOT="$(find "$CONTENT_DIR" -mindepth 1 -maxdepth 1 -type d -name '20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]' \
    -exec basename {} \; | sort | tail -n 1)"
  SNAPSHOT="${SNAPSHOT:-$(date -u +%Y-%m-%d)}"
fi
DGT_DESDE="$("${PY[@]}" -c "
from datetime import date
hoy = date.today(); m = hoy.year * 12 + hoy.month - 1 - int('$DGT_MESES')
print(f'{m // 12:04d}-{m % 12 + 1:02d}')")"

paso() { [[ "$SALTAR" != *",$1,"* ]]; }
echo "== $(date -Iseconds) corpus=$CONTENT_DIR snapshot=$SNAPSHOT índice=$INDEX_DIR"

fallos=0
if paso boe; then
  echo "== BOE (catálogo)"
  "${PY[@]}" -m asistente.plataforma.cli.boe_seed --output-dir "$CONTENT_DIR" --snapshot-date "$SNAPSHOT" \
    || { echo "!! boe_seed terminó con errores (se sigue)"; fallos=$((fallos + 1)); }
fi
if paso aeat; then
  echo "== AEAT (manuales prácticos)"
  "${PY[@]}" -m asistente.plataforma.cli.aeat_manuales_seed --output-dir "$CONTENT_DIR" --snapshot-date "$SNAPSHOT" \
    || { echo "!! aeat_manuales_seed terminó con errores (se sigue)"; fallos=$((fallos + 1)); }
fi
if paso dgt; then
  echo "== DGT (consultas vinculantes desde $DGT_DESDE)"
  "${PY[@]}" -m asistente.plataforma.cli.dgt_seed --output-dir "$CONTENT_DIR" --snapshot-date "$SNAPSHOT" \
    --start "$DGT_DESDE" --delay 1.0 \
    || { echo "!! dgt_seed terminó con errores (se sigue)"; fallos=$((fallos + 1)); }
fi
if paso indice; then
  echo "== Índice incremental"
  "${PY[@]}" -m asistente.plataforma.cli.processor --incremental \
    --content-dir "$CONTENT_DIR" --output-dir "$INDEX_DIR" --num-workers "$NUM_WORKERS"
fi
echo "== $(date -Iseconds) fin (pasos de descarga con errores: $fallos). Log: $LOG"
exit $(( fallos > 0 ? 1 : 0 ))
