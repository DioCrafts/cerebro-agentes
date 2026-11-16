"""Turn the DGT golden set (:mod:`hacienda_gpt.evals.agent_eval`) into suite cases.

``eval_agent build`` writes ``eval_data/dgt_golden.jsonl`` from downloaded consultas (one model call each);
this module converts each line into a ``dgt`` case of the suite: the consulta is hidden from retrieval while
the case runs (the answer key), the agent must cite at least one source, and an LLM judge compares the
conclusion with the DGT's — a criterion that only counts if the judge quotes the answer literally.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from hacienda_gpt.evals.agent_eval import GoldenCase, load_golden
from hacienda_gpt.evaluacion.modelos import CasoEvaluacion, ExpCitas, Origen, Rubrica


def caso_dgt(golden: GoldenCase) -> CasoEvaluacion:
    slug = re.sub(r"[^a-z0-9]+", "-", golden.case_id.lower()).strip("-")
    base = "; ".join(golden.legal_basis)
    return CasoEvaluacion(
        id=f"dgt-{slug}",
        categoria="dgt",
        descripcion=f"Consulta vinculante {golden.case_id}" + (f" ({golden.tributo})" if golden.tributo else ""),
        pregunta=golden.question,
        expectativas=[ExpCitas(tipo="citas", minimo=1)],
        rubrica=Rubrica(
            criterios=[
                "Llega a la misma conclusión que la referencia de la DGT, con sus condiciones esenciales.",
                "No da ninguna cifra, plazo ni requisito que contradiga la referencia.",
            ],
            referencia=golden.expected_answer + (f"\nNormativa: {base}" if base else ""),
        ),
        ocultar_fuente=golden.case_id,
        origen=Origen(tipo="dgt", referencia=golden.case_id, etiquetas=[golden.tributo] if golden.tributo else []),
    )


def importar_dgt(dataset: Path, destino: Path, *, sobrescribir: bool = False) -> list[Path]:
    """Write one case file per golden case under ``destino``; existing files are kept unless asked."""
    destino.mkdir(parents=True, exist_ok=True)
    escritos: list[Path] = []
    for golden in load_golden(dataset):
        caso = caso_dgt(golden)
        ruta = destino / f"{caso.id}.json"
        if ruta.exists() and not sobrescribir:
            continue
        ruta.write_text(
            json.dumps(caso.model_dump(mode="json", exclude_defaults=True), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        escritos.append(ruta)
    return escritos


__all__ = ["caso_dgt", "importar_dgt"]
