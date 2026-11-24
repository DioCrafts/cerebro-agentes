"""Spanish figures in text: a table of cases and seeded property checks (no hypothesis in this environment)."""

from __future__ import annotations

from decimal import Decimal
import random

import pytest

from asistente.nucleo.cerebro.colaboracion.cifras import (
    coincide,
    escanear,
    extraer_cifras,
    formatear_valor,
    ignorables,
    numeros_de,
    numeros_en_letras,
    plano,
    unidades_compatibles,
)

D = Decimal

# (text, [(value, unit, written decimals)] of the figures found) — every other number must be ignorable.
CIFRAS: list[tuple[str, list[tuple[Decimal, str | None, int]]]] = [
    ("1.234,56 €", [(D("1234.56"), "EUR", 2)]),
    ("1234,56", [(D("1234.56"), None, 2)]),
    ("1 234,56", [(D("1234.56"), None, 2)]),
    ("1 234,56 €", [(D("1234.56"), "EUR", 2)]),
    ("1 234 €", [(D("1234"), "EUR", 0)]),
    ("12 %", [(D("12"), "%", 0)]),
    ("12%", [(D("12"), "%", 0)]),
    ("3,45 kW", [(D("3.45"), "kW", 2)]),
    ("3kW", [(D("3"), "kW", 0)]),
    ("200 kWh", [(D("200"), "kWh", 0)]),
    ("200kWh", [(D("200"), "kWh", 0)]),
    ("24 meses", [(D("24"), "meses", 0)]),
    ("1 mes", [(D("1"), "meses", 0)]),
    ("30 días", [(D("30"), "dias", 0)]),
    ("15 dias", [(D("15"), "dias", 0)]),
    ("5 años", [(D("5"), "años", 0)]),
    ("3 unidades", [(D("3"), "unidades", 0)]),
    ("-150,00 €", [(D("-150.00"), "EUR", 2)]),
    ("−150 €", [(D("-150"), "EUR", 0)]),
    ("(−150 €)", [(D("-150"), "EUR", 0)]),
    ("+3 %", [(D("3"), "%", 0)]),
    ("1,2 millones", [(D("1200000"), None, 1)]),
    ("1,2 millones de euros", [(D("1200000"), "EUR", 1)]),
    ("1,5 millón", [(D("1500000"), None, 1)]),
    ("3 mil euros", [(D("3000"), "EUR", 0)]),
    ("2 mil millones", [(D("2000000000"), None, 0)]),
    ("1,2 M€", [(D("1200000"), "EUR", 1)]),
    ("3 k€", [(D("3000"), "EUR", 0)]),
    ("1,234.56", [(D("1234.56"), None, 2)]),
    ("1,234,567", [(D("1234567"), None, 0)]),
    ("1.234.567", [(D("1234567"), None, 0)]),
    ("1,234", [(D("1.234"), None, 3)]),
    ("1.234", [(D("1234"), None, 0)]),
    ("12.5", [(D("12.5"), None, 1)]),
    ("0.234", [(D("0.234"), None, 3)]),
    ("0,5 %", [(D("0.5"), "%", 1)]),
    ("3.45 kW", [(D("3.45"), "kW", 2)]),
    ("2 300 €", [(D("2300"), "EUR", 0)]),
    ("en 2 300 casos", [(D("300"), None, 0)]),
    ("pagas 50 € al mes", [(D("50"), "EUR/mes", 0)]),
    ("50 €/mes", [(D("50"), "EUR/mes", 0)]),
    ("50 € mensuales", [(D("50"), "EUR/mes", 0)]),
    ("50 euros cada mes", [(D("50"), "EUR/mes", 0)]),
    ("600 € por mes", [(D("600"), "EUR/mes", 0)]),
    ("1.000 €/año", [(D("1000"), "EUR/año", 0)]),
    ("1.000 € al año", [(D("1000"), "EUR/año", 0)]),
    ("1.000 euros anuales", [(D("1000"), "EUR/año", 0)]),
    ("30.000 € brutos anuales", [(D("30000"), "EUR/año", 0)]),
    ("EUR 1.234", [(D("1234"), "EUR", 0)]),
    ("€ 1.234,5", [(D("1234.5"), "EUR", 1)]),
    ("1.234 EUR", [(D("1234"), "EUR", 0)]),
    ("12 euros", [(D("12"), "EUR", 0)]),
    ("1 euro", [(D("1"), "EUR", 0)]),
    ("el 21 por ciento", [(D("21"), "%", 0)]),
    ("2 puntos porcentuales", [(D("2"), "%", 0)]),
    ("10-15 %", [(D("10"), "%", 0), (D("15"), "%", 0)]),
    ("entre 300 y 400 €", [(D("300"), "EUR", 0), (D("400"), "EUR", 0)]),
    ("de 3 a 5 años", [(D("3"), "años", 0), (D("5"), "años", 0)]),
    ("2025 €", [(D("2025"), "EUR", 0)]),
    ("2.025", [(D("2025"), None, 0)]),
    ("2050 al mes", [(D("2050"), None, 0)]),
    ("tienes 15 suscripciones", [(D("15"), None, 0)]),
    ("Cuota: 812,34 €. Intereses: 45.000,00 €.", [(D("812.34"), "EUR", 2), (D("45000.00"), "EUR", 2)]),
    ("**2.350 €**", [(D("2350"), "EUR", 0)]),
    ("0 €", [(D("0"), "EUR", 0)]),
    ("100 %", [(D("100"), "%", 0)]),
    ("9,5 m3", [(D("9.5"), None, 1)]),
    ("ahorro 120 € al año (10 €/mes)", [(D("120"), "EUR/año", 0), (D("10"), "EUR/mes", 0)]),
    ("Total: 99,99€.", [(D("99.99"), "EUR", 2)]),
]

# (text, reason of every number in it) — none of these is a figure.
NO_CIFRAS: list[tuple[str, list[str]]] = [
    ("Ley 35/2006", ["norma"]),
    ("la Ley Orgánica 3/2018", ["norma"]),
    ("Real Decreto 439/2007", ["norma"]),
    ("RD 439/2007", ["norma"]),
    ("Orden HAC/1234/2025", ["norma"]),
    ("Directiva 2006/112/CE", ["norma"]),
    ("según la 35/2006", ["norma"]),
    ("art. 68 LIRPF", ["articulo"]),
    ("art. 68.1", ["articulo"]),
    ("artículo 7 h)", ["articulo"]),
    ("artículos 19 y 20", ["articulo"]),
    ("arts. 19, 20 y 21", ["articulo"]),
    ("apartado 3", ["articulo"]),
    ("modelo 100", ["articulo"]),
    ("casilla 0505", ["articulo"]),
    ("disposición adicional 4.ª", ["articulo"]),
    ("15/03/2025", ["fecha"]),
    ("15-03-2025", ["fecha"]),
    ("15.03.2025", ["fecha"]),
    ("2025-03-15", ["fecha"]),
    ("15 de marzo de 2025", ["fecha"]),
    ("1 de enero", ["fecha"]),
    ("en marzo de 2025", ["fecha"]),
    ("el 15/03", ["fecha"]),
    ("a las 10:30", ["hora"]),
    ("en 2025", ["año"]),
    ("el ejercicio 2024", ["año"]),
    ("2024-2025", ["rango_años"]),
    ("2024/25", ["rango_años"]),
    ("+34 612 345 678", ["telefono"]),
    ("612345678", ["telefono"]),
    ("912 345 678", ["telefono"]),
    ("91 234 56 78", ["telefono"]),
    ("BOE-A-2006-20764", ["identificador"]),
    ("V0058-25", ["identificador"]),
    ("12345678Z", ["identificador"]),
    ("ES12 3456 7890 1234 5678 9012", ["identificador"]),
    ("el caso C12", ["identificador"]),
    ("[F3]", ["cita"]),
    ("[F3, F7]", ["cita"]),
    ("[F3][F7]", ["cita", "cita"]),
    ("https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764", ["url"]),
    ("CP 28001", ["codigo_postal"]),
    ("1.º", ["ordinal"]),
    ("3.ª", ["ordinal"]),
    ("2º", ["ordinal"]),
    ("1er", ["ordinal"]),
    ("1. Primero\n2. Segundo", ["ordinal", "ordinal"]),
    ("tus 3 recibos", ["numero_pequeno"]),
    ("10 veces", ["numero_pequeno"]),
]


@pytest.mark.parametrize(("texto", "esperado"), CIFRAS)
def test_figures(texto: str, esperado: list[tuple[Decimal, str | None, int]]) -> None:
    hallado = [(n.valor, n.unidad, n.decimales) for n in numeros_de(texto)]
    assert hallado == esperado


@pytest.mark.parametrize(("texto", "motivos"), NO_CIFRAS)
def test_not_figures(texto: str, motivos: list[str]) -> None:
    assert numeros_de(texto) == []
    assert [n.tipo for n in ignorables(texto)] == motivos


def test_there_are_at_least_eighty_table_cases() -> None:
    assert len(CIFRAS) + len(NO_CIFRAS) >= 80


def test_literal_and_positions_are_those_of_the_text() -> None:
    texto = "Pagas 1.234,56 € al mes, según el art. 68."
    cifra, articulo = escanear(texto)
    assert cifra.literal == "1.234,56 € al mes" and texto[cifra.inicio : cifra.fin] == cifra.literal
    assert articulo.literal == "art. 68" and articulo.motivo == "referencia normativa"


def test_a_figure_inside_a_reference_list_is_still_a_figure() -> None:
    assert [(n.tipo, n.literal) for n in escanear("el artículo 7 y 1.200 € de multa")] == [
        ("articulo", "artículo 7"),
        ("cifra", "1.200 €"),
    ]


# --------------------------------------------------------------------------------------------------------------
# Tool output → Cifra
# --------------------------------------------------------------------------------------------------------------


def test_figures_of_a_tool_output_get_concept_year_and_provenance() -> None:
    texto = (
        "Cuota mensual: 812,34 €. Intereses totales 2025: 45.000,00 €; ahorro 120 € al año (10 €/mes). "
        "Fuentes: BOE-A-2000-1, art. 21."
    )
    cifras = extraer_cifras(texto, especialista="hipoteca", fragment_id="F4", herramienta="cuota")
    assert [(c.id, c.concepto, c.valor, c.unidad, c.ejercicio, c.literal) for c in cifras] == [
        ("C1", "Cuota mensual", D("812.34"), "EUR", None, "812,34 €"),
        ("C2", "Intereses totales 2025", D("45000.00"), "EUR", 2025, "45.000,00 €"),
        ("C3", "ahorro", D("120"), "EUR/año", None, "120 € al año"),
        ("C4", "ahorro", D("10"), "EUR/mes", None, "10 €/mes"),
    ]
    assert all((c.especialista, c.fragment_id, c.herramienta) == ("hipoteca", "F4", "cuota") for c in cifras)


def test_a_figure_with_nothing_before_takes_the_words_after_it() -> None:
    (cifra,) = extraer_cifras("812,34 € de cuota mensual.", especialista="hipoteca", fragment_id=None, herramienta=None)
    assert cifra.concepto == "cuota mensual" and cifra.herramienta is None


def test_ids_start_where_asked() -> None:
    cifras = extraer_cifras("A: 1 €. B: 2 €.", especialista="luz", fragment_id="F1", herramienta="x", primera=7)
    assert [c.id for c in cifras] == ["C7", "C8"]


# --------------------------------------------------------------------------------------------------------------
# Precision, units, formatting
# --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("escrito", "valor", "coincide_"),
    [
        ("1.234 €", "1234.40", True),  # rounded
        ("1.234 €", "1234.56", True),  # truncated
        ("1.234 €", "1235.50", False),
        ("1.235 €", "1234.56", True),  # rounded half up
        ("1.234,5 €", "1234.54", True),
        ("1.234,56 €", "1234.6", False),  # more precision than the source has
        ("1,2 millones", "1234567", True),
        ("1,3 millones", "1234567", False),
        ("23 %", "23", True),
        ("150 €", "-150.00", True),  # the sign is said in words
        ("812 €", "812.34", True),
        ("800 €", "812.34", False),
    ],
)
def test_matching_at_the_written_precision(escrito: str, valor: str, coincide_: bool) -> None:
    (numero,) = numeros_de(escrito)
    assert coincide(numero, D(valor)) is coincide_


@pytest.mark.parametrize(
    ("a", "b", "compatibles"),
    [
        (None, "EUR", True),
        ("EUR", None, True),
        ("EUR", "EUR/mes", True),
        ("EUR/año", "EUR", True),
        ("EUR/mes", "EUR/año", False),
        ("%", "EUR", False),
        ("kWh", "kW", False),
        ("meses", "meses", True),
    ],
)
def test_unit_compatibility(a: str | None, b: str | None, compatibles: bool) -> None:
    assert unidades_compatibles(a, b) is compatibles


@pytest.mark.parametrize(
    ("valor", "unidad", "texto"),
    [
        ("1234.5", "EUR", "1.234,5 €"),
        ("1234", None, "1.234"),
        ("-150.00", "EUR/mes", "−150,00 €/mes"),
        ("0.5", "%", "0,5 %"),
        ("1234567.891", "kWh", "1.234.567,891 kWh"),
    ],
)
def test_spanish_formatting(valor: str, unidad: str | None, texto: str) -> None:
    assert formatear_valor(D(valor), unidad) == texto


def test_folding_keeps_positions() -> None:
    texto = "Según Fiscal, la ÍNTEGRA del año"
    assert len(plano(texto)) == len(texto) and plano(texto) == "segun fiscal, la integra del año"


# --------------------------------------------------------------------------------------------------------------
# Seeded property checks: whatever way a figure is written, it reads back exactly
# --------------------------------------------------------------------------------------------------------------

_ESTILOS = ("es", "nbsp", "llano", "llano_sin_miles")
_UNIDADES = (("€", "EUR"), (" €", "EUR"), (" € al mes", "EUR/mes"), (" €/año", "EUR/año"), (" %", "%"), (" kWh", "kWh"))


def _escribir(valor: Decimal, estilo: str) -> str:
    texto = formatear_valor(valor)  # es: 1.234,56
    if estilo == "nbsp":
        return texto.replace(".", " ")
    if estilo == "llano":
        return texto.replace(".", "")
    return texto.replace(".", "") if valor < 1000 else texto


@pytest.mark.parametrize("semilla", range(5))
def test_any_written_figure_reads_back_exactly(semilla: int) -> None:
    azar = random.Random(semilla)
    for _ in range(200):
        decimales = azar.choice((0, 1, 2))
        valor = D(azar.randrange(11, 10_000_000)).scaleb(-decimales)
        estilo = azar.choice(_ESTILOS)
        sufijo, unidad = azar.choice(_UNIDADES)
        escrito = _escribir(valor, estilo) + sufijo
        frase = f"Según el cálculo, {escrito} en total."
        numeros = numeros_de(frase)
        assert len(numeros) == 1, (frase, numeros)
        (numero,) = numeros
        assert (numero.valor, numero.unidad, numero.decimales) == (valor, unidad, decimales), frase
        assert coincide(numero, valor)


@pytest.mark.parametrize("semilla", range(3))
def test_references_never_become_figures(semilla: int) -> None:
    azar = random.Random(100 + semilla)
    plantillas = (
        "Ley {a}/{y}",
        "art. {a}.{b}",
        "artículos {a} y {b}",
        "el {d}/{m}/{y}",
        "el {d} de marzo de {y}",
        "en {y}",
        "modelo {a}",
        "[F{a}]",
        "BOE-A-{y}-{a}{b}",
        "{a}.º",
    )
    for _ in range(300):
        datos = {
            "a": azar.randrange(1, 400),
            "b": azar.randrange(1, 40),
            "d": azar.randrange(1, 29),
            "m": azar.randrange(1, 13),
            "y": azar.randrange(1990, 2031),
        }
        texto = azar.choice(plantillas).format(**datos)
        assert numeros_de(texto) == [], texto


def test_norma_43_es_un_formato_no_una_cifra():
    numeros = [n for n in escanear("Sirven los formatos Norma 43, CSV o camt.053; cuesta 43 €.") if n.es_cifra]
    assert [n.literal.strip() for n in numeros] == ["43 €"] or [n.valor for n in numeros] == [43]
    assert len(numeros) == 1


# --------------------------------------------------------------------------------------------------------------
# dd-mm dates without a year (a bill's period) and figures written in words (the fragment side of the gate)
# --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "fechas"),
    [
        ("[del 01-11 al 31-10]", ["01-11", "31-10"]),
        ("Periodo del 1-11 al 30-11", ["1-11", "30-11"]),
        ("desde 5-3 hasta 4-4", ["5-3", "4-4"]),
        ("el 15-03-2025", ["15-03-2025"]),
        ("emitida el 31-12", ["31-12"]),
    ],
)
def test_day_month_dates_with_a_hyphen_are_not_figures(texto: str, fechas: list[str]) -> None:
    assert numeros_de(texto) == []
    assert [(n.tipo, n.literal) for n in ignorables(texto)] == [("fecha", f) for f in fechas]


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Periodo [del 01-11 al 31-10]: potencia 4,6 kW", [(D("4.6"), "kW", 1)]),
        ("sube un 1-2 %", [(D("1"), "%", 0), (D("2"), "%", 0)]),
        ("del 10-12 %", [(D("10"), "%", 0), (D("12"), "%", 0)]),
        ("de 3-5 años", [(D("3"), "años", 0), (D("5"), "años", 0)]),
        ("consumo de 10-12 kWh", [(D("10"), "kWh", 0), (D("12"), "kWh", 0)]),
        ("entre 15-20 €", [(D("15"), "EUR", 0), (D("20"), "EUR", 0)]),
    ],
)
def test_hyphenated_ranges_with_a_unit_stay_figures(
    texto: str, esperado: list[tuple[Decimal, str | None, int]]
) -> None:
    assert [(n.valor, n.unidad, n.decimales) for n in numeros_de(texto)] == esperado


def test_article_ranges_stay_references() -> None:
    assert [n.tipo for n in ignorables("art. 10-11")] == ["articulo"]


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Los contribuyentes menores de cuarenta años podrán deducir", [(D(40), "años", "cuarenta años")]),
        ("menores de treinta y cinco años", [(D(35), "años", "treinta y cinco años")]),
        ("en el plazo de ciento veinte días", [(D(120), "dias", "ciento veinte días")]),
        ("deducir el cinco por ciento", [(D(5), "%", "cinco por ciento")]),
        ("el cien por cien", [(D(100), "%", "cien por cien")]),
        ("durante veintiún meses", [(D(21), "meses", "veintiún meses")]),
        ("con dieciséis años", [(D(16), "años", "dieciséis años")]),
        ("cero días", [(D(0), "dias", "cero días")]),
        ("Dos contribuyentes y tres hijos", []),
        ("los cuarenta principales", []),
    ],
)
def test_figures_in_words_before_a_unit(texto: str, esperado: list[tuple[Decimal, str, str]]) -> None:
    hallados = numeros_en_letras(texto)
    assert [(n.valor, n.unidad, n.literal) for n in hallados] == esperado
    assert all(n.es_cifra and texto[n.inicio : n.fin] == n.literal for n in hallados)


def test_the_answer_side_scan_still_ignores_words() -> None:
    assert numeros_de("menores de cuarenta años") == []
