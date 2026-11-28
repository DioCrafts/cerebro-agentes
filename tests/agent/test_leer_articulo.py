"""Las herramientas de lectura por identificador (``leer_articulo``, ``indice_norma``) y ``vigente_en`` en la
búsqueda, contra un índice real diminuto con una ley inventada."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sqlite_vec")
pytest.importorskip("lxml")

from asistente.conocimiento.indice.buscador import SqliteCorpusSearcher  # noqa: E402
from asistente.conocimiento.indice.ingesta import EscritorSqlite  # noqa: E402
from asistente.conocimiento.processor.strategies.boe_bloques import cargar_norma, fragmentos_de  # noqa: E402
from asistente.nucleo.agent.evidence import EvidenceLedger  # noqa: E402
from asistente.nucleo.agent.providers.base import ToolCall  # noqa: E402
from asistente.nucleo.agent.team import RESEARCH_TOOLS  # noqa: E402
from asistente.nucleo.agent.tools import Toolbox, fecha_de_vigencia  # noqa: E402
from tests.conocimiento.test_indice_articulos import EmbeddingsDePrueba  # noqa: E402
from .fakes import FakeSearcher  # noqa: E402

FUENTE = Path(__file__).resolve().parents[1] / "fixtures" / "boe_bloques" / "BOE-A-2099-1.boe.xml"


@pytest.fixture
def toolbox(tmp_path: Path) -> Toolbox:
    docs = fragmentos_de(cargar_norma(FUENTE), str(FUENTE))
    escritor = EscritorSqlite(tmp_path / "indice", EmbeddingsDePrueba())
    escritor.add_documents(docs, ids=[d.metadatos["chunk_id"] for d in docs])
    buscador = SqliteCorpusSearcher(escritor.indice, EmbeddingsDePrueba(), hibrido=True)
    return Toolbox(searcher=buscador, ledger=EvidenceLedger(), modulos=())


def _call(name: str, **arguments) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=arguments)


def test_se_ofrecen_solo_si_el_buscador_lee_por_articulos(toolbox: Toolbox) -> None:
    nombres = [spec.name for spec in toolbox.specs()]
    assert nombres[:3] == ["buscar_fuentes", "leer_articulo", "indice_norma"]
    sin_articulos = Toolbox(searcher=FakeSearcher([]), ledger=EvidenceLedger(), modulos=())
    assert [spec.name for spec in sin_articulos.specs()] == ["buscar_fuentes"]
    assert sin_articulos.execute(_call("leer_articulo", norma="LDP", articulo="1")).is_error
    # The researchers of the team may read by identifier too.
    assert {"leer_articulo", "indice_norma"} <= RESEARCH_TOOLS


def test_leer_articulo_devuelve_el_texto_citable_con_su_vigencia_y_remisiones(toolbox: Toolbox) -> None:
    outcome = toolbox.execute(_call("leer_articulo", norma="LDP", articulo="Artículo 1"))
    assert not outcome.is_error
    assert outcome.content.startswith("[F1]") and "15 por ciento" in outcome.content
    assert "tipo=normativa" in outcome.content and "rige_desde=2023-01-01" in outcome.content
    assert "Redacción vigente, en vigor desde el 2023-01-01." in outcome.content
    assert "Remite a los artículos de esta norma: 3 bis." in outcome.content
    assert "art. 20 de Ley 37/1992" in outcome.content
    assert "Última modificación según el BOE" in outcome.content
    fragmento = toolbox.ledger.get("F1")
    assert fragmento is not None and fragmento.locator.endswith("id=BOE-A-2099-1#a1")
    # Reading it again registers nothing new.
    assert "[F1]" in toolbox.execute(_call("leer_articulo", norma="BOE-A-2099-1", articulo="1")).content
    assert len(toolbox.ledger) == 1


def test_leer_articulo_en_una_fecha_da_la_redaccion_de_entonces(toolbox: Toolbox) -> None:
    outcome = toolbox.execute(_call("leer_articulo", norma="LDP", articulo="1", fecha="2022"))
    assert "10 por ciento" in outcome.content and "15 por ciento" not in outcome.content
    assert "TEXTO NO VIGENTE, rigió 2020-01-02→2023-01-01" in outcome.content
    assert "NO es la vigente hoy" in outcome.content
    # The current text is another fragment: both can be cited side by side.
    assert "[F2]" in toolbox.execute(_call("leer_articulo", norma="LDP", articulo="1")).content

    no_existia = toolbox.execute(_call("leer_articulo", norma="LDP", articulo="3 bis", fecha="2023-06-30"))
    assert not no_existia.is_error and "no existía el 2023-06-30" in no_existia.content
    assert "2024-07-01" in no_existia.content


def test_articulo_derogado_apartado_y_disposicion(toolbox: Toolbox) -> None:
    derogado = toolbox.execute(_call("leer_articulo", norma="LDP", articulo="2"))
    assert not derogado.is_error and "sin contenido desde el 2023-01-01" in derogado.content
    assert "rigió del 2020-01-02 al 2023-01-01" in derogado.content
    assert len(toolbox.ledger) == 0  # nothing citable was registered for an empty article

    apartado = toolbox.execute(_call("leer_articulo", norma="LDP", articulo="1", apartado="2"))
    assert "12.000 euros" in apartado.content and "15 por ciento" not in apartado.content
    assert toolbox.execute(_call("leer_articulo", norma="LDP", articulo="1", apartado="9")).is_error

    disposicion = toolbox.execute(_call("leer_articulo", norma="LDP", disposicion="disposición adicional primera"))
    assert "conservan su régimen" in disposicion.content


def test_leer_articulo_valida_sus_argumentos(toolbox: Toolbox) -> None:
    assert toolbox.execute(_call("leer_articulo", articulo="1")).is_error
    assert toolbox.execute(_call("leer_articulo", norma="LDP")).is_error
    assert toolbox.execute(_call("leer_articulo", norma="LDP", articulo="1", disposicion="x")).is_error
    assert toolbox.execute(_call("leer_articulo", norma="LDP", articulo="1", fecha="ayer")).is_error
    desconocida = toolbox.execute(_call("leer_articulo", norma="LIRPF", articulo="1"))
    assert desconocida.is_error and "LDP" in desconocida.content  # says which norms there are
    sin_articulo = toolbox.execute(_call("leer_articulo", norma="LDP", articulo="99"))
    assert sin_articulo.is_error and "indice_norma" in sin_articulo.content


def test_indice_norma(toolbox: Toolbox) -> None:
    normas = toolbox.execute(_call("indice_norma"))
    assert "LDP: Ley 1/2099" in normas.content and "4 preceptos" in normas.content

    indice = toolbox.execute(_call("indice_norma", norma="LDP"))
    lineas = indice.content.splitlines()
    assert lineas[1:5] == [
        "TÍTULO I. Deducciones",
        "  CAPÍTULO I. Vivienda",
        "    Artículo 1. Deducción por alquiler.",
        "    Artículo 2. Deducción por compra. (sin contenido)",
    ]
    assert "  CAPÍTULO II. Límites" in lineas and lineas.count("TÍTULO I. Deducciones") == 1
    assert lineas[-1] == "Disposición adicional primera. Arrendamientos anteriores."
    assert len(toolbox.ledger) == 0  # an index is not a source

    filtrado = toolbox.execute(_call("indice_norma", norma="LDP", contiene="limite"))
    assert "Artículo 3 bis. Límite conjunto." in filtrado.content and "Artículo 1." not in filtrado.content
    assert toolbox.execute(_call("indice_norma", norma="LIRPF")).is_error


def test_buscar_fuentes_con_vigente_en(toolbox: Toolbox) -> None:
    hoy = toolbox.execute(_call("buscar_fuentes", consulta="deducción por compra de vivienda"))
    assert "deducir el 5 por ciento" not in hoy.content
    entonces = toolbox.execute(_call("buscar_fuentes", consulta="deducción por compra de vivienda", vigente_en="2022"))
    assert "deducir el 5 por ciento" in entonces.content and "TEXTO NO VIGENTE" in entonces.content
    assert toolbox.execute(_call("buscar_fuentes", consulta="x", vigente_en="hace poco")).is_error


def test_fecha_de_vigencia() -> None:
    assert fecha_de_vigencia("2022") == "2022-12-31"
    assert fecha_de_vigencia(" 2022-06-30 ") == "2022-06-30"
    assert fecha_de_vigencia("2022-13-40") is None and fecha_de_vigencia("junio") is None
