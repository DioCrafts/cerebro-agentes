"""The engine's document exists and says what the code does (settings, constants, fairness mechanics)."""

from decimal import Decimal
from pathlib import Path

from asistente.nucleo.consejo import deliberacion
from asistente.plataforma import settings

RAIZ = Path(__file__).resolve().parents[3]


def test_the_document_covers_the_mechanics_and_matches_the_code() -> None:
    contenido = (RAIZ / "docs/consejo/deliberacion.md").read_text(encoding="utf-8")
    for marcador in (
        "```mermaid",
        "## Fairness mechanics, and why each one",
        "## Aggregation and agreement",
        "## Cost, cap and budget",
        "## What is not verified",
        "Kemeny",
        "Schulze",
        "Latin square",
        "dictamen_parcial",
        "autoria_criticas",
    ):
        assert marcador in contenido, marcador
    for ajuste in ("CONSEJO_MIEMBROS", "CONSEJO_FAMILIAS", "CONSEJO_MAX_PARALELO", "CONSEJO_SEMILLA"):
        assert ajuste in contenido and hasattr(settings, ajuste), ajuste
    assert deliberacion.MARGEN_PROYECCION == Decimal("1.15") and "× 1.15" in contenido
    assert [str(v) for v in deliberacion.FACTOR_SALIDA.values()] == ["1.0", "1.0", "1.5"]
