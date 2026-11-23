#!/usr/bin/env bash
set -euo pipefail

# Automated snapshot pipeline:
# 1) crawl
# 2) snapshot versioning
# 3) controlled reindex
# 4) quality validation
# 5) optional promotion

ENVIRONMENT="${1:-dev}"
SNAPSHOT_DATE="${2:-$(date +%F)}"
DATA_ROOT="./data/${ENVIRONMENT}"
SNAPSHOT_DIR="${DATA_ROOT}/snapshots/${SNAPSHOT_DATE}"
HTML_DIR="${SNAPSHOT_DIR}/html"
INDEX_DIR="${SNAPSHOT_DIR}/faiss"
EVAL_JSON="${SNAPSHOT_DIR}/eval_pipeline_results.json"
EVAL_MD="${SNAPSHOT_DIR}/eval_pipeline_report.md"
EVAL_HTML="${SNAPSHOT_DIR}/eval_pipeline_report.html"

mkdir -p "${HTML_DIR}" "${INDEX_DIR}"

echo "[1/5] Crawling snapshot ${SNAPSHOT_DATE} for ${ENVIRONMENT}"
uv run python -m asistente.plataforma.cli.crawler --crawler web --folder "${HTML_DIR}" --depth 1 --mode flat

echo "[2/5] Building FAISS index"
uv run python -m asistente.plataforma.cli.processor --content-dir "${HTML_DIR}" --output-dir "${INDEX_DIR}" --overwrite-output

echo "[3/5] Running evaluation pipeline"
uv run python -m asistente.plataforma.cli.eval_pipeline --dataset eval_data/intent_fact_extraction.jsonl --output-json "${EVAL_JSON}" --output-md "${EVAL_MD}" --output-html "${EVAL_HTML}"

echo "[4/5] Running critical regression checks"
uv run python -m asistente.plataforma.cli.check_critical_regressions --dataset eval_data/critical_cases/tramite_year_critical_cases.jsonl

echo "[5/5] Snapshot ready at ${SNAPSHOT_DIR}"
echo "Promotion is a separate controlled step via: uv run python -m asistente.plataforma.cli.promote_snapshot --env ${ENVIRONMENT} --snapshot ${SNAPSHOT_DATE}"
