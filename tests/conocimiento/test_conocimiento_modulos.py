"""Every module's legal data passes the ONE shared checker against the BOE extracts its tests keep.

The extracts are the ones each module's own tests already use (``tests/fixtures/modulos/<module>``…). The
check here is the one ``params-verify`` runs every week against today's BOE (``verificar_fichero``), so a module
whose own test is green is also green in the weekly verification, and the other way round.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from asistente.conocimiento import (
    CitaLegal,
    GrupoLegal,
    Ortografia,
    comprobar_grupo,
    fichero_legal,
    ficheros_legales,
    verificar_fichero,
)
from asistente.conocimiento.modelo import FRONTERA_PALABRA
from asistente.conocimiento.registro import DECLARANTES

TESTS = Path(__file__).resolve().parents[1]
MODULOS = TESTS / "fixtures" / "modulos"

# Declared file -> the folder of BOE extracts its module's tests use.
EXTRACTOS: dict[str, Path] = {
    "luz": MODULOS / "luz" / "boe",
    "caducidades": MODULOS / "caducidades",
    "ayudas_2026": MODULOS / "ayudas" / "boe",
    "hipoteca": MODULOS / "hipoteca",
    "hipoteca_deduccion": MODULOS / "hipoteca" / "deduccion_vivienda",
    "suscripciones": MODULOS / "suscripciones" / "boe",
    "correo": MODULOS / "correo" / "boe",
    "planificador": TESTS / "planificador" / "fixtures",
    "fiscal_obligacion": TESTS / "fixtures" / "boe_obligacion",
}
# Files with nothing to check against a law text, and why.
SIN_EXTRACTOS = {
    "hipoteca_indices": "serie del Banco de España: su lector comprueba cada resolución en XML",
    "fiscal_optimizador": "sin extractos en el repositorio: solo la verificación semanal contra el BOE",
}


def de_extractos(carpeta: Path) -> Callable[[str, str | None], str | None]:
    """A ``texto_de`` over a folder of extracts: ``<id>.txt`` / ``<id>.html``, a version as ``<id>_p<version>``.

    A norm read from the daily BOE (``version="diario"``) is kept as ``<id>.txt`` like the others.
    """

    def texto_de(boe_id: str, version: str | None = None) -> str | None:
        nombre = boe_id if version in (None, "diario") else f"{boe_id}_p{version}"
        for sufijo in (".html", ".txt"):
            path = carpeta / f"{nombre}{sufijo}"
            if path.exists():
                return path.read_text(encoding="utf-8")
        return None

    return texto_de


def test_every_declarant_declares_and_every_file_says_who_maintains_it() -> None:
    claves = {f.clave for f in ficheros_legales()}
    assert set(EXTRACTOS) | set(SIN_EXTRACTOS) == claves
    # Only the core's own files are listed by hand; domains and capabilities declare theirs.
    assert DECLARANTES == ("asistente.nucleo.planificador.textos",)
    for fichero in ficheros_legales():
        # Either the loop re-encodes it, or the declaration says why a person does.
        assert (fichero.recodificable is not None) != bool(fichero.solo_persona), fichero.clave
        assert fichero.ruta is None or fichero.ruta.exists(), fichero.clave
    assert [f.clave for f in ficheros_legales() if f.recodificable] == ["caducidades"]


@pytest.mark.parametrize("clave", sorted(EXTRACTOS))
def test_each_modules_groups_pass_the_shared_checker_against_its_fixtures(clave: str) -> None:
    fichero = fichero_legal(clave)
    resultados = verificar_fichero(fichero, de_extractos(EXTRACTOS[clave]))
    assert resultados, clave
    malos = [(r.comprobacion.descripcion, r.faltan[:3]) for r in resultados if not r.ok]
    assert malos == []
    # And offline (no law text): every figure is spelled in its passages.
    assert fichero.comprobar() == []


def test_the_series_of_reference_rates_is_consistent_offline() -> None:
    assert fichero_legal("hipoteca_indices").comprobar() == []


def test_a_changed_law_fails_the_group_that_quotes_it_and_only_that_one() -> None:
    fichero = fichero_legal("caducidades")
    normal = de_extractos(EXTRACTOS["caducidades"])

    def cambiada(boe_id: str, version: str | None = None) -> str | None:
        texto = normal(boe_id, version)
        if texto and boe_id == "BOE-A-1980-22501":
            return texto.replace("al menos, un mes de anticipación", "al menos, dos meses de anticipación")
        return texto

    resultados = {r.comprobacion.ruta[0]: r for r in verificar_fichero(fichero, cambiada)}
    assert not resultados["seguro"].ok and "no aparece literal" in resultados["seguro"].faltan[0]
    assert all(r.ok for nombre, r in resultados.items() if nombre != "seguro")
    # A law that cannot be read is "sin texto", as for the tax files.
    sin_ley = {r.comprobacion.ruta[0]: r for r in verificar_fichero(fichero, lambda b, v=None: None)}
    assert sin_ley["seguro"].faltan == ["sin texto"]


def test_the_checker_reads_spellings_as_each_module_spells_them() -> None:
    ortografia = Ortografia(leer_cifra=lambda literal: {"dos": 2, "diez": 10}[literal.split()[0].lower()])
    ortografia = Ortografia(leer_cifra=ortografia.leer_cifra, frontera=FRONTERA_PALABRA)
    cita = CitaLegal(
        boe_id="BOE-A-1-1",
        articulo="art. 1",
        cita="El plazo será de dos meses y la prórroga de diez años.",
        cifras={"plazo": "dos meses", "prorroga": "diez años"},
    )
    grupo = GrupoLegal(nombre="regla", ruta=["regla"], cifras={"plazo": 2, "prorroga": 10}, citas=[cita])
    textos = {"BOE-A-1-1": "Artículo 1.\nEl plazo será de  dos meses\ny la prórroga de diez años."}
    assert comprobar_grupo(grupo, ortografia=ortografia, textos=textos, fuentes={"BOE-A-1-1"}) == []
    mal = grupo.model_copy(update={"cifras": {"plazo": 3, "prorroga": 10, "otra": 5}})
    assert comprobar_grupo(mal, ortografia=ortografia) == [
        "regla.plazo: el dato es 3 y la cita dice «dos meses»",
        "regla.otra: cifra sin evidencia",
    ]
    # A spelling must be a whole word ("dos" is not inside "doscientos") and the passage must be in its law.
    pegada = cita.model_copy(update={"cita": "El plazo será de doscientos meses y la prórroga de diez años."})
    problemas = comprobar_grupo(
        grupo.model_copy(update={"citas": [pegada]}), ortografia=ortografia, textos=textos, fuentes=set()
    )
    assert "regla: BOE-A-1-1 no figura en fuentes_principales" in problemas
    assert "regla: la cita de art. 1 no aparece literal en BOE-A-1-1" in problemas
    assert "regla.plazo: «dos meses» no aparece en la cita de art. 1" in problemas
    # A superseded wording is checked against that version's text, never today's.
    vieja = cita.model_copy(update={"version": "20121031"})
    problemas = comprobar_grupo(grupo.model_copy(update={"citas": [vieja]}), ortografia=ortografia, textos=textos)
    assert problemas == ["regla: no se ha aportado el texto de BOE-A-1-1@20121031"]


def test_legal_files_come_from_each_domains_hooks_and_each_capabilitys_declaration() -> None:
    from asistente.capacidades.registro import descubrir as descubrir_capacidades
    from asistente.nucleo.modulos.ganchos import ganchos_de
    from asistente.nucleo.modulos.registro import descubrir as descubrir_ambitos

    de_ambitos = {f.clave for _o, m in descubrir_ambitos()[0] for f in ganchos_de(m).ficheros_legales}
    de_capacidades = {f.clave for _o, c in descubrir_capacidades()[0] for f in getattr(c, "ficheros_legales", ())}
    assert de_capacidades == {"correo"}
    # The same coverage the hand-made list had: every domain's files, the mail's rules and the planner's passages.
    assert (
        {f.clave for f in ficheros_legales()}
        == de_ambitos | de_capacidades | {"planificador"}
        == {
            "luz",
            "caducidades",
            "ayudas_2026",
            "hipoteca",
            "hipoteca_deduccion",
            "hipoteca_indices",
            "suscripciones",
            "correo",
            "planificador",
            "fiscal_obligacion",
            "fiscal_optimizador",
        }
    )


def test_a_new_domains_legal_file_is_covered_without_listing_it(monkeypatch) -> None:
    from dataclasses import replace

    from asistente.conocimiento import registro as conocimiento_registro
    from asistente.nucleo.modulos import registro as modulos_registro
    from asistente.nucleo.modulos.ganchos import Ganchos

    nuevo = replace(fichero_legal("luz"), clave="agua_nueva", modulo="agua")

    class Agua:
        clave = "agua"
        ganchos = Ganchos(ficheros_legales=(nuevo,))

    real = modulos_registro.descubrir
    monkeypatch.setattr(modulos_registro, "descubrir", lambda: (real()[0] + [("agua", Agua())], {}))
    conocimiento_registro._declarados.cache_clear()
    try:
        assert "agua_nueva" in {f.clave for f in ficheros_legales()}
    finally:
        monkeypatch.undo()
        conocimiento_registro._declarados.cache_clear()
