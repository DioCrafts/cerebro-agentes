"""The figures gate: origin of every figure, sums nobody computed, attribution, citations, the revision message."""

from __future__ import annotations

from decimal import Decimal
import random
import time

import pytest

from asistente.nucleo.agent.evidence import EvidenceLedger
from asistente.nucleo.cerebro.colaboracion import buscar_combinacion, mensaje_de_revision, revisar_cifras
from asistente.nucleo.cerebro.colaboracion.cifras import formatear_valor, numeros_de
from asistente.nucleo.cerebro.contrato import Cifra, InformeEspecialista
from .fakes import cifra

D = Decimal


def _ledger() -> EvidenceLedger:
    ledger = EvidenceLedger()
    for texto, fuente in (
        ("Ahorro anual por amortizar: 1.500,00 €. Cuota mensual: 812,34 €.", "modulo://hipoteca/a/1"),  # F1
        ("Ahorro fiscal estimado: 850,00 €. Tipo marginal: 37 %.", "modulo://fiscal/irpf/1"),  # F2
        ("El tipo general del impuesto será el 21 por ciento. El plazo es de 4 años.", "https://boe.es/a"),  # F3
        ("Coste de la luz: 50,00 €/mes. Potencia: 3,45 kW.", "modulo://luz/f/1"),  # F4
        ("Ahorro total combinado: 2.350,00 €.", "modulo://cerebro/planificador/1"),  # F5
    ):
        ledger.register(text=texto, metadata={"source": fuente, "title": fuente})
    return ledger


def _informes() -> list[InformeEspecialista]:
    return [
        InformeEspecialista(
            encargo_id="e1",
            especialista="hipoteca",
            nombre="Hipoteca",
            estado="ok",
            texto="…",
            cifras=[
                cifra("C1", "hipoteca", "ahorro anual por amortizar", "1500.00", "EUR/año", "F1", "1.500,00 €"),
                cifra("C2", "hipoteca", "cuota mensual", "812.34", "EUR/mes", "F1", "812,34 €"),
            ],
            fragment_ids=["F1"],
        ),
        InformeEspecialista(
            encargo_id="e2",
            especialista="fiscal",
            nombre="Fiscal",
            estado="ok",
            texto="…",
            cifras=[
                cifra("C3", "fiscal", "ahorro fiscal estimado", "850.00", "EUR", "F2", "850,00 €"),
                cifra("C4", "fiscal", "tipo marginal", "37", "%", "F2", "37 %"),
            ],
            fragment_ids=["F2", "F3"],
        ),
        InformeEspecialista(
            encargo_id="e3",
            especialista="luz",
            nombre="Luz",
            estado="ok",
            texto="…",
            cifras=[
                cifra("C5", "luz", "coste de la luz", "50.00", "EUR/mes", "F4", "50,00 €/mes"),
                cifra("C6", "luz", "potencia", "3.45", "kW", "F4", "3,45 kW"),
            ],
            fragment_ids=["F4"],
        ),
    ]


def _con_planificador() -> list[InformeEspecialista]:
    """The brain's own tool combined the two savings: its report carries that total."""
    return [
        *_informes(),
        InformeEspecialista(
            encargo_id="e9",
            especialista="cerebro",
            nombre="Asistente",
            estado="ok",
            cifras=[cifra("C9", "cerebro", "ahorro total combinado", "2350.00", "EUR", "F5", "2.350,00 €")],
            fragment_ids=["F5"],
        ),
    ]


USUARIO = ["Cobro 30.000 € al año y tengo 2 hijos. Pago unos 1.100 € de alquiler."]

# (answer, the violation types expected, in order)
CASOS: list[tuple[str, list[str]]] = [
    # Attributed figures.
    ("Ahorras 1.500 € al año amortizando [F1].", []),
    ("Tu cuota es de 812,34 € al mes [F1].", []),
    ("Tu cuota es de 812 € al mes [F1].", []),
    ("Tu cuota ronda los 812,3 €.", []),
    ("Según Fiscal, tu tipo marginal es 37 % [F2].", []),
    ("Tu tipo marginal es 37 % (Fiscal).", []),
    ("La luz te cuesta 50 € al mes y tienes 3,45 kW contratados [F4].", []),
    ("**Hipoteca**: 1.500 € al año [F1]. **Fiscal**: 850 € [F2].", []),
    ("- Hipoteca: 812,34 €/mes\n- Fiscal: 37 %", []),
    # The user's own words.
    ("Con tus 30.000 € al año de sueldo y 1.100 € de alquiler…", []),
    ("Con un sueldo de 30.000 € brutos anuales.", []),
    # Literal of a cited source.
    ("El IVA general es del 21 % [F3].", []),
    ("El plazo es de 4 años [F3].", []),
    # Not figures.
    ("Según el art. 68 de la Ley 35/2006, en 2025 y antes del 30/06/2026 puedes hacerlo.", []),
    ("Tienes 2 hijos y 3 recibos domiciliados.", []),
    ("Llama al 912 345 678 o mira https://www.boe.es/x?id=BOE-A-2006-20764.", []),
    ("1. Revisa la cuota.\n2. Pide el certificado.", []),
    ("Presenta el modelo 100 (casilla 0505) antes del 1 de julio.", []),
    # A tool that combined the savings: passes.
    ("En total ahorras 2.350 € [F5].", []),
    # Invented figures.
    ("Ahorrarías unos 3.000 € al año.", ["cifra_sin_origen"]),
    ("Tu cuota es de 777 € al mes.", ["cifra_sin_origen"]),
    ("La diferencia es de 800 € al mes.", ["suma_no_combinada"]),  # 850 (Fiscal) − 50 (Luz): nobody computed it
    ("Tu cuota es de 812,35 €.", ["cifra_sin_origen"]),
    ("El tipo es del 38 %.", ["cifra_sin_origen"]),
    ("Pagarías 15 € más.", ["cifra_sin_origen"]),
    # Units: a monthly figure is not a yearly one.
    ("Tu cuota es de 812,34 € al año.", ["cifra_sin_origen"]),
    ("La potencia es de 50 kW.", ["cifra_sin_origen"]),
    # Sums and differences of different specialists that no tool made.
    ("En total ahorras 2.350 € al año.", ["suma_no_combinada"]),
    ("Entre los dos ahorros, 2.350 €.", ["suma_no_combinada"]),
    ("El ahorro de la hipoteca supera al fiscal en 650 €.", ["suma_no_combinada"]),
    ("Entre cuota y luz pagas 862,34 € al mes.", ["suma_no_combinada"]),
    ("Entre cuota y luz pagas 10.348 € al año.", ["suma_no_combinada"]),  # (812,34 + 50) × 12 = 10.348,08
    ("Sumando las tres cosas, 2.400 €.", ["suma_no_combinada"]),  # 1.500 + 850 + 50
    # The model's own arithmetic on ONE specialist's figures: no origin (with the hint).
    ("Al año pagas 9.748 € de cuota.", ["cifra_sin_origen"]),  # 812,34 × 12
    ("Ahorras 125 € al mes amortizando.", ["cifra_sin_origen"]),  # 1.500 ÷ 12
    ("Entre ahorro y cuota, 2.312 €.", ["cifra_sin_origen"]),  # 1.500 + 812,34, both hipoteca
    # Misattributed.
    ("Según Hipoteca, tu tipo marginal es 37 % [F2].", ["cifra_mal_atribuida"]),
    ("Hipoteca calcula un ahorro de 850 € [F2].", ["cifra_mal_atribuida"]),
    ("El ahorro fiscal es de 850 € (Luz).", ["cifra_mal_atribuida"]),
    ("**Luz**: 1.500 € al año.", ["cifra_mal_atribuida"]),
    # Citations.
    ("Ahorras 1.500 € al año [F9].", ["cita_inexistente"]),
    ("Mira [F2] y [F12].", ["cita_inexistente"]),
    # Several at once.
    ("Según Hipoteca, 37 % [F7]; en total 2.350 €.", ["cita_inexistente", "cifra_mal_atribuida", "suma_no_combinada"]),
]


@pytest.mark.parametrize(("respuesta", "tipos"), CASOS)
def test_the_gate(respuesta: str, tipos: list[str]) -> None:
    resultado = revisar_cifras(respuesta, _informes(), ledger=_ledger(), textos_usuario=USUARIO)
    assert [v.tipo for v in resultado.violaciones] == tipos, resultado.violaciones
    assert resultado.ok is (not tipos)


def test_there_are_at_least_forty_cases() -> None:
    assert len(CASOS) >= 40


def test_the_tool_that_combined_them_makes_the_total_pass() -> None:
    respuesta = "Hipoteca: 1.500 € al año [F1]; Fiscal: 850 € [F2]. En total, 2.350 € [F5]."
    resultado = revisar_cifras(respuesta, _con_planificador(), ledger=_ledger(), textos_usuario=[])
    assert resultado.ok
    assert [(a.literal, a.cifra_id, a.especialista) for a in resultado.atribuciones] == [
        ("1.500 € al año", "C1", "hipoteca"),
        ("850 €", "C3", "fiscal"),
        ("2.350 €", "C9", "cerebro"),
    ]


def test_the_adversarial_total_is_caught_with_its_operands() -> None:
    respuesta = (
        "Según Hipoteca, amortizar te ahorra 1.500 € al año [F1], y Fiscal calcula 850 € de ahorro en la renta [F2]. "
        "Así que ahorras en total 2.350 €."
    )
    resultado = revisar_cifras(respuesta, _informes(), ledger=_ledger(), textos_usuario=USUARIO)
    (violacion,) = resultado.violaciones
    assert violacion.tipo == "suma_no_combinada" and violacion.literal == "2.350 €"
    assert violacion.cifras == ["C1", "C3"]
    assert "C1 (Hipoteca, 1.500,00 €) y C3 (Fiscal, 850,00 €)" in violacion.detalle
    mensaje = mensaje_de_revision(resultado)
    assert "No sumes" in mensaje and "2.350 €" in mensaje and mensaje.startswith("La comprobación de cifras")


def test_attributions_say_where_each_figure_came_from() -> None:
    respuesta = "Pagas 812 € al mes [F1]; cobras 30.000 € al año; el IVA es del 21 % [F3]."
    resultado = revisar_cifras(respuesta, _informes(), ledger=_ledger(), textos_usuario=USUARIO)
    assert resultado.ok
    assert [(a.literal, a.cifra_id, a.especialista, a.fragment_id, a.del_usuario) for a in resultado.atribuciones] == [
        ("812 € al mes", "C2", "hipoteca", "F1", False),
        ("30.000 € al año", None, None, None, True),
        ("21 %", None, "fiscal", "F3", False),
    ]
    assert resultado.ignorados == ["[F1] (cita)", "[F3] (cita)"]


def test_ignored_numbers_are_listed_with_their_reason() -> None:
    resultado = revisar_cifras(
        "Según el art. 68 de la Ley 35/2006, en 2025.", _informes(), ledger=_ledger(), textos_usuario=[]
    )
    assert resultado.ignorados == ["art. 68 (referencia normativa)", "Ley 35/2006 (número de norma)", "2025 (año)"]


def test_the_same_value_from_two_specialists_takes_the_named_one() -> None:
    informes = _informes() + [
        InformeEspecialista(
            encargo_id="e4",
            especialista="seguros",
            nombre="Seguros",
            estado="ok",
            cifras=[cifra("C7", "seguros", "prima", "850", "EUR", "F6")],
        )
    ]
    resultado = revisar_cifras("Seguros: la prima es de 850 €.", informes, ledger=_ledger(), textos_usuario=[])
    assert resultado.ok and resultado.atribuciones[0].cifra_id == "C7"
    resultado = revisar_cifras("La prima es de 850 € [F2].", informes, ledger=_ledger(), textos_usuario=[])
    assert resultado.atribuciones[0].cifra_id == "C3"  # the cited fragment decides


def test_a_figure_received_through_a_request_keeps_its_specialist() -> None:
    informes = [
        InformeEspecialista(
            encargo_id="e1",
            especialista="luz",
            nombre="Luz",
            estado="ok",
            cifras=[cifra("C1", "fiscal", "tipo marginal", "37", "%", "F2")],  # received from fiscal
        )
    ]
    assert revisar_cifras("Fiscal calcula un 37 %.", informes, ledger=_ledger(), textos_usuario=[]).ok
    (violacion,) = revisar_cifras("Luz calcula un 37 %.", informes, ledger=_ledger(), textos_usuario=[]).violaciones
    assert violacion.tipo == "cifra_mal_atribuida" and "es de Fiscal" in violacion.detalle


def test_the_revision_message_covers_every_kind() -> None:
    respuesta = "Según Hipoteca, 37 % [F7]; en total 2.350 €; además 999 €."
    resultado = revisar_cifras(respuesta, _informes(), ledger=_ledger(), textos_usuario=[])
    mensaje = mensaje_de_revision(resultado)
    for titulo in (
        "Cifras combinadas sin herramienta",
        "Cifras sin origen",
        "Cifras atribuidas a quien no es",
        "Citas que no existen",
    ):
        assert titulo in mensaje
    assert mensaje.rstrip().endswith("no añadas cifras nuevas ni cambies las que están bien.")
    assert mensaje_de_revision(revisar_cifras("Hola.", _informes(), ledger=_ledger(), textos_usuario=[])) == ""


def test_hints_for_the_models_own_arithmetic_and_wrong_units() -> None:
    (por_doce,) = revisar_cifras("Al año, 9.748 €.", _informes(), ledger=_ledger(), textos_usuario=[]).violaciones
    assert "C2 (Hipoteca, 812,34 €) multiplicada por 12" in por_doce.detalle
    (unidad,) = revisar_cifras(
        "Tu cuota es de 812,34 € al año.", _informes(), ledger=_ledger(), textos_usuario=[]
    ).violaciones
    assert "pero con otra unidad" in unidad.detalle and unidad.cifras == ["C2"]


# --------------------------------------------------------------------------------------------------------------
# The sum detector, directly
# --------------------------------------------------------------------------------------------------------------


def _numero(texto: str):  # type: ignore[no-untyped-def]
    (numero,) = numeros_de(texto)
    return numero


def test_the_simplest_combination_wins_and_mixed_specialists_are_preferred() -> None:
    cifras = [cifra("C1", "aa", "x", "100"), cifra("C2", "aa", "y", "50"), cifra("C3", "bb", "z", "50")]
    combinacion = buscar_combinacion(_numero("150 €"), cifras)
    assert combinacion is not None and combinacion.cruzada and [c.id for _s, c in combinacion.operandos] == ["C1", "C3"]
    assert buscar_combinacion(_numero("37 €"), cifras) is None


def test_zero_figures_and_other_units_never_take_part() -> None:
    cifras = [cifra("C1", "aa", "x", "100"), cifra("C2", "bb", "cero", "0"), cifra("C3", "bb", "pct", "50", "%")]
    assert buscar_combinacion(_numero("100 €"), cifras) is None
    assert buscar_combinacion(_numero("150 €"), cifras) is None


@pytest.mark.parametrize("semilla", range(10))
def test_property_any_cross_specialist_sum_is_caught(semilla: int) -> None:
    azar = random.Random(semilla)
    for _ in range(20):
        cifras: list[Cifra] = []
        for n in range(azar.randrange(3, 9)):
            cifras.append(
                cifra(
                    f"C{n + 1}",
                    azar.choice(("aa", "bb", "cc")),
                    f"c{n}",
                    str(D(azar.randrange(100, 900_000)).scaleb(-2)),
                    "EUR",
                )
            )
        a, b = azar.sample(cifras, 2)
        if a.especialista == b.especialista:
            continue
        total = a.valor + b.valor
        if any(c.valor == total for c in cifras):
            continue  # a figure already shows it: not a sum
        informes = [
            InformeEspecialista(
                encargo_id="e1",
                especialista=e,
                nombre=e.upper(),
                estado="ok",
                cifras=[c for c in cifras if c.especialista == e],
            )
            for e in sorted({c.especialista for c in cifras})
        ]
        respuesta = f"En total, {formatear_valor(total, 'EUR')}."
        resultado = revisar_cifras(respuesta, informes, ledger=EvidenceLedger(), textos_usuario=[])
        tipos = [v.tipo for v in resultado.violaciones]
        assert tipos in (["suma_no_combinada"], ["cifra_sin_origen"]), respuesta
        if tipos == ["cifra_sin_origen"]:
            # Only when a same-specialist combination explains it at least as simply (then it is labelled that way).
            assert resultado.violaciones[0].cifras
        # And if a tool had computed that total, the same answer passes.
        con_total = [
            *informes,
            InformeEspecialista(
                encargo_id="e9",
                especialista="cerebro",
                nombre="Asistente",
                estado="ok",
                cifras=[cifra("C99", "cerebro", "total", str(total))],
            ),
        ]
        assert revisar_cifras(respuesta, con_total, ledger=EvidenceLedger(), textos_usuario=[]).ok


@pytest.mark.parametrize("semilla", range(5))
def test_property_quoting_figures_as_they_are_always_passes(semilla: int) -> None:
    azar = random.Random(1000 + semilla)
    for _ in range(20):
        cifras = [
            cifra(
                f"C{n + 1}",
                azar.choice(("aa", "bb")),
                f"c{n}",
                str(D(azar.randrange(11, 900_000)).scaleb(-azar.choice((0, 2)))),
                azar.choice(("EUR", "EUR/mes", "%", None)),
            )
            for n in range(6)
        ]
        informes = [InformeEspecialista(encargo_id="e1", especialista="aa", nombre="AA", estado="ok", cifras=cifras)]
        elegidas = azar.sample(cifras, 3)
        respuesta = " ".join(f"Dato: {formatear_valor(c.valor, c.unidad)}." for c in elegidas)
        resultado = revisar_cifras(respuesta, informes, ledger=EvidenceLedger(), textos_usuario=[])
        assert resultado.ok, (respuesta, resultado.violaciones)
        assert len(resultado.atribuciones) == len({(formatear_valor(c.valor, c.unidad)) for c in elegidas})


def test_the_search_is_bounded() -> None:
    """60 figures, 20 numbers nobody computed: the gate still answers fast (pairs O(n²), triples O(n² log n))."""
    azar = random.Random(7)
    cifras = [
        cifra(f"C{n + 1}", ("aa", "bb", "cc")[n % 3], f"c{n}", str(azar.randrange(1000, 100000)), "EUR")
        for n in range(60)
    ]
    informes = [InformeEspecialista(encargo_id="e1", especialista="aa", nombre="AA", estado="ok", cifras=cifras)]
    respuesta = " ".join(f"Dato: {formatear_valor(D(9_000_001 + i * 7919), 'EUR')}." for i in range(20))
    inicio = time.monotonic()
    resultado = revisar_cifras(respuesta, informes, ledger=EvidenceLedger(), textos_usuario=[])
    assert time.monotonic() - inicio < 5
    assert len(resultado.violaciones) == 20
