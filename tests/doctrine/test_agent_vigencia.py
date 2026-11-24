"""The agent with a doctrine graph: validity alerts, conflicts and "no firm criterion"."""

from datetime import date

from click.testing import CliRunner
import pytest

from asistente.conocimiento.doctrine.citas import Documento
from asistente.conocimiento.doctrine.grafo import Arista, DoctrineGraph
from asistente.conocimiento.fragmento import FragmentoRecuperado
from asistente.nucleo.agent.agent import AgentMode, TaxAgent
from asistente.nucleo.agent.evidence import EvidenceLedger
from asistente.nucleo.agent.providers.base import ToolCall
from asistente.nucleo.agent.tools import Toolbox
from tests.agent.fakes import FakeSearcher, ScriptedProvider, text_turn, tool_turn, verdict

OLD = FragmentoRecuperado(
    texto="No procede la exención por reinversión cuando se emplea financiación ajena.",
    metadatos={
        "title": "Consulta V1234-19",
        "source": "dgt-vinculantes/2019/V1234-19.html",
        "num_consulta": "V1234-19",
        "fecha_resolucion": "2019-05-10",
        "vinculante": True,
        "chunk_id": "old",
    },
)
RULING = FragmentoRecuperado(
    texto="Procede la exención por reinversión aunque se emplee financiación ajena.",
    metadatos={
        "title": "STS de 3 de junio de 2020",
        "source": "ts/STS_1500_2020.html",
        "source_authority": "TS_SALA_TERCERA",
        "ecli": "ES:TS:2020:1500",
        "numero_recurso": "1234/2019",
        "fecha_resolucion": "2020-06-03",
        "chunk_id": "ruling",
    },
)
TEAC_A = FragmentoRecuperado(
    texto="La deducción exige factura completa.",
    metadatos={
        "title": "TEAC 00/100/2021",
        "source": "teac/a.html",
        "source_authority": "TEAC",
        "numero_resolucion": "00/100/2021",
        "fecha_resolucion": "2021-02-01",
        "chunk_id": "a",
    },
)
TEAC_B = FragmentoRecuperado(
    texto="La deducción no exige factura completa si el gasto se acredita por otros medios.",
    metadatos={
        "title": "TEAC 00/200/2022",
        "source": "teac/b.html",
        "source_authority": "TEAC",
        "numero_resolucion": "00/200/2022",
        "fecha_resolucion": "2022-03-01",
        "chunk_id": "b",
    },
)
SEARCH = ("buscar_fuentes", {"consulta": "exención reinversión financiación ajena"})


@pytest.fixture
def graph() -> DoctrineGraph:
    g = DoctrineGraph(":memory:")
    g.upsert(Documento("DGT:V1234-19", "DGT", fecha=date(2019, 5, 10)), en_corpus=True)
    g.upsert(
        Documento("TS:ECLI:ES:TS:2020:1500", "TS", alias=["TS:REC:1234/2019"], fecha=date(2020, 6, 3)), en_corpus=True
    )
    g.upsert(Documento("TEAC:00/00100/2021", "TEAC", fecha=date(2021, 2, 1)), en_corpus=True)
    g.upsert(Documento("TEAC:00/00200/2022", "TEAC", fecha=date(2022, 3, 1)), en_corpus=True)
    g.add_edge(
        Arista(
            origen="TS:ECLI:ES:TS:2020:1500",
            destino="DGT:V1234-19",
            relacion="supera",
            confianza=0.85,
            evidencia="cambio de criterio",
            metodo="llm",
        )
    )
    g.add_edge(
        Arista(
            origen="TEAC:00/00200/2022",
            destino="TEAC:00/00100/2021",
            relacion="contradice",
            confianza=0.8,
            evidencia="no comparte el criterio",
            metodo="llm",
        )
    )
    return g


def _call(name: str, **arguments) -> ToolCall:
    return ToolCall(id="c", name=name, arguments=arguments)


def test_search_results_carry_validity_and_alerts(graph: DoctrineGraph) -> None:
    toolbox = Toolbox(searcher=FakeSearcher([OLD, RULING]), ledger=EvidenceLedger(), graph=graph)

    outcome = toolbox.execute(_call("buscar_fuentes", consulta="reinversión"))

    assert "VIGENCIA=SUPERADA" in outcome.content
    assert "ALERTAS DE VIGENCIA" in outcome.content
    assert "[F1] Consulta V1234-19: Superada por STS rec. 1234/2019 (03/06/2020)." in outcome.content
    assert "[F2] supera a [F1]" in outcome.content
    old, ruling = toolbox.ledger.get("F1"), toolbox.ledger.get("F2")
    assert (old.vigencia, ruling.vigencia) == ("superada", "vigente")
    assert "VIGENCIA=" not in ruling.header()  # current doctrine is not decorated

    # Without a graph nothing changes, and the validity tool is not offered.
    plain = Toolbox(searcher=FakeSearcher([OLD, RULING]), ledger=EvidenceLedger())
    assert "VIGENCIA" not in plain.execute(_call("buscar_fuentes", consulta="x")).content
    assert "consultar_vigencia" not in {spec.name for spec in plain.specs()}
    assert plain.execute(_call("consultar_vigencia", referencia="V1234-19")).is_error


def test_validity_tool_explains_a_documents_status(graph: DoctrineGraph) -> None:
    toolbox = Toolbox(searcher=FakeSearcher([]), ledger=EvidenceLedger(), graph=graph)
    assert "consultar_vigencia" in {spec.name for spec in toolbox.specs()}

    outcome = toolbox.execute(_call("consultar_vigencia", referencia="consulta V1234-19"))
    assert "estado: SUPERADA" in outcome.content and "la supera — «cambio de criterio»" in outcome.content
    assert "no una fuente citable" in outcome.content
    assert len(toolbox.ledger) == 0  # graph hints are never registered as evidence

    unknown = toolbox.execute(_call("consultar_vigencia", referencia="V9999-24"))
    assert "no consta en el grafo" in unknown.content
    assert toolbox.execute(_call("consultar_vigencia", referencia="la de la semana pasada")).is_error
    disputed = toolbox.execute(_call("consultar_vigencia", referencia="resolución TEAC 00/100/2021"))
    assert "estado: DISCUTIDA" in disputed.content


def _agent(provider: ScriptedProvider, docs: list[FragmentoRecuperado], graph: DoctrineGraph, **kwargs) -> TaxAgent:
    return TaxAgent(provider=provider, searcher=FakeSearcher(docs), graph=graph, **kwargs)


def test_answer_resting_on_superseded_doctrine_is_sent_back_then_downgraded(graph: DoctrineGraph) -> None:
    stale = "No procede la exención con financiación ajena [F1]."
    supported = verdict("respuesta", ("No procede la exención", ["F1"], "respaldada"))
    provider = ScriptedProvider([tool_turn(SEARCH), text_turn(stale), text_turn(stale)], [supported, supported])

    result = _agent(provider, [OLD, RULING], graph).run("¿Hay exención si reinvierto con hipoteca?")

    # Every claim matched its source, yet the source is overruled: not "verified".
    assert result.mode is AgentMode.PARTIALLY_VERIFIED and result.revisions == 1
    assert "Superada por STS rec. 1234/2019" in provider.user_messages[0]
    assert any("doctrina que consta como superada (F1)" in w for w in result.warnings)
    assert result.conflicts == ["[F1] Consulta V1234-19: Superada por STS rec. 1234/2019 (03/06/2020)."]
    assert result.citations[0].vigencia == "superada"


def test_citing_the_overruling_judgment_alongside_is_verified(graph: DoctrineGraph) -> None:
    answer = "La DGT lo negaba [F1], pero el Supremo declaró en 2020 que sí procede [F2]."
    provider = ScriptedProvider(
        [tool_turn(SEARCH), text_turn(answer)],
        [
            verdict(
                "respuesta",
                ("La DGT lo negaba", ["F1"], "respaldada"),
                ("El Supremo declaró que procede", ["F2"], "respaldada"),
            )
        ],
    )
    result = _agent(provider, [OLD, RULING], graph).run("¿Hay exención si reinvierto con hipoteca?")

    assert result.mode is AgentMode.VERIFIED and result.revisions == 0
    assert len(result.conflicts) == 1 and not result.sin_criterio_firme  # shown to the user, but not a defect


def test_disputed_doctrine_must_be_presented_as_unsettled(graph: DoctrineGraph) -> None:
    confident = "La deducción exige factura completa [F1]."
    ok = verdict("respuesta", ("Exige factura completa", ["F1"], "respaldada"))
    silent = ScriptedProvider([tool_turn(SEARCH), text_turn(confident)], [ok])
    result = _agent(silent, [TEAC_A, TEAC_B], graph, max_revisions=0).run("¿Necesito factura completa?")
    assert result.mode is AgentMode.PARTIALLY_VERIFIED
    assert any("no advierte de que no hay criterio firme" in w for w in result.warnings)

    honest_text = "No hay criterio firme: el TEAC exigió factura completa [F1] y después admitió otros medios [F2]."
    honest_verdict = {
        **verdict(
            "respuesta",
            ("Exigió factura completa", ["F1"], "respaldada"),
            ("Admitió otros medios", ["F2"], "respaldada"),
        ),
        "declara_falta_de_criterio": True,
    }
    honest = ScriptedProvider([tool_turn(SEARCH), text_turn(honest_text)], [honest_verdict])
    result = _agent(honest, [TEAC_A, TEAC_B], graph).run("¿Necesito factura completa?")
    assert result.mode is AgentMode.VERIFIED and result.sin_criterio_firme
    assert len(result.conflicts) == 2 and result.warnings == []


def test_cli_builds_the_graph_from_the_index_and_lists_what_to_acquire(tmp_path) -> None:
    from asistente.conocimiento.indice import Fragmento, IndiceHibrido
    from asistente.conocimiento.indice.ingesta import ruta_indice
    from asistente.plataforma.cli.doctrine_graph import cli

    cites_missing = FragmentoRecuperado(
        texto="Conforme a la sentencia del Tribunal Supremo de 5 de mayo de 2021 (rec. 999/2020), procede.",
        metadatos={"source_file": "teac/c.html", "source_authority": "TEAC", "numero_resolucion": "00/300/2022"},
    )

    index = tmp_path / "indice"
    graph_path = str(tmp_path / "g.sqlite3")
    runner = CliRunner()

    missing = runner.invoke(cli, ["build", "--index-path", str(index), "--graph", graph_path])
    assert missing.exit_code != 0 and "indice.sqlite" in missing.output  # no index yet: a clear error

    corpus = [OLD, RULING, cites_missing]
    with IndiceHibrido(ruta_indice(index)) as indice:
        indice.anadir([Fragmento(str(i), d.texto, dict(d.metadatos)) for i, d in enumerate(corpus)], [[1.0, 0.0]] * 3)
    built = runner.invoke(cli, ["build", "--index-path", str(index), "--graph", graph_path])
    assert built.exit_code == 0, built.output
    assert '"documentos_de_doctrina": 3' in built.output

    wanted = runner.invoke(cli, ["wanted", "--graph", graph_path])
    assert "STS rec. 999/2020 (05/05/2021)" in wanted.output
    status = runner.invoke(cli, ["status", "--graph", graph_path, "V1234-19"])
    assert "DGT:V1234-19: vigente" in status.output  # the ruling does not cite the consulta in this tiny corpus
    assert '"nodos_en_corpus": 3' in runner.invoke(cli, ["stats", "--graph", graph_path]).output


def test_supreme_court_sidecar_feeds_the_graph_and_the_evidence_header() -> None:
    """The ruling filter, the graph and the agent agree on the metadata contract."""
    from asistente.conocimiento.doctrine.citas import identify_document
    from asistente.conocimiento.doctrine.construccion import build_graph
    from asistente.conocimiento.doctrine.supremo import analizar_documento, es_objetivo_prioritario, sidecar_metadata

    html = """<html><body>
    <p>Roj: STS 1500/2020 - ECLI:ES:TS:2020:1500</p><p>Órgano: Tribunal Supremo. Sala de lo Contencioso</p>
    <p>Sección: 2</p><p>Fecha: 03/06/2020</p><p>Nº de Recurso: 1234/2019</p><p>Procedimiento: Recurso de casación</p>
    <p>Tipo de procedimiento: R. CASACION</p>
    <h3>ANTECEDENTES DE HECHO</h3><p>La liquidación del IRPF practicada por la Agencia Tributaria denegó la exención
    por reinversión en vivienda habitual. La cuestión que presenta interés casacional objetivo para la formación de la
    jurisprudencia consiste en determinar si procede la exención cuando se emplea financiación ajena.</p>
    <h3>FUNDAMENTOS DE DERECHO</h3><p>TERCERO.- Criterios interpretativos. Fijamos la siguiente doctrina: procede la
    exención por reinversión aunque se emplee financiación ajena, lo que supone un cambio de criterio respecto de la
    consulta V1234-19.</p><h3>FALLO</h3><p>Fijar los criterios interpretativos expresados en el fundamento jurídico tercero.</p>
    </body></html>"""
    sentencia, base = analizar_documento(html, source_url="https://www.poderjudicial.es/search/doc/1")
    assert es_objetivo_prioritario(sentencia) and sentencia.fija_doctrina and sentencia.prioridad == 100
    sidecar = sidecar_metadata(sentencia, base)

    doc = identify_document(sidecar)
    assert doc.clave == "TS:ECLI:ES:TS:2020:1500" and "TS:REC:1234/2019" in doc.alias and doc.fija_doctrina

    g = DoctrineGraph(":memory:")
    ruling_chunk = FragmentoRecuperado(
        texto=sentencia.doctrina, metadatos={**sidecar, "title": "STS 3-6-2020", "source_file": "ts/a.html"}
    )
    build_graph([OLD, ruling_chunk], g)
    assert g.vigencia("DGT:V1234-19").estado == "superada"
    assert g.get("TS:REC:1234/2019").fija_doctrina

    toolbox = Toolbox(searcher=FakeSearcher([OLD, ruling_chunk]), ledger=EvidenceLedger(), graph=g)
    content = toolbox.execute(_call("buscar_fuentes", consulta="reinversión")).content
    assert "fija_doctrina=sí" in content and "Superada por STS rec. 1234/2019 (03/06/2020)" in content
