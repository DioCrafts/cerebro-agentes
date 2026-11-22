"""Recuperador en árbol estilo PageIndex (FISCAL_RECUPERADOR=pageindex)."""

from __future__ import annotations

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
import pytest

from asistente.conocimiento.llm import arbol
from asistente.conocimiento.llm.arbol import (
    Consumo,
    RecuperadorArbol,
    SelectorLexico,
    SelectorLLM,
    construir_arboles,
    envolver_recuperador,
    navegar,
    ruta_headings,
)


def _chunk(fuente: str, headings: list[str], texto: str, *, via: str = "headings") -> Document:
    meta: dict = {"source_file": fuente, "norma": fuente.split("/")[-1].split(".")[0], "chunk_id": f"{fuente}{headings}"}
    if via == "dl_meta":
        meta["dl_meta"] = {"headings": headings}
    elif via == "section":
        meta["section"] = " — ".join(headings)
    else:
        meta["headings"] = headings
    return Document(page_content=texto, metadata=meta)


CORPUS = [
    _chunk("boe/LIVA.xml", ["Título VIII. Deducciones", "Artículo 99. Ejercicio del derecho a la deducción"],
           "Los sujetos pasivos deducirán las cuotas soportadas de las devengadas en cada periodo."),
    _chunk("boe/LIVA.xml", ["Título VI. Tipos", "Artículo 91. Tipos reducidos"],
           "Tipo del 4 por ciento al pan, libros y medicamentos.", via="dl_meta"),
    _chunk("boe/LGT.xml", ["Título III. Aplicación", "Artículo 66. Plazos de prescripción"],
           "Prescribe a los cuatro años el derecho a liquidar la deuda tributaria.", via="section"),
    _chunk("boe/LGT.xml", ["Título IV. Sanciones", "Artículo 191. Dejar de ingresar"],
           "Infracción por dejar de ingresar la deuda de la autoliquidación."),
]


class _Fijo(BaseRetriever):
    docs: list

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        return list(self.docs)


def test_ruta_headings_lee_dl_meta_headings_y_section() -> None:
    assert ruta_headings(CORPUS[1])[-1].startswith("Artículo 91")
    assert ruta_headings(CORPUS[2]) == ["Título III. Aplicación", "Artículo 66. Plazos de prescripción"]
    assert ruta_headings(Document(page_content="x", metadata={"section": "general"})) == []


def test_construir_arboles_un_arbol_por_documento_con_esquema_pageindex() -> None:
    raices = construir_arboles(CORPUS)
    assert [r.fuente for r in raices] == ["boe/LIVA.xml", "boe/LGT.xml"]
    liva = raices[0].a_dict()
    assert set(liva) == {"title", "node_id", "summary", "nodes"}
    assert [n["title"] for n in liva["nodes"]] == ["Título VIII. Deducciones", "Título VI. Tipos"]
    # El resumen de un nodo interno es su tabla de contenidos.
    assert "Artículo 99" in raices[0].nodes[0].summary
    assert len(raices[0].todos_los_docs()) == 2


def test_navegar_baja_hasta_el_articulo_y_cuenta_llamadas() -> None:
    consumo = Consumo()
    docs = navegar("¿Cuál es el plazo de prescripción de la deuda tributaria?", construir_arboles(CORPUS), SelectorLexico(), consumo)
    assert docs and "cuatro años" in docs[0].page_content
    assert consumo.llamadas >= 2 and consumo.tokens_entrada > 0


def test_navegar_se_abstiene_si_ningun_documento_trata_el_tema() -> None:
    assert navegar("¿Cómo recurro una multa de tráfico?", construir_arboles(CORPUS), SelectorLexico(), Consumo()) == []


def test_selector_llm_parsea_json_ignora_ids_inventados_y_cuenta_tokens() -> None:
    class LLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            return type("R", (), {"content": '{"node_ids": ["0001", "9999"]}'})()

    llm = LLM()
    raices = construir_arboles(CORPUS)
    consumo = Consumo()
    assert SelectorLLM(llm)("prescripción", raices, consumo) == ["0001"]
    assert consumo.llamadas == 1
    assert "DATO no confiable" in llm.prompts[0]


def test_envolver_recuperador_respeta_el_flag() -> None:
    base = _Fijo(docs=[])
    assert envolver_recuperador("faiss", base, CORPUS, SelectorLexico()) is base
    assert isinstance(envolver_recuperador("PageIndex", base, CORPUS, SelectorLexico()), RecuperadorArbol)
    with pytest.raises(ValueError, match="FISCAL_RECUPERADOR"):
        envolver_recuperador("bm42", base, CORPUS, SelectorLexico())


def test_valores_del_flag() -> None:
    assert arbol.RECUPERADORES == ("faiss", "pageindex")


def test_navegar_baja_hasta_el_articulo_aunque_la_raiz_tenga_chunks():
    from langchain_core.documents import Document

    from asistente.conocimiento.llm.arbol import Consumo, construir_arboles, navegar

    preambulo = [Document(page_content=f"Preámbulo {i}", metadata={"source_file": "ley", "headings": []})
                 for i in range(5)]
    art = Document(page_content="Exención por reinversión", metadata={
        "source_file": "ley", "headings": ["Ganancias patrimoniales", "Artículo 38. Reinversión"]})
    otro = Document(page_content="Rentas exentas", metadata={
        "source_file": "ley", "headings": ["Rentas exentas", "Artículo 7. Rentas exentas"]})
    raices = construir_arboles(preambulo + [art, otro])

    def elige_ganancias(consulta, candidatos, consumo):
        return [n.node_id for n in candidatos if "Ganancias" in n.title or "38" in n.title]

    docs = navegar("reinversión vivienda", raices, elige_ganancias, Consumo(), max_docs=3)
    assert docs[0] is art


def test_raiz_de_pagina_sin_documento_usa_su_primer_titulo() -> None:
    from langchain_core.documents import Document

    from asistente.conocimiento.llm.arbol import construir_arboles

    meta = {"source_file": "data/html/x/" + "a" * 32 + ".html", "norma": "AEAT"}
    docs = [
        Document(page_content="t", metadata={**meta, "headings": ["Certificados de residencia fiscal", "Cómo pedirlo"]}),
        Document(page_content="t", metadata={**meta, "headings": ["Certificados de residencia fiscal"]}),
    ]
    (raiz,) = construir_arboles(docs)
    assert raiz.title == "AEAT: Certificados de residencia fiscal"
