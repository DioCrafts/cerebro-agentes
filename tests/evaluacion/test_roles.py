"""Prompt roles whose text lives in other packages (the brain, the weekly pass, the memory): lazy, optional."""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
import shutil
import sys
import types

import pytest

from asistente.nucleo.agent.prompts import AGENT_SYSTEM_PROMPT, MODULES_ADDENDUM
from asistente.nucleo.evaluacion import variantes as v

CEREBRO = "asistente.nucleo.cerebro.prompts"
REPASO = "asistente.nucleo.repaso.prompts"
RECUERDOS = "asistente.nucleo.recuerdos.prompts"


@pytest.fixture
def paquetes(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, types.ModuleType]]:
    """Stand-in modules for the three packages (whatever the real ones say today)."""
    falsos: dict[str, types.ModuleType] = {}
    for nombre, atributos in {
        CEREBRO: {
            "CEREBRO_SYSTEM_PROMPT": "Eres el cerebro del hogar. Especialistas:\n{especialistas}",
            "INTENCION_SYSTEM_PROMPT": "Entiende la consulta.",
            "SINTESIS_SYSTEM_PROMPT": "Une los informes.",
        },
        REPASO: {"REPASO_SYSTEM_PROMPT": "Resume la semana."},
        RECUERDOS: {"RESUMEN_SYSTEM_PROMPT": "Resume la conversación."},
    }.items():
        modulo = types.ModuleType(nombre)
        for clave, valor in atributos.items():
            setattr(modulo, clave, valor)
        monkeypatch.setitem(sys.modules, nombre, modulo)
        falsos[nombre] = modulo
    yield falsos


def _sin(monkeypatch: pytest.MonkeyPatch, nombre: str) -> None:
    """Make ``import nombre`` fail (the package is not installed)."""
    monkeypatch.setitem(sys.modules, nombre, None)  # type: ignore[arg-type]


def test_the_roles_and_where_their_text_lives() -> None:
    assert v.ROLES[:3] == ("agente", "modulos", "especialista")
    assert set(v.ROLES) == {
        "agente",
        "modulos",
        "especialista",
        "cerebro",
        "cerebro_intencion",
        "cerebro_sintesis",
        "repaso",
        "memoria_resumen",
    }
    assert v.origen_de("cerebro") == (CEREBRO, "CEREBRO_SYSTEM_PROMPT")
    assert v.origen_de("repaso") == (REPASO, "REPASO_SYSTEM_PROMPT")
    assert v.origen_de("memoria_resumen") == (RECUERDOS, "RESUMEN_SYSTEM_PROMPT")
    assert v.origen_de("agente") == ("asistente.nucleo.agent.prompts", "AGENT_SYSTEM_PROMPT")


def test_external_roles_are_read_lazily(paquetes: dict[str, types.ModuleType]) -> None:
    assert v.texto_integrado("cerebro").startswith("Eres el cerebro del hogar")
    assert v.prompt_activo("repaso") == "Resume la semana."
    assert set(v.roles_disponibles()) == set(v.ROLES)
    # The local roles are exactly what they always were.
    assert v.prompt_activo("agente") == AGENT_SYSTEM_PROMPT and v.prompt_activo("modulos") == MODULES_ADDENDUM


def test_a_missing_package_makes_the_role_unavailable_with_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _sin(monkeypatch, CEREBRO)
    assert not v.rol_disponible("cerebro") and "cerebro" not in v.roles_disponibles()
    with pytest.raises(v.RolNoDisponibleError, match="no está disponible") as error:
        v.prompt_activo("cerebro")
    assert isinstance(error.value, LookupError) and error.value.rol == "cerebro"
    # A role that is unavailable and not registered is not a registry problem (nothing to register here).
    assert not [p for p in v.comprobar_integrados() if "«cerebro»" in p]
    # The base variant and its fingerprints simply leave it out; the local roles are untouched.
    assert "cerebro" not in v.huellas_de(v.VARIANTE_BASE) and "agente" in v.huellas_de(v.VARIANTE_BASE)
    assert v.prompt_activo("agente") == AGENT_SYSTEM_PROMPT


def test_a_module_without_the_attribute_is_unavailable_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, REPASO, types.ModuleType(REPASO))
    with pytest.raises(v.RolNoDisponibleError, match="REPASO_SYSTEM_PROMPT"):
        v.texto_integrado("repaso")


def test_a_variant_overrides_an_external_role(paquetes: dict[str, types.ModuleType]) -> None:
    variante = v.Variante(
        id="cerebro-breve",
        prompts={"cerebro": "cerebro@breve", "repaso": "repaso@corto"},
        textos={"cerebro@breve": "Sé breve. {especialistas}", "repaso@corto": "Tres líneas."},
    )
    assert variante.roles_cambiados() == ["cerebro", "repaso"] and not variante.es_base()
    with v.usar_variante(variante):
        assert v.prompt_activo("cerebro") == "Sé breve. {especialistas}"
        assert v.prompt_activo("repaso") == "Tres líneas."
        assert v.prompt_activo("agente") == AGENT_SYSTEM_PROMPT
    # Template fields are checked against the integrated text: a broken one falls back to it.
    mala = v.Variante(id="mala", prompts={"cerebro": "cerebro@x"}, textos={"cerebro@x": "Sin campos."})
    with pytest.raises(v.VarianteInvalidaError, match="especialistas"):
        v.texto_prompt(mala, "cerebro")
    with v.usar_variante(mala):
        assert v.prompt_activo("cerebro").startswith("Eres el cerebro del hogar")
    completa = v.autocontenida(variante)
    assert completa.textos == {"cerebro@breve": "Sé breve. {especialistas}", "repaso@corto": "Tres líneas."}
    assert set(completa.huellas) == set(v.ROLES)


def test_unregistered_roles_use_the_integrado_alias_and_are_reported(paquetes: dict[str, types.ModuleType]) -> None:
    if v.INTEGRADOS.get("cerebro") is None:  # until the registry is synchronised
        assert v.version_integrada("cerebro") == "cerebro@integrado"
        assert any("«cerebro» y no está registrado" in p for p in v.comprobar_integrados())
        assert "cerebro" in v.pendientes_de_registro()
    assert v.es_integrada("cerebro", "cerebro@integrado")
    assert v.Variante(id="x", prompts={"cerebro": "cerebro@integrado"}).es_base()


def test_registering_a_new_role(tmp_path: Path, paquetes: dict[str, types.ModuleType]) -> None:
    antes = v.REGISTRO_PROMPTS.read_text(encoding="utf-8")
    registro = tmp_path / "registro.json"
    shutil.copy(v.REGISTRO_PROMPTS, registro)
    datos = json.loads(antes)
    datos.pop("memoria_resumen", None)  # as if it had never been registered
    registro.write_text(json.dumps(datos), encoding="utf-8")
    nueva = v.registrar_version("memoria_resumen", nota="Primera versión", ruta=registro)
    assert nueva.version == "memoria_resumen@1"
    assert v.cargar_registro(registro)["memoria_resumen"][-1].huella == v.huella("Resume la conversación.")
    with pytest.raises(ValueError, match="no ha cambiado"):
        v.registrar_version("memoria_resumen", nota="otra", ruta=registro)
    # The repository's registry was not touched.
    assert v.REGISTRO_PROMPTS.read_text(encoding="utf-8") == antes


def test_an_active_variant_with_an_unavailable_role_still_applies_the_others(
    variante_activa_aislada: Path, paquetes: dict[str, types.ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidata = v.Variante(
        id="mixta",
        prompts={"agente": "agente@m", "cerebro": "cerebro@m"},
        textos={"agente@m": "Agente mixto.", "cerebro@m": "Cerebro mixto. {especialistas}"},
    )
    v.escribir_activa(candidata, frente_a="base", resumen=None)
    _sin(monkeypatch, CEREBRO)  # e.g. a deployment without the brain
    v._cache.clear()
    activa = v.variante_activa()
    assert activa.id == "mixta" and v.prompt_activo("agente") == "Agente mixto."
    # The embedded text of the unavailable role is still what the variant says.
    assert v.texto_prompt(activa, "cerebro") == "Cerebro mixto. {especialistas}"
