"""The gateway's decision record and architecture document exist and say what the code does; every ``MENSAJERIA_*``
setting is documented for the operator; the privacy document lists its tables."""

from pathlib import Path
import re

RAIZ = Path(__file__).resolve().parents[2]


def test_adr_0005_has_the_required_sections() -> None:
    contenido = (RAIZ / "docs/adr/0005-mensajeria.md").read_text(encoding="utf-8")
    for marcador in (
        "## 1) Contexto",
        "## 2) Problemas",
        "## 3) Decisión",
        "## 4) Alternativas descartadas",
        "## 5) Trade-offs",
        "## 6) Riesgos y mitigaciones",
        "## 7) Plan incremental",
        "```mermaid",
        "MENSAJERIA_ACTIVA",
        "PuertaPalabrasPropias",
    ):
        assert marcador in contenido, marcador


def test_the_architecture_document_covers_pairing_confirmations_data_and_adapters() -> None:
    contenido = (RAIZ / "docs/mensajeria/arquitectura.md").read_text(encoding="utf-8")
    for marcador in (
        "```mermaid",
        "## Pairing",
        "### Forwarded and quoted text is data",
        "## Confirmations in chat",
        "## Outgoing notices",
        "## What an adapter must provide",
        "## What is not verified",
        "ejecutar_turno_agente",
        "desconectar_contacto",
        "/desemparejar",
    ):
        assert marcador in contenido, marcador


def test_every_messaging_setting_is_documented_for_the_operator() -> None:
    despliegue = (RAIZ / "docs/despliegue.md").read_text(encoding="utf-8")
    arquitectura = (RAIZ / "docs/mensajeria/arquitectura.md").read_text(encoding="utf-8")
    fuente = (RAIZ / "asistente/plataforma/settings.py").read_text(encoding="utf-8")
    bloque = fuente.split("# --- mensajeria (gateway) ---", 1)[1].split("\n# --- ", 1)[0]
    declarados = re.findall(r"^(MENSAJERIA_[A-Z0-9_]+) =", bloque, flags=re.MULTILINE)
    assert len(declarados) >= 15
    for nombre in declarados:
        assert re.search(rf"`{nombre}`", despliegue), nombre
        assert nombre in arquitectura, nombre


def test_the_privacy_document_lists_the_gateways_tables() -> None:
    privacidad = (RAIZ / "docs/privacidad.md").read_text(encoding="utf-8")
    for tabla in ("mensajeria_vinculos", "mensajeria_codigos", "mensajeria_procesados", "mensajeria_entregas"):
        assert f"`{tabla}`" in privacidad, tabla
