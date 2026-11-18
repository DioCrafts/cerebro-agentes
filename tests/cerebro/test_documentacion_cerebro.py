"""The brain's decision record and architecture document exist and say what the code does."""

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def test_adr_0003_has_the_required_sections() -> None:
    contenido = (RAIZ / "docs/adr/0003-cerebro-del-hogar.md").read_text(encoding="utf-8")
    for marcador in (
        "## 1) Contexto",
        "## 2) Problemas",
        "## 3) Decisión",
        "## 5) Trade-offs",
        "## 6) Riesgos y mitigaciones",
        "## 7) Plan incremental de migración",
        "```mermaid",
        "CEREBRO_MODO=fiscal",
    ):
        assert marcador in contenido


def test_the_architecture_document_covers_the_gates_cost_and_limits() -> None:
    contenido = (RAIZ / "docs/cerebro/arquitectura.md").read_text(encoding="utf-8")
    for marcador in (
        "```mermaid",
        "## The gates, and why",
        "## Cost and latency",
        "## What is not verified",
        "cerebro:intencion",
        "paso directo",
        "CEREBRO_UMBRAL_CONFIANZA",
    ):
        assert marcador in contenido
