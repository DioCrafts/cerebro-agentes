"""El texto consolidado por bloques del BOE: lectura, versiones, jerarquía, troceado por artículo y remisiones."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lxml")

from asistente.conocimiento.boe.bloques import (  # noqa: E402
    Norma,
    Remision,
    es_xml_de_bloques,
    leer_norma,
    numero_de_articulo,
    remisiones,
    trocear,
)
from asistente.conocimiento.processor.strategies.boe_bloques import (  # noqa: E402
    BoeBloquesStrategy,
    cargar_norma,
    fragmentos_de,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "boe_bloques" / "BOE-A-2099-1.boe.xml"


@pytest.fixture(scope="module")
def norma() -> Norma:
    return cargar_norma(FIXTURE)


def _bloque(norma: Norma, bloque_id: str):
    return next(b for b in norma.bloques if b.bloque_id == bloque_id)


def test_lee_preceptos_con_numero_rotulo_y_jerarquia(norma: Norma) -> None:
    assert (norma.boe_id, norma.sigla, norma.actualizada) == ("BOE-A-2099-1", "LDP", "2024-06-10")
    assert [b.bloque_id for b in norma.preceptos] == ["a1", "a2", "a3bis", "daprimera"]
    a1, a3bis, da = _bloque(norma, "a1"), _bloque(norma, "a3bis"), _bloque(norma, "daprimera")
    assert a1.numero == "1" and a3bis.numero == "3 bis" and da.numero is None
    assert a1.jerarquia == ("TÍTULO I. Deducciones", "CAPÍTULO I. Vivienda")
    # A new chapter replaces the previous one under the same title; a provision has no heading above it.
    assert a3bis.jerarquia == ("TÍTULO I. Deducciones", "CAPÍTULO II. Límites")
    assert da.jerarquia == ()
    assert a1.vigente is not None and a1.vigente.rotulo == "Artículo 1. Deducción por alquiler."


def test_versiones_ordenadas_con_su_intervalo_de_vigencia(norma: Norma) -> None:
    a1 = _bloque(norma, "a1")
    assert [(v.vigente_desde, v.vigente_hasta) for v in a1.versiones] == [
        ("2020-01-02", "2023-01-01"),
        ("2023-01-01", None),
    ]
    assert "10 por ciento" in a1.version_en("2022-12-31").texto
    assert "15 por ciento" in a1.version_en("2023-01-01").texto
    assert a1.version_en("2019-12-31") is None  # the law did not exist yet
    # The amendment note is kept apart from the text, and the table is one line per row.
    assert a1.vigente.notas and "Ley 9/2022" in a1.vigente.notas[0]
    assert "Ley 9/2022" not in a1.vigente.texto
    assert "Hasta 6.000 | 15" in a1.vigente.texto


def test_un_articulo_derogado_se_reconoce(norma: Norma) -> None:
    a2 = _bloque(norma, "a2")
    assert a2.vigente.sin_contenido and not a2.versiones[0].sin_contenido


def test_trocear_pone_la_cabecera_y_respeta_el_tamano(norma: Norma) -> None:
    a1 = _bloque(norma, "a1")
    trozos = trocear(norma, a1, a1.vigente, maximo=160)
    assert len(trozos) >= 2
    assert all(len(t.cuerpo) <= 160 for t in trozos)
    assert trozos[0].texto.startswith("LDP · Artículo 1. Deducción por alquiler\n")
    assert trozos[0].apartados == ("1",) and "2" in trozos[-1].apartados
    # Nothing of the body is lost in the cut.
    assert " ".join(t.cuerpo for t in trozos).replace("\n", " ").split() == a1.vigente.texto.replace("\n", " ").split()


def test_un_parrafo_enorme_se_parte_por_frases() -> None:
    xml = (
        '<response><data><bloque id="a1" tipo="precepto" titulo="Artículo 1">'
        '<version id_norma="X" fecha_publicacion="20200101" fecha_vigencia="20200101">'
        f'<p class="parrafo">{"Una frase bastante larga sobre el impuesto. " * 60}</p>'
        "</version></bloque></data></response>"
    ).encode()
    norma = leer_norma(xml, boe_id="BOE-A-2099-2")
    bloque = norma.preceptos[0]
    trozos = trocear(norma, bloque, bloque.vigente, maximo=400)
    assert len(trozos) > 5 and all(len(t.cuerpo) <= 400 for t in trozos)


def test_remisiones_propias_y_a_otra_norma(norma: Norma) -> None:
    a1 = _bloque(norma, "a1")
    assert remisiones(a1.vigente.texto, propio="1") == [Remision("3 bis"), Remision("20", "Ley 37/1992")]
    assert remisiones("Lo previsto en los artículos 7, 8 y 14 de esta Ley.") == [
        Remision("7"),
        Remision("8"),
        Remision("14"),
    ]
    assert remisiones("Según el artículo 5.", propio="5") == []


def test_numero_de_articulo() -> None:
    assert numero_de_articulo("Artículo 68") == "68"
    assert numero_de_articulo("Artículo 7 bis") == "7 bis"
    assert numero_de_articulo("Artículo 81.quáter") == "81 quater"
    assert numero_de_articulo("Disposición adicional cuarta") is None


def test_rechaza_lo_que_no_es_el_texto_por_bloques() -> None:
    assert not es_xml_de_bloques(b"<html><body>BOE</body></html>")
    with pytest.raises(ValueError, match="no es el texto consolidado"):
        leer_norma(b"<response><data/></response>", boe_id="X")
    with pytest.raises(ValueError, match="mal formado"):
        leer_norma(b"<response><bloque", boe_id="X")


def test_la_estrategia_reclama_solo_sus_ficheros(tmp_path: Path) -> None:
    estrategia = BoeBloquesStrategy()
    assert estrategia.probe(str(FIXTURE))
    pagina = tmp_path / "BOE-A-2099-1.html"
    pagina.write_text("<html><title>BOE-A-2099-1</title></html>", encoding="utf-8")
    assert not estrategia.probe(str(pagina))
    falso = tmp_path / "BOE-A-2099-3.boe.xml"
    falso.write_text("<otra_cosa/>", encoding="utf-8")
    assert not estrategia.probe(str(falso))
    assert list(estrategia.parse(str(falso))) == []  # unreadable: nothing, never an exception


def test_fragmentos_por_articulo_con_sus_metadatos(norma: Norma) -> None:
    fragmentos = fragmentos_de(norma, str(FIXTURE))
    por_bloque: dict[str, list] = {}
    for f in fragmentos:
        por_bloque.setdefault(f.metadatos["bloque_id"], []).append(f)
    assert set(por_bloque) == {"a1", "a2", "a3bis", "daprimera"}
    a1 = por_bloque["a1"][0].metadatos
    assert a1["chunk_id"] == "BOE-A-2099-1:a1:2023-01-01:0"  # by norm, not by path
    assert a1["norma"] == "LDP" and a1["articulo"] == "1" and a1["source_authority"] == "BOE"
    assert a1["source_url"] == "https://www.boe.es/buscar/act.php?id=BOE-A-2099-1#a1"
    assert a1["dl_meta"]["headings"][-1] == "Artículo 1. Deducción por alquiler."
    assert "vigente_hasta" not in a1 and "fecha_actualizacion" not in a1
    # The repealed article is indexed with its last text, dated: it stopped applying on 2023-01-01.
    a2 = por_bloque["a2"][0]
    assert "5 por ciento" in a2.texto and a2.metadatos["vigente_hasta"] == "2023-01-01"
    assert len({f.metadatos["chunk_id"] for f in fragmentos}) == len(fragmentos)
