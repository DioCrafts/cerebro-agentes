"""The decision model's pure rules: scope, default expiry, reopening conditions, no copy of the user's data."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import ValidationError
import pytest

from hacienda_gpt.memoria.ambitos import ambito_de_accion, ambito_de_aviso, ambito_de_palanca, etiqueta
from hacienda_gpt.memoria.modelo import (
    Condicion,
    Decision,
    caducidad_por_defecto,
    cubre,
    evaluar,
    huella,
    normalizar_texto,
    sumar_meses,
)
from .conftest import HOY


def _decision(**kw) -> Decision:
    datos = {"case_id": "c1", "ambito": "fiscal:plan_pensiones", "tipo": "rechazo", "razon": "necesito liquidez"}
    datos["fuente"] = "chat"
    datos.update(kw)
    return Decision(**datos)


def test_a_decision_covers_its_scope_and_narrower_ones_only() -> None:
    assert cubre("fiscal:plan_pensiones", "fiscal:plan_pensiones")
    assert cubre("fiscal:plan_pensiones", "fiscal:plan_pensiones:conyuge")
    assert cubre("fiscal", "fiscal:donativo")
    assert not cubre("fiscal:plan_pensiones:conyuge", "fiscal:plan_pensiones")
    assert not cubre("fiscal:plan", "fiscal:plan_pensiones")  # a prefix of a word is not a scope
    assert not cubre("luz:cambio_tarifa", "fiscal:plan_pensiones")


def test_scope_must_be_module_and_lever() -> None:
    for malo in ("", "Fiscal:x", "fiscal::x", "fiscal x", "a:b:c:d:e:f"):
        with pytest.raises(ValidationError):
            _decision(ambito=malo)
    assert _decision(ambito=" luz:cambio_tarifa:oferta-x ").ambito == "luz:cambio_tarifa:oferta-x"


def test_default_expiry_per_kind() -> None:
    assert caducidad_por_defecto("rechazo", HOY) == date(2027, 11, 16)
    assert caducidad_por_defecto("restriccion", HOY) == date(2027, 11, 16)
    assert caducidad_por_defecto("aceptacion", HOY) == date(2027, 5, 16)
    assert caducidad_por_defecto("preferencia", HOY) is None
    assert sumar_meses(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert sumar_meses(date(2027, 12, 31), 2) == date(2028, 2, 29)


def test_in_force_until_it_expires() -> None:
    decision = _decision(caducidad=date(2027, 11, 16))
    assert evaluar(decision, hoy=HOY).estado == "vigente"
    vencida = evaluar(decision, hoy=date(2027, 11, 16))
    assert vencida.estado == "caducada" and vencida.por == "caducidad" and "16/11/2027" in (vencida.motivo or "")


def test_a_date_condition_reopens_it() -> None:
    decision = _decision(
        ambito="luz:cambio_tarifa",
        condiciones_de_reapertura=[{"tipo": "fecha", "fecha": date(2027, 3, 31), "descripcion": "fin de permanencia"}],
    )
    assert evaluar(decision, hoy=date(2027, 3, 30)).vigente
    abierta = evaluar(decision, hoy=date(2027, 3, 31))
    assert abierta.estado == "reabrible" and "fin de permanencia" in (abierta.motivo or "")


def test_a_fact_condition_keeps_only_a_fingerprint_and_reopens_when_it_changes() -> None:
    condicion = Condicion(tipo="hecho", hecho="estado_civil", huella_al_decidir=huella("soltero"))
    decision = _decision(ambito="fiscal:tributacion:conjunta", condiciones_de_reapertura=[condicion])
    # Never the value itself: the decision is not a second copy of the user's data.
    assert "soltero" not in decision.model_dump_json()
    assert evaluar(decision, hoy=HOY, hechos={"estado_civil": "soltero"}).vigente
    assert evaluar(decision, hoy=HOY, hechos={"estado_civil": "casado"}).estado == "reabrible"
    assert evaluar(decision, hoy=HOY, hechos=None).vigente  # facts unknown: not checked
    # Numbers compare by value.
    assert huella(3) == huella(3.0) == huella(Decimal("3.00")) and huella(None) is None


def test_a_much_better_offer_reopens_it_once() -> None:
    decision = _decision(
        ambito="luz:cambio_tarifa",
        condiciones_de_reapertura=[{"tipo": "mejora", "importe_referencia": Decimal("40")}],
    )
    assert decision.condiciones_de_reapertura[0].factor == 2
    assert evaluar(decision, hoy=HOY, importe=Decimal("80")).vigente  # not MORE than twice
    mejor = evaluar(decision, hoy=HOY, importe=Decimal("80.01"))
    assert mejor.estado == "reabrible" and mejor.por == "mejora"
    # After that one comeback it still holds ordinary offers back, and never reopens for a better one again.
    reabierta = decision.model_copy(update={"reabierta_el": HOY, "reabierta_por": "mejora"})
    assert evaluar(reabierta, hoy=HOY, importe=Decimal("500")).vigente


def test_once_reopened_for_a_date_or_expiry_it_holds_nothing_back() -> None:
    decision = _decision(reabierta_el=HOY, reabierta_por="caducidad", caducidad=date(2026, 11, 1))
    assert evaluar(decision, hoy=HOY).estado == "reabierta"
    assert evaluar(_decision(sustituida_por="dec_" + "0" * 32), hoy=HOY).estado == "sustituida"


def test_conditions_are_validated() -> None:
    with pytest.raises(ValidationError):
        Condicion(tipo="fecha")
    with pytest.raises(ValidationError):
        Condicion(tipo="mejora")
    with pytest.raises(ValidationError):
        Condicion(tipo="hecho")


def test_notice_scopes() -> None:
    luz = ambito_de_aviso("modulo:luz", "modulo:luz:luz:ahorro:oferta-x:2026-10-31")
    # The proposal is about one offer; «No me interesa» answers the lever (any tariff change).
    assert (luz.propuesta, luz.respuesta) == ("luz:cambio_tarifa:oferta-x", "luz:cambio_tarifa")
    sub = ambito_de_aviso("modulo:suscripciones", "modulo:suscripciones:suscripciones:renovacion:netflix:2026-12-01")
    assert (sub.propuesta, sub.respuesta) == ("suscripciones:renovacion:netflix",) * 2
    assert ambito_de_aviso("plazo", "plazo:irpf_pago_fraccionado:2026:01").propuesta == (
        "fiscal:plazo:irpf_pago_fraccionado:2026:01"
    )
    assert ambito_de_aviso("fin_de_ejercicio", "fin_de_ejercicio:2026:abc").propuesta == "fiscal:plan_fin_de_ejercicio"
    assert (
        ambito_de_aviso("cambio_normativo", "novedad:BOE-A-2026-1").propuesta == "fiscal:cambio_normativo:BOE-A-2026-1"
    )
    assert ambito_de_aviso("resumen_semanal", "resumen_semanal:2026-W47") is None
    assert ambito_de_palanca("plan_pensiones_conyuge") == "fiscal:plan_pensiones:conyuge"
    assert ambito_de_palanca("tributacion", "conjunta_biparental") == "fiscal:tributacion:conjunta"
    assert ambito_de_accion("agua", "agua_cambiar_tarifa") == "agua:accion:cambiar_tarifa"
    assert etiqueta("luz:cambio_tarifa:oferta-x") == "Cambiar de tarifa de la luz (oferta-x)"


def test_words_are_compared_without_accents_case_or_punctuation() -> None:
    assert normalizar_texto("«No quiero» planes, ¡necesito LIQUIDEZ!") == "no quiero planes necesito liquidez"
