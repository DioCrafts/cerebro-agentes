#!/usr/bin/env bash
set -u

run_step() {
  local name="$1"
  shift
  echo "== ${name} =="
  "$@"
  local rc=$?
  if [ $rc -eq 0 ]; then
    echo "PASS: ${name}"
  else
    echo "FAIL(${rc}): ${name}"
  fi
  echo
  return 0
}

run_step "Python compile check" python -m compileall asistente
run_step "CLI import check: crawler" python -m asistente.plataforma.cli.crawler --help
run_step "CLI import check: processor" python -m asistente.plataforma.cli.processor --help
run_step "Live source reachability (AEAT)" python - <<'PY'
import urllib.request
urls=[
'https://sede.agenciatributaria.gob.es/',
'https://sede.agenciatributaria.gob.es/Sede/irpf.html',
'https://sede.agenciatributaria.gob.es/Sede/iva.html',
'https://sede.agenciatributaria.gob.es/Sede/colaborar-agencia-tributaria/calendario-contribuyente.html'
]
for u in urls:
    with urllib.request.urlopen(u, timeout=20) as r:
        print(f'{u} -> {r.status} {r.getheader("content-type")}')
PY
run_step "Evaluator smoke test" python -m asistente.plataforma.cli.eval --output /tmp/eval_results.json
[ -f /tmp/eval_results.json ] && cat /tmp/eval_results.json
