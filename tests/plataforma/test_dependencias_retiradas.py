"""Libraries the project retired must not come back through an import.

FAISS and LangChain left with the move of the corpus index to SQLite; ``requests`` (and the ``urllib3`` it brought)
with the move of every HTTP call to ``httpx``. Some of them are still installed as dependencies of other packages,
so only this test notices a new import.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2] / "asistente"
RETIRADAS = ("faiss", "langchain", "langchain_core", "langchain_community", "requests", "urllib3")


def _importados(fichero: Path) -> set[str]:
    nombres: set[str] = set()
    for nodo in ast.walk(ast.parse(fichero.read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.Import):
            nombres.update(alias.name.split(".")[0] for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            nombres.add(nodo.module.split(".")[0])
    return nombres


def test_ningun_modulo_importa_una_libreria_retirada() -> None:
    culpables = {
        str(f.relative_to(RAIZ.parent)): sorted(_importados(f) & set(RETIRADAS)) for f in sorted(RAIZ.rglob("*.py"))
    }
    assert {f: libs for f, libs in culpables.items() if libs} == {}
