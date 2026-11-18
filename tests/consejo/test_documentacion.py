"""The council's decision record and architecture document exist, say what the code does, and every setting of the
``CONSEJO_*`` block is documented for the operator."""

from pathlib import Path
import re

from asistente.plataforma import settings

RAIZ = Path(__file__).resolve().parents[2]


def test_adr_0004_has_the_required_sections() -> None:
    contenido = (RAIZ / "docs/adr/0004-consejo.md").read_text(encoding="utf-8")
    for marcador in (
        "## 1) Contexto",
        "## 2) Problemas",
        "## 3) Decisión",
        "## 4) Alternativas descartadas",
        "## 5) Trade-offs",
        "## 6) Riesgos y mitigaciones",
        "## 7) Plan incremental",
        "```mermaid",
        "CONSEJO_ACTIVO",
    ):
        assert marcador in contenido


def test_the_architecture_document_covers_phases_bias_privacy_cost_and_limits() -> None:
    contenido = (RAIZ / "docs/consejo/arquitectura.md").read_text(encoding="utf-8")
    for marcador in (
        "```mermaid",
        "## Anti-bias measures, and why",
        "## Privacy",
        "## Cost",
        "## What is not verified",
        "convocar_consejo",
        "consejo:expediente",
        "preferencia_usuario",
    ):
        assert marcador in contenido


def test_every_council_setting_is_documented_for_the_operator() -> None:
    despliegue = (RAIZ / "docs/despliegue.md").read_text(encoding="utf-8")
    arquitectura = (RAIZ / "docs/consejo/arquitectura.md").read_text(encoding="utf-8")
    for nombre in (n for n in dir(settings) if n.startswith("CONSEJO_")):
        assert re.search(rf"`{nombre}`", despliegue), nombre
        assert nombre in arquitectura, nombre
