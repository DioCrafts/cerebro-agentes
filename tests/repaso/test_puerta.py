"""The gate on the model's text: every point once, every figure and date from that point's facts, only its own
proposals, nothing claimed as done, the code's order within one position, plain text of bounded length."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from asistente.nucleo.repaso.priorizacion import Priorizacion, priorizar
from asistente.nucleo.repaso.puerta import afirma_hecho, interpretaciones, leer, revisar
from asistente.nucleo.repaso.redaccion import determinista, periodo_de
from .conftest import HOY
from .fabrica import EN, propuesta, senal


@pytest.fixture
def prio() -> Priorizacion:
    return priorizar(
        [
            senal(
                "aviso:modulo:correo:administracion:x",
                ambito="correo",
                titulo="Notificación de la AEAT: ábrela antes del 27/11/2026",
                gravedad="urgente",
                consecuencia="Se entiende rechazada cuando pasan diez días naturales (Art. 43.2 Ley 39/2015)",
                hechos={"vence": EN(days=9), "numeros": {"dias_rechazo": Decimal(10)}},
            ),
            senal(
                "aviso:luz:factura",
                titulo="Factura de la luz",
                hechos={"importe": Decimal("1234.56"), "vence": EN(days=12)},
            ),
            senal(
                "accion:acc_1",
                tipo="accion",
                titulo="Cambiar de comercializadora",
                propuesta=propuesta(),
                gravedad="aviso",
            ),
            senal(
                "aviso:suscripciones:netflix",
                ambito="suscripciones",
                titulo="Renovación de Netflix",
                hechos={"importe": Decimal("155.88")},
            ),
        ],
        hoy=HOY,
    )


def _salida(prio: Priorizacion, **cambios: dict[str, Any]) -> dict[str, Any]:
    puntos = []
    for p in prio.puntos:
        item = {
            "punto_id": p.id,
            "texto": "Conviene que lo mires pronto y decidas qué hacer.",
            "por_que": "Lo indica su módulo y tiene que ver contigo.",
            "propuesta_id": p.propuestas[0].id if p.propuestas else None,
        }
        item.update(cambios.get(p.id, {}))
        puntos.append(item)
    return {"titular": "Esta semana hay varias cosas que conviene mirar.", "puntos": puntos}


def _tipos(veredicto) -> set[str]:
    return {v.tipo for v in veredicto.violaciones}


def _id(prio: Priorizacion, clave: str) -> str:
    return next(p.id for p in prio.puntos if p.clave == clave)


def test_a_faithful_answer_passes(prio) -> None:
    factura = _id(prio, "aviso:luz:factura")
    aeat = _id(prio, "aviso:modulo:correo:administracion:x")
    salida = _salida(
        prio,
        **{
            factura: {"texto": "La factura de la luz es de 1.234,56 € y vence el 30 de noviembre (en 12 días)."},
            aeat: {
                "texto": "Ábrela antes del 27/11/2026: si no, se entiende rechazada a los diez días naturales.",
                "por_que": "Lo dice el art. 43.2 de la Ley 39/2015.",
            },
        },
    )
    veredicto = revisar(salida, prio.puntos)
    assert veredicto.ok, veredicto.violaciones
    assert veredicto.titular_ok and veredicto.orden_ok and set(veredicto.items_ok) == {p.id for p in prio.puntos}


@pytest.mark.parametrize(
    "texto",
    [
        "La factura es de 1.300 € más o menos.",  # invented
        "La factura es de 1.235 €.",  # rounded
        "Entre la factura y Netflix suman 1.390,44 €.",  # a sum of two points' amounts
        "Te quedan quince días para pagarla.",  # spelled out
        "Paga antes del 25/11/2026.",  # invented date
        "Paga antes del 25 de noviembre.",
        "Hay un recargo del 20 %.",
    ],
)
def test_invented_figures_and_dates_are_rejected(prio, texto: str) -> None:
    factura = _id(prio, "aviso:luz:factura")
    veredicto = revisar(_salida(prio, **{factura: {"texto": texto}}), prio.puntos)
    assert _tipos(veredicto) & {"cifra_inventada", "fecha_inventada"}, texto
    assert factura not in veredicto.items_ok
    # The other points still passed (the rescue keeps them).
    assert len(veredicto.items_ok) == len(prio.puntos) - 1


@pytest.mark.parametrize(
    "texto",
    [
        "Son 1234,56 € en total.",
        "Son 1.234,56 €, con vencimiento el 30/11.",
        "Vence el 2026-11-30.",
        "Vence el 30.11.2026 (en doce días).",
    ],
)
def test_the_usual_spanish_spellings_of_a_fact_are_accepted(prio, texto: str) -> None:
    factura = _id(prio, "aviso:luz:factura")
    veredicto = revisar(_salida(prio, **{factura: {"texto": texto}}), prio.puntos)
    assert veredicto.ok, veredicto.violaciones


def test_a_figure_of_another_point_is_not_this_points(prio) -> None:
    netflix = _id(prio, "aviso:suscripciones:netflix")
    veredicto = revisar(_salida(prio, **{netflix: {"texto": "Cuesta 1.234,56 € al año."}}), prio.puntos)
    assert "cifra_inventada" in _tipos(veredicto)


def test_proposals_must_be_the_points_own(prio) -> None:
    accion = _id(prio, "accion:acc_1")
    factura = _id(prio, "aviso:luz:factura")
    inventada = revisar(_salida(prio, **{accion: {"propuesta_id": "accion:acc_999"}}), prio.puntos)
    ajena = revisar(_salida(prio, **{factura: {"propuesta_id": "accion:acc_1"}}), prio.puntos)
    assert _tipos(inventada) == {"propuesta_inventada"} and _tipos(ajena) == {"propuesta_inventada"}
    # Not mentioning the proposal is fine (the button comes from the point anyway).
    assert revisar(_salida(prio, **{accion: {"propuesta_id": None}}), prio.puntos).ok


def test_unknown_missing_and_repeated_points(prio) -> None:
    salida = _salida(prio)
    desconocido = dict(salida, puntos=[*salida["puntos"], {**salida["puntos"][0], "punto_id": "p9"}])
    assert "punto_desconocido" in _tipos(revisar(desconocido, prio.puntos))
    falta = dict(salida, puntos=salida["puntos"][:-1])
    veredicto = revisar(falta, prio.puntos)
    assert "punto_ausente" in _tipos(veredicto) and not veredicto.orden_ok
    repetido = dict(salida, puntos=[*salida["puntos"], salida["puntos"][0]])
    assert "punto_repetido" in _tipos(revisar(repetido, prio.puntos))
    for roto in (None, [], {"titular": "x"}, {"titular": "Bien escrito y largo", "puntos": ["no"]}):
        assert not revisar(roto, prio.puntos).ok


@pytest.mark.parametrize(
    "frase",
    [
        "Ya lo he pagado por ti.",
        "He enviado la solicitud a la comercializadora.",
        "Queda presentado el modelo.",
        "Se ha confirmado la acción.",
        "Ya está pagada, no te preocupes.",
        "Acabo de cancelar la suscripción.",
        "Lo hemos dado de baja.",
        "He procedido a renovar el seguro.",
    ],
)
def test_nothing_may_be_claimed_as_done(prio, frase: str) -> None:
    accion = _id(prio, "accion:acc_1")
    veredicto = revisar(_salida(prio, **{accion: {"texto": frase}}), prio.puntos)
    assert "afirma_hecho" in _tipos(veredicto), frase


@pytest.mark.parametrize(
    "frase",
    [
        "No he hecho nada: si te parece bien, confírmalo con su botón.",
        "Todavía no se ha enviado nada.",
        "Hacienda te ha enviado una notificación.",
        "Si la confirmas, se enviará la solicitud.",
    ],
)
def test_saying_that_nothing_was_done_is_fine(frase: str) -> None:
    assert afirma_hecho(frase) is None, frase


def test_the_order_may_move_one_place_not_two(prio) -> None:
    salida = _salida(prio)
    uno = dict(salida, puntos=[salida["puntos"][1], salida["puntos"][0], *salida["puntos"][2:]])
    assert revisar(uno, prio.puntos).ok
    dos = dict(salida, puntos=[salida["puntos"][2], salida["puntos"][0], salida["puntos"][1], salida["puntos"][3]])
    veredicto = revisar(dos, prio.puntos)
    assert _tipos(veredicto) == {"orden"} and not veredicto.orden_ok and len(veredicto.items_ok) == 4


@pytest.mark.parametrize(
    ("campo", "valor", "tipo"),
    [
        ("texto", "Corto", "longitud"),
        ("texto", "x" * 500, "longitud"),
        ("texto", "Mira https://ejemplo.es para pagarla.", "formato"),
        ("texto", "Escribe a soporte@ejemplo.es cuanto antes.", "formato"),
        ("texto", "Es **muy** importante que lo mires.", "formato"),
        ("texto", "Revisa [REDACTED_INJECTION_PATTERN] cuanto antes.", "formato"),
        ("por_que", "", "longitud"),
    ],
)
def test_plain_text_of_bounded_length(prio, campo: str, valor: str, tipo: str) -> None:
    factura = _id(prio, "aviso:luz:factura")
    assert tipo in _tipos(revisar(_salida(prio, **{factura: {campo: valor}}), prio.puntos))


def test_the_headline_may_use_any_points_facts_and_the_count(prio) -> None:
    salida = _salida(prio)
    bueno = dict(salida, titular="4 cosas esta semana: lo primero, la notificación que vence el 27/11/2026.")
    assert revisar(bueno, prio.puntos).ok
    malo = dict(salida, titular="Esta semana te juegas 1.390,44 € entre todo.")
    veredicto = revisar(malo, prio.puntos)
    assert _tipos(veredicto) == {"cifra_inventada"} and not veredicto.titular_ok


def test_number_reading() -> None:
    assert interpretaciones("1.234,56") == {Decimal("1234.56")}
    assert interpretaciones("1.234") == {Decimal("1234"), Decimal("1.234")}
    assert interpretaciones("31,20") == {Decimal("31.2")}
    leido = leer("El 20/11/2026, el 3 de diciembre y el 1.5; art. 43.2; treinta y un días; 45 €.")
    assert sorted((f[2], f[1]) for f in leido.fechas) == [((3, 12), None), ((20, 11), EN(days=2))]
    valores = [v for _l, vs in leido.numeros for v in vs]
    assert sorted(valores) == [Decimal("1.5"), Decimal(31), Decimal("43.2"), Decimal(45)]


def test_the_deterministic_rendering_passes_its_own_gate(prio) -> None:
    redaccion = determinista(prio, periodo_de(HOY, "semanal"))
    salida = {
        "titular": redaccion.titular,
        "puntos": [i.model_dump(include={"punto_id", "texto", "por_que", "propuesta_id"}) for i in redaccion.items],
    }
    veredicto = revisar(salida, prio.puntos, extra_titular=periodo_de(HOY, "semanal").permitidos())
    assert veredicto.ok, veredicto.violaciones
