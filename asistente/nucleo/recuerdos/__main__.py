"""The memory's scheduled job (``memoria-resumir`` in ``asistente/plataforma/trabajos.py``).

    python -m asistente.nucleo.recuerdos resumir [--db-path RUTA] [--sin-lotes] [--limite N]
    python -m asistente.nucleo.recuerdos purgar [--db-path RUTA]

``resumir`` collects finished batches, summarises the idle conversations (one batch when the provider has a
batch API, otherwise one call each) and then applies the retention of the raw messages; without a configured
model it only purges. Prints a JSON report; exit code 0 unless the database cannot be opened.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from asistente.plataforma import settings

logger = logging.getLogger(__name__)


def _proveedor() -> Any | None:
    from asistente.nucleo.recuerdos.servicio import proveedor_por_defecto

    try:
        return proveedor_por_defecto()
    except Exception:
        logger.warning("No model is configured for the memory's summaries: only the retention runs.", exc_info=True)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m asistente.nucleo.recuerdos")
    parser.add_argument("orden", choices=["resumir", "purgar"])
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--sin-lotes", action="store_true", help="una llamada por conversación, sin la API de lotes")
    parser.add_argument("--limite", type=int, default=50)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    from asistente.nucleo.recuerdos.servicio import servicio

    memoria = servicio(args.db_path or settings.DECISION_STATE_DB_PATH)
    informe: dict[str, Any] = {}
    try:
        if args.orden == "resumir" and getattr(settings, "MEMORIA_CONVERSACIONES", True):
            informe["resumen"] = memoria.resumir_pendientes(
                _proveedor(), lotes=False if args.sin_lotes else None, limite=args.limite
            )
        informe["retencion"] = memoria.purgar_caducados()
    except Exception:
        logger.exception("The memory job failed.")
        return 1
    print(json.dumps(informe, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
