"""The council's prompt roles: read lazily from the council's package, versionable like every other prompt."""

from __future__ import annotations

from collections.abc import Iterator
import sys
import types

import pytest

from asistente.nucleo.evaluacion import variantes as v

CONSEJO = "asistente.nucleo.consejo.prompts"
EXPEDIENTE = "asistente.nucleo.consejo.prompts_expediente"
ROLES = ("consejo_miembro", "consejo_critica", "consejo_revision", "consejo_presidente", "consejo_expediente")


def test_the_roles_point_at_the_councils_modules() -> None:
    assert set(ROLES) <= set(v.ROLES)
    assert v.origen_de("consejo_miembro") == (CONSEJO, "MIEMBRO_SYSTEM_PROMPT")
    assert v.origen_de("consejo_critica") == (CONSEJO, "CRITICA_SYSTEM_PROMPT")
    assert v.origen_de("consejo_revision") == (CONSEJO, "REVISION_SYSTEM_PROMPT")
    assert v.origen_de("consejo_presidente") == (CONSEJO, "PRESIDENTE_SYSTEM_PROMPT")
    assert v.origen_de("consejo_expediente") == (EXPEDIENTE, "EXPEDIENTE_SYSTEM_PROMPT")


def test_the_real_texts_are_read() -> None:
    from asistente.nucleo.consejo import prompts, prompts_expediente

    assert v.texto_integrado("consejo_miembro") == prompts.MIEMBRO_SYSTEM_PROMPT
    assert v.texto_integrado("consejo_expediente") == prompts_expediente.EXPEDIENTE_SYSTEM_PROMPT
    assert set(ROLES) <= set(v.roles_disponibles())


@pytest.fixture
def sin_consejo(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setitem(sys.modules, CONSEJO, None)  # type: ignore[arg-type]
    monkeypatch.setitem(sys.modules, EXPEDIENTE, None)  # type: ignore[arg-type]
    yield


@pytest.mark.usefixtures("sin_consejo")
def test_without_the_council_the_roles_are_unavailable_not_broken() -> None:
    for rol in ROLES:
        assert not v.rol_disponible(rol)  # type: ignore[arg-type]
        with pytest.raises(v.RolNoDisponibleError, match="no está disponible"):
            v.prompt_activo(rol)  # type: ignore[arg-type]
    # The rest of the prompts are untouched.
    assert v.rol_disponible("agente") and "consejo_miembro" not in v.huellas_de(v.VARIANTE_BASE)


def test_a_variant_can_change_a_council_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    falso = types.ModuleType(CONSEJO)
    for atributo in (
        "MIEMBRO_SYSTEM_PROMPT",
        "CRITICA_SYSTEM_PROMPT",
        "REVISION_SYSTEM_PROMPT",
        "PRESIDENTE_SYSTEM_PROMPT",
    ):
        setattr(falso, atributo, f"Texto de {atributo}.")
    monkeypatch.setitem(sys.modules, CONSEJO, falso)
    variante = v.Variante(
        id="critica-estricta",
        prompts={"consejo_critica": "consejo_critica@estricta"},
        textos={"consejo_critica@estricta": "Critica con la rúbrica, sin piedad."},
    )
    assert variante.roles_cambiados() == ["consejo_critica"]
    with v.usar_variante(variante):
        assert v.prompt_activo("consejo_critica") == "Critica con la rúbrica, sin piedad."
        assert v.prompt_activo("consejo_miembro") == "Texto de MIEMBRO_SYSTEM_PROMPT."
    assert v.prompt_activo("consejo_critica") == "Texto de CRITICA_SYSTEM_PROMPT."
