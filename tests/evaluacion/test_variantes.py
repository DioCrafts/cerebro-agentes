"""Versioned prompts and the active variant: by default the agent is exactly what it was."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hacienda_gpt.agent.agent import TaxAgent
from hacienda_gpt.agent.prompts import (
    AGENT_SYSTEM_PROMPT,
    CASE_TOOLS_ADDENDUM,
    MODULES_ADDENDUM,
    SPECIALIST_SYSTEM_PROMPT,
)
from hacienda_gpt.evaluacion import variantes as v
from tests.agent.fakes import FakeSearcher, ScriptedProvider, corpus, text_turn, verdict


def _system(provider: ScriptedProvider) -> str:
    return str(provider.started["system"])


def _run(**kwargs) -> ScriptedProvider:  # type: ignore[no-untyped-def]
    provider = ScriptedProvider([text_turn("¿De qué comunidad eres?")], [verdict("pregunta_aclaratoria")])
    TaxAgent(provider=provider, searcher=FakeSearcher(corpus())).run("Hola", **kwargs)
    return provider


def test_the_prompt_registry_and_the_code_agree() -> None:
    """prompts/registro.json records the content hash of every prompt in hacienda_gpt/agent/prompts.py.

    It is not frozen text: when a prompt is edited, register the new version (it gets the next id and its hash)
    with `python -m hacienda_gpt.cli.evaluacion prompts registrar --rol <rol> --informe <informe>` after
    evaluating it, or `prompts sincronizar --motivo "<por qué no se evaluó>"`. This test only fails while the
    registry and the code disagree.
    """
    assert v.comprobar_integrados() == []
    registro = v.cargar_registro()
    for rol in v.ROLES:
        assert registro[rol][-1].huella == v.huella(v.texto_integrado(rol))
    assert v.texto_integrado("agente") == AGENT_SYSTEM_PROMPT
    assert v.texto_integrado("modulos") == MODULES_ADDENDUM
    assert v.texto_integrado("especialista") == SPECIALIST_SYSTEM_PROMPT


def test_a_changed_prompt_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(v._prompts, "AGENT_SYSTEM_PROMPT", AGENT_SYSTEM_PROMPT + " ")
    (problema,) = v.comprobar_integrados()
    assert v.INTEGRADOS["agente"].version in problema and "promote" in problema and "sincronizar" in problema


def test_without_an_active_file_the_agent_runs_exactly_as_before(variante_activa_aislada: Path) -> None:
    assert not variante_activa_aislada.exists()
    assert v.variante_activa() == v.VARIANTE_BASE and v.VARIANTE_BASE.es_base()
    assert v.argumentos_proveedor() == {}
    assert _system(_run()) == AGENT_SYSTEM_PROMPT


def test_an_active_variant_changes_the_prompt_and_a_broken_one_falls_back(variante_activa_aislada: Path) -> None:
    candidata = v.Variante(
        id="breve", prompts={"agente": "agente@breve"}, textos={"agente@breve": "Eres un asesor breve."}, modelo="m-1"
    )
    v.escribir_activa(candidata, frente_a="base", resumen={"tasa_candidata": 1.0})
    assert v.variante_activa().id == "breve"
    assert v.argumentos_proveedor() == {"model": "m-1"}
    assert _system(_run()) == "Eres un asesor breve."

    variante_activa_aislada.write_text("{no json", encoding="utf-8")
    assert v.variante_activa() == v.VARIANTE_BASE
    assert _system(_run()) == AGENT_SYSTEM_PROMPT


def test_usar_variante_overrides_the_active_one_only_inside_the_block() -> None:
    otra = v.Variante(id="otra", prompts={"agente": "agente@x"}, textos={"agente@x": "Otro prompt."})
    with v.usar_variante(otra):
        assert v.prompt_activo("agente") == "Otro prompt."
    assert v.prompt_activo("agente") == AGENT_SYSTEM_PROMPT


def test_template_prompts_must_keep_their_fields(tmp_path: Path) -> None:
    mala = v.Variante(id="mala", prompts={"modulos": "modulos@mala"}, textos={"modulos@mala": "Sin campos."})
    with pytest.raises(v.VarianteInvalidaError, match="especialistas"):
        v.texto_prompt(mala, "modulos")
    # In the agent, a bad variant never breaks a turn: the integrated text is used.
    with v.usar_variante(mala):
        assert v.prompt_activo("modulos") == MODULES_ADDENDUM
    buena = v.Variante(
        id="buena", prompts={"modulos": "modulos@b"}, textos={"modulos@b": "Especialistas:\n{especialistas}"}
    )
    assert v.texto_prompt(buena, "modulos").endswith("{especialistas}")


def test_variant_files_load_prompt_texts_next_to_them(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "agente@corto.txt").write_text("Prompt corto.", encoding="utf-8")
    ruta = tmp_path / "candidata.json"
    ruta.write_text(json.dumps({"id": "corto", "prompts": {"agente": "agente@corto"}}), encoding="utf-8")
    variante = v.cargar_variante(ruta)
    assert variante.textos == {"agente@corto": "Prompt corto."}
    assert variante.prompts["modulos"] == "modulos@1"
    with pytest.raises(v.VarianteInvalidaError):
        v.cargar_variante(tmp_path / "no-existe.json")
    with pytest.raises(ValueError):
        v.Variante(id="x", prompts={"agente": "modulos@1"})


def test_case_addendum_still_follows_the_active_prompt() -> None:
    from hacienda_gpt.decision.schemas import CaseState

    case = CaseState(case_id="c1", user_id="u", jurisdiction="ES", tax_period="2025")
    assert _system(_run(case=case)) == AGENT_SYSTEM_PROMPT + CASE_TOOLS_ADDENDUM


def test_the_active_variant_references_its_prompts_by_content(
    variante_activa_aislada: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidata = v.Variante(id="c", prompts={"agente": "agente@c"}, textos={"agente@c": "Texto evaluado."})
    v.escribir_activa(candidata, frente_a="base", resumen=None)
    activa = v.variante_activa()
    assert activa.huellas["agente"] == v.huella("Texto evaluado.")
    assert activa.huellas["modulos"] == v.huella(MODULES_ADDENDUM)
    assert v.desajustes(activa) == []

    # An embedded text that is not the evaluated one is refused (the agent then uses the integrated text).
    manipulada = activa.model_copy(update={"textos": {"agente@c": "Otro texto."}})
    with pytest.raises(v.VarianteInvalidaError, match="no es el que se evaluó"):
        v.texto_prompt(manipulada, "agente")

    # prompts.py changing after the evaluation is reported, never fatal: the code's text is used.
    monkeypatch.setattr(v._prompts, "MODULES_ADDENDUM", MODULES_ADDENDUM + "\nNuevo: {especialistas}")
    (aviso,) = v.desajustes(activa)
    assert "«modulos»" in aviso and "vuelve a evaluarla" in aviso
    assert v.texto_prompt(activa, "modulos").endswith("Nuevo: {especialistas}")
