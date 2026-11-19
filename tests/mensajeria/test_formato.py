"""Rendering the brain's Markdown for a chat, and splitting long answers on sensible boundaries."""

from __future__ import annotations

from decimal import Decimal

import pytest

from asistente.nucleo.agent.agent import AgentCitation
from asistente.nucleo.cerebro.contrato import (
    AtribucionCifra,
    Cifra,
    InformeCerebro,
    Intencion,
    ResultadoPuerta,
    ResumenInforme,
)
from asistente.plataforma.mensajeria.formato import a_canal, consultados, enumerar, partir, texto_cifras, texto_fuentes


@pytest.mark.parametrize(
    ("markdown", "simplex", "texto"),
    [
        ("El plazo es de **dos años** [F2].", "El plazo es de *dos años* [F2].", "El plazo es de dos años [F2]."),
        ("Es *importante* y __urgente__.", "Es _importante_ y *urgente*.", "Es importante y urgente."),
        ("Usa `hipoteca_calcular` ~~ya no~~.", "Usa `hipoteca_calcular` ~ya no~.", "Usa hipoteca_calcular ya no."),
        ("## Resumen", "*Resumen*", "Resumen"),
        ("- uno\n- dos\n  - tres", "• uno\n• dos\n  • tres", "• uno\n• dos\n  • tres"),
        ("> cita del BOE", "› cita del BOE", "› cita del BOE"),
        (
            "[BOE](https://boe.es/x) y https://boe.es",
            "BOE (https://boe.es/x) y https://boe.es",
            "BOE (https://boe.es/x) y https://boe.es",
        ),
        ("[https://boe.es](https://boe.es)", "https://boe.es", "https://boe.es"),
        ("Hola<br>adiós <b>ya</b>", "Hola\nadiós ya", "Hola\nadiós ya"),
        ("uno\n\n---\n\ndos", "uno\n\ndos", "uno\n\ndos"),
        ("```\nx = 1\n```", "x = 1", "x = 1"),
        (
            "Si la renta < 22.000 € y 3 * 4 = 12",
            "Si la renta < 22.000 € y 3 * 4 = 12",
            "Si la renta < 22.000 € y 3 * 4 = 12",
        ),
    ],
)
def test_markdown_is_rendered_for_the_channel(markdown: str, simplex: str, texto: str) -> None:
    assert a_canal(markdown, "simplex") == simplex
    assert a_canal(markdown, "texto") == texto
    assert a_canal(markdown, "markdown") == markdown.strip()


def test_a_table_becomes_one_line_per_row() -> None:
    tabla = (
        "Comparativa:\n\n"
        "| Concepto | Ahora | Con la oferta |\n"
        "|:---|---:|---:|\n"
        "| Cuota mensual | **812,40 €** | 790,10 € |\n"
        "| Coste total | 97.488 € | |\n"
        "\nFin."
    )
    assert a_canal(tabla, "simplex") == (
        "Comparativa:\n\n"
        "• Concepto: Cuota mensual · Ahora: *812,40 €* · Con la oferta: 790,10 €\n"
        "• Concepto: Coste total · Ahora: 97.488 €\n"
        "\nFin."
    )


def test_long_answers_are_split_on_paragraphs_then_lines_sentences_and_words() -> None:
    parrafos = [f"Párrafo {i}: " + "texto " * 30 for i in range(6)]
    partes = partir("\n\n".join(parrafos), 400)
    assert all(len(p) <= 400 for p in partes)
    assert "\n\n".join(partes).split("\n\n") == [p.strip() for p in parrafos]  # nothing lost, paragraphs whole
    # One long paragraph: cut between sentences, never inside a word.
    frases = " ".join(f"Frase número {i} con algo de contenido." for i in range(40))
    partes = partir(frases, 200)
    assert all(len(p) <= 200 for p in partes) and all(p.endswith(".") for p in partes)
    assert " ".join(partes) == frases
    # A single endless word is cut hard (nothing else is possible).
    assert partir("x" * 450, 200) == ["x" * 200, "x" * 200, "x" * 50]
    assert partir("  ", 200) == [] and partir("corto", 200) == ["corto"]


def test_names_are_joined_in_spanish() -> None:
    assert enumerar(["Hipoteca"]) == "Hipoteca"
    assert enumerar(["Hipoteca", "Luz"]) == "Hipoteca y Luz"
    assert enumerar(["Luz", "Hipoteca"]) == "Luz e Hipoteca"
    assert enumerar(["Luz", "Hipoteca", "Impuestos"]) == "Luz, Hipoteca e Impuestos"
    assert enumerar(["Luz", "Hielo"]) == "Luz y Hielo"


def _informe() -> InformeCerebro:
    cuota = Cifra(
        id="C1",
        especialista="hipoteca",
        concepto="cuota mensual",
        valor=Decimal("812.40"),
        unidad="EUR/mes",
        fragment_id="F3",
        herramienta="hipoteca_calcular",
    )
    return InformeCerebro(
        intencion=Intencion(tipo="cruzada", confianza=0.9, origen="reglas"),
        ruta="sintesis",
        informes=[
            ResumenInforme(encargo_id="e1", especialista="hipoteca", nombre="Hipoteca", estado="ok", cifras=[cuota]),
            ResumenInforme(encargo_id="e2", especialista="fiscal", nombre="Impuestos", estado="ok"),
        ],
        puerta=ResultadoPuerta(
            atribuciones=[
                AtribucionCifra(
                    literal="812,40 €",
                    valor=Decimal("812.40"),
                    cifra_id="C1",
                    especialista="hipoteca",
                    fragment_id="F3",
                ),
                AtribucionCifra(literal="1.500 €", valor=Decimal("1500"), del_usuario=True),
            ]
        ),
    )


def test_who_was_consulted_and_where_each_figure_comes_from() -> None:
    informe = _informe()
    assert consultados(informe) == "Consulté a: Hipoteca e Impuestos."
    assert consultados(None) is None
    texto = texto_cifras(informe)
    assert "• 812,40 € — Hipoteca: cuota mensual (cálculo `hipoteca_calcular`) [F3]" in texto
    assert "• 1.500 € — lo dijiste tú" in texto
    assert texto_cifras(None) == "La última respuesta no tiene cifras calculadas que explicar."


def test_the_sources_are_listed_by_their_citation() -> None:
    citas = [
        AgentCitation(
            fragment_id="F2",
            title="Ley 35/2006 IRPF",
            locator="https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764",
            section="Artículo 38",
            vigencia="superada",
        )
    ]
    texto = texto_fuentes(citas)
    assert "• [F2] Ley 35/2006 IRPF, Artículo 38 — https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764" in texto
    assert "(_superada_)" in texto
    assert texto_fuentes([]) == "La última respuesta no cita fuentes."
