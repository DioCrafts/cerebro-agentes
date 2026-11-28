"""The short names tax practice gives the main laws («LIRPF», «LIVA»), by BOE id.

A sigla is how users and the agent name a norm, and what a fragment's header shows. The table is curated: a norm
without a row is named by its title. The catalog of the corpus (``boe_seed_catalog.json``) may give a ``sigla``
for a norm that is not here; the catalog wins.
"""

from __future__ import annotations

SIGLAS: dict[str, str] = {
    "BOE-A-2006-20764": "LIRPF",  # Ley 35/2006, del IRPF
    "BOE-A-2007-6820": "RIRPF",  # Real Decreto 439/2007, Reglamento del IRPF
    "BOE-A-1992-28740": "LIVA",  # Ley 37/1992, del IVA
    "BOE-A-2003-23186": "LGT",  # Ley 58/2003, General Tributaria
    "BOE-A-2014-12328": "LIS",  # Ley 27/2014, del Impuesto sobre Sociedades
    "BOE-A-1987-28141": "LISD",  # Ley 29/1987, del Impuesto sobre Sucesiones y Donaciones
}


def sigla_de(boe_id: str) -> str:
    return SIGLAS.get(boe_id, "")
