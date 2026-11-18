"""The deliberation engine end to end, with a scripted provider per purpose (no network, no model)."""

from __future__ import annotations

import contextvars
from datetime import UTC, datetime
from decimal import Decimal
import threading
from typing import Any

import pytest

from asistente.nucleo.agent.providers.base import ProviderError
from asistente.nucleo.consejo import prompts
from asistente.nucleo.consejo.contrato import (
    EVENTO_FASE,
    EVENTO_MIEMBRO,
    MotorDeliberacion,
    SesionConsejo,
)
from asistente.nucleo.consejo.deliberacion import Deliberacion, construir_motor, validar_criticas, validar_propuesta
from asistente.nucleo.consejo.miembros import PRESIDENTE, ROLES_POR_DEFECTO, FabricaProveedores
from asistente.nucleo.consejo.presidente import MARCA_RETIRADA, SalidaInvalidaError
from asistente.plataforma.ops.consumo import PresupuestoAgotadoError
from tests.consejo.motor.fakes import (
    PREFERENCIA,
    SESION,
    ProveedorConsejo,
    critica_de,
    criticas,
    criticas_por,
    dictamen,
    etiquetas_de,
    expediente,
    para,
    por_esquema,
    propuesta,
    proveedores_basicos,
    revisada,
)

ASIENTOS = [r.id for r in ROLES_POR_DEFECTO]
NOMBRES = {r.id: r.nombre for r in ROLES_POR_DEFECTO}
MARCAS = {"prudente": "zafiro", "fiscal": "rubí", "liquidez_familia": "ámbar", "abogado_del_diablo": "jade"}
RELOJ = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def motor(proveedores: dict[str, ProveedorConsejo], **kwargs: Any) -> Deliberacion:
    kwargs.setdefault("semilla", 1)
    kwargs.setdefault("reloj", lambda: RELOJ)
    return Deliberacion(proveedor_para=para(proveedores), **kwargs)


def de(proveedores: dict[str, ProveedorConsejo], asiento: str) -> ProveedorConsejo:
    return proveedores[f"consejo:miembro:{asiento}"]


def presidente(proveedores: dict[str, ProveedorConsejo]) -> ProveedorConsejo:
    return proveedores[PRESIDENTE.proposito]


# -- the happy path --------------------------------------------------------------------------------------------


def test_happy_path_rules_and_reveals_authorship_at_the_end() -> None:
    proveedores = proveedores_basicos()
    eventos: list[tuple[str, dict[str, Any]]] = []
    m = motor(proveedores)
    assert isinstance(m, MotorDeliberacion)
    sesion = m.deliberar(expediente(), sesion_id=SESION, emit=lambda tipo, **d: eventos.append((tipo, d)))

    assert sesion.estado == "terminada" and sesion.fase == "sintesis" and sesion.error is None
    assert [p.ronda for p in sesion.propuestas] == [1, 1, 1, 1, 2, 2, 2, 2]
    d = sesion.dictamen
    assert d is not None
    assert (d.recomendacion, d.consenso) == ("o1", "mayoria")
    assert d.disenso and d.resumen.startswith("La mayoría del consejo")
    assert {p.opcion_id: p.miembros for p in d.posturas} == {
        "o1": ["prudente", "fiscal", "liquidez_familia"],
        "o2": ["abogado_del_diablo"],
    }
    # Authorship revealed: who served each seat (and the chair), and the public labels.
    assert [(x.rol.id, x.proveedor, x.modelo) for x in d.miembros] == [
        ("prudente", "fam0", "modelo-prudente"),
        ("fiscal", "fam1", "modelo-fiscal"),
        ("liquidez_familia", "fam0", "modelo-liquidez_familia"),
        ("abogado_del_diablo", "fam1", "modelo-abogado_del_diablo"),
        ("presidente", "fam0", "modelo-presidente"),
    ]
    assert sorted(d.autoria.values()) == sorted(ASIENTOS) and sorted(d.autoria) == ["A", "B", "C", "D"]
    assert d.puerta is not None and d.puerta.ok
    # 4 proposals + 4 critiques + 4 revisions + 1 chair; no usage reported by these doubles.
    assert sesion.coste.llamadas == 13
    assert any("no se conoce" in a for a in d.avisos)
    # Every reviewer reviewed the three others; stored critiques and rankings use the ruling's labels.
    assert len(sesion.criticas) == 12 and len(sesion.rankings) == 4
    for ranking in sesion.rankings:
        propia = next(e for e, a in d.autoria.items() if a == ranking.evaluador_id)
        assert propia not in ranking.orden and len(ranking.orden) == 3
    assert all(d.autoria[c.etiqueta] != c.evaluador_id for c in sesion.criticas)
    # One call per seat per phase, then the chair.
    assert [len(de(proveedores, a).json_requests) for a in ASIENTOS] == [3, 3, 3, 3]
    assert len(presidente(proveedores).json_requests) == 1
    fases = [(d["fase"], d["estado"]) for t, d in eventos if t == EVENTO_FASE]
    assert fases == [
        ("propuestas", "inicio"),
        ("propuestas", "fin"),
        ("criticas", "inicio"),
        ("criticas", "fin"),
        ("revision", "inicio"),
        ("revision", "fin"),
        ("sintesis", "inicio"),
        ("sintesis", "fin"),
        ("sintesis", "terminada"),
    ]
    assert {d["miembro_id"] for t, d in eventos if t == EVENTO_MIEMBRO} == {*ASIENTOS, "presidente"}


def test_round_one_is_identical_for_all_and_the_preference_is_sealed_until_revision() -> None:
    proveedores = proveedores_basicos()
    motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    esperado = prompts.mensaje_propuesta(expediente())
    for asiento in ASIENTOS:
        peticiones = de(proveedores, asiento).json_requests
        propuesta_r1, critica, revision = peticiones
        assert propuesta_r1["user"] == esperado  # the same dossier for every member
        assert propuesta_r1["schema_name"] == "propuesta_consejo"
        assert NOMBRES[asiento] in propuesta_r1["system"]  # the seat's mandate is in its own system prompt
        assert PREFERENCIA not in propuesta_r1["user"] and PREFERENCIA not in propuesta_r1["system"]
        assert PREFERENCIA not in critica["user"]
        assert "Preferencia declarada" not in propuesta_r1["user"] + critica["user"]
        assert f"Preferencia declarada por el usuario: «{PREFERENCIA}»" in revision["user"]
        assert revision["schema_name"] == "propuesta_revisada_consejo"
    assert f"«{PREFERENCIA}»" in presidente(proveedores).json_requests[0]["user"]


def test_critique_requests_hide_authorship_and_exclude_the_reviewer() -> None:
    marcadas = {
        a: ProveedorConsejo(
            [
                propuesta("o1", razonamiento=f"Análisis {MARCAS[a]}: la cuota C1 (850 €/mes) pesa en el hogar."),
                criticas,
                revisada("o1"),
            ],
            name=f"proveedor-{a}",
            model=f"modelo-{a}",
        )
        for a in ASIENTOS
    }
    proveedores = {f"consejo:miembro:{a}": p for a, p in marcadas.items()}
    proveedores[PRESIDENTE.proposito] = ProveedorConsejo([dictamen(disenso="")])
    m = motor(proveedores)
    m.deliberar(expediente(), sesion_id=SESION)
    for revisor in ASIENTOS:
        peticion = marcadas[revisor].json_requests[1]
        bloque = peticion["user"].split("<propuestas>")[1]
        assert MARCAS[revisor] not in bloque  # its own proposal is not there
        assert all(MARCAS[o] in bloque for o in ASIENTOS if o != revisor)
        for otro in ASIENTOS:  # no seat names, ids or models in what the reviewer reads
            assert NOMBRES[otro] not in peticion["user"] and f"modelo-{otro}" not in peticion["user"]
            assert otro not in bloque
        # The labels the reviewer saw point at the right authors.
        for etiqueta, autor in m.registro.presentaciones[revisor].items():
            linea = next(x for x in bloque.splitlines() if f'"etiqueta": "{etiqueta}"' in x)
            assert MARCAS[autor] in linea
    # Balanced design: across reviewers every proposal was shown once in each position.
    posiciones: dict[str, set[str]] = {}
    for mapa in m.registro.presentaciones.values():
        for etiqueta, autor in mapa.items():
            posiciones.setdefault(autor, set()).add(etiqueta)
    assert all(v == {"A", "B", "C"} for v in posiciones.values())


def test_critiques_reach_the_author_of_the_proposal_they_were_about() -> None:
    def critica_marcada(revisor: str) -> Any:
        def responder(peticion: dict[str, Any]) -> dict[str, Any]:
            etiquetas = etiquetas_de(peticion)
            resultado = {"criticas": [critica_de(e) for e in etiquetas], "ranking": etiquetas}
            for c in resultado["criticas"]:
                c["fortalezas"] = [f"Nota {MARCAS[revisor]} sobre {c['etiqueta']}: clara."]
            return resultado

        return responder

    proveedores = {
        f"consejo:miembro:{a}": ProveedorConsejo([propuesta("o1"), critica_marcada(a), revisada("o1")])
        for a in ASIENTOS
    }
    proveedores[PRESIDENTE.proposito] = ProveedorConsejo([dictamen(disenso="")])
    m = motor(proveedores)
    sesion = m.deliberar(expediente(), sesion_id=SESION)
    for revisor, mapa in m.registro.presentaciones.items():
        for etiqueta, autor in mapa.items():
            nota = f"Nota {MARCAS[revisor]} sobre {etiqueta}: clara."
            for asiento in ASIENTOS:
                revision = de(proveedores, asiento).json_requests[2]["user"]
                assert (nota in revision) == (asiento == autor)
                assert MARCAS[revisor] not in revision or asiento != revisor or nota not in revision
    # Stored critiques, relabelled to the ruling's labels, still say who they were about.
    assert sesion.dictamen is not None
    for critica in sesion.criticas:
        autor = sesion.dictamen.autoria[critica.etiqueta]
        vista = next(e for e, a in m.registro.presentaciones[critica.evaluador_id].items() if a == autor)
        assert critica.fortalezas == [f"Nota {MARCAS[critica.evaluador_id]} sobre {vista}: clara."]


def test_only_the_devils_advocate_learns_the_leading_option() -> None:
    proveedores = proveedores_basicos(("o1", "o1", "o2", "o2"))  # tie in round 1: no leader to argue against
    motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    assert all("<opcion_lider>" not in de(proveedores, a).json_requests[2]["user"] for a in ASIENTOS)
    proveedores = proveedores_basicos()
    motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    for asiento in ASIENTOS:
        revision = de(proveedores, asiento).json_requests[2]["user"]
        assert ("<opcion_lider>" in revision) == (asiento == "abogado_del_diablo")
    assert "es o1." in de(proveedores, "abogado_del_diablo").json_requests[2]["user"]


# -- invalid outputs -------------------------------------------------------------------------------------------


def test_invalid_json_is_repaired_once() -> None:
    proveedores = proveedores_basicos()
    de(proveedores, "fiscal").json_responses.insert(0, ProviderError("no es JSON", salida_invalida=True))
    eventos: list[dict[str, Any]] = []
    sesion = motor(proveedores).deliberar(
        expediente(), sesion_id=SESION, emit=lambda t, **d: eventos.append(d) if t == EVENTO_MIEMBRO else None
    )
    assert sesion.estado == "terminada"
    peticiones = de(proveedores, "fiscal").json_requests
    assert len(peticiones) == 4
    assert "no se puede usar: no era un objeto JSON válido" in peticiones[1]["user"]
    assert peticiones[1]["user"].startswith(peticiones[0]["user"])
    assert {"miembro_id": "fiscal", "fase": "propuestas", "estado": "reparado"} in eventos


def test_a_seat_that_fails_twice_is_dropped_and_the_others_go_on() -> None:
    proveedores = proveedores_basicos()
    malo = {**propuesta("o9")}
    de(proveedores, "fiscal").json_responses[:3] = [malo, malo]
    sesion = motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    assert sesion.estado == "terminada"
    peticiones = de(proveedores, "fiscal").json_requests
    assert len(peticiones) == 2 and "«recomendacion» debe ser una de: o1, o2, depende" in peticiones[1]["user"]
    assert "fiscal" not in {p.miembro_id for p in sesion.propuestas}
    assert sesion.dictamen is not None
    assert any("Fiscalidad y marco legal" in a and "quedó fuera" in a for a in sesion.dictamen.avisos)
    assert sorted(sesion.dictamen.autoria.values()) == ["abogado_del_diablo", "liquidez_familia", "prudente"]
    for asiento in ("prudente", "liquidez_familia", "abogado_del_diablo"):
        assert etiquetas_de(de(proveedores, asiento).json_requests[1]) == ["A", "B"]


def test_fewer_than_three_valid_proposals_fail_honestly() -> None:
    proveedores = proveedores_basicos()
    for asiento in ("fiscal", "prudente"):
        de(proveedores, asiento).json_responses[:3] = [{"nada": 1}, {"nada": 1}]
    sesion = motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    assert sesion.estado == "fallida" and sesion.fase == "propuestas" and sesion.dictamen is None
    assert sesion.error and "Solo 2 de los 4 miembros" in sesion.error
    assert not presidente(proveedores).json_requests
    assert len(sesion.propuestas) == 2


def test_a_provider_bug_never_breaks_the_session() -> None:
    proveedores = proveedores_basicos()
    de(proveedores, "prudente").json_responses[0] = RuntimeError("fallo inesperado")
    sesion = motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    assert sesion.estado == "terminada"
    assert "prudente" not in {p.miembro_id for p in sesion.propuestas}


def test_a_seat_whose_provider_cannot_be_built_leaves_and_the_rest_deliberate() -> None:
    proveedores = proveedores_basicos()

    def para_con_fallo(proposito: str) -> ProveedorConsejo:
        if proposito == "consejo:miembro:fiscal":
            raise RuntimeError("sin credenciales")
        return proveedores[proposito]

    sesion = Deliberacion(proveedor_para=para_con_fallo, semilla=1).deliberar(expediente(), sesion_id=SESION)
    assert sesion.estado == "terminada" and sesion.dictamen is not None
    assert "fiscal" not in {p.miembro_id for p in sesion.propuestas}
    assert any("No se pudo contactar con el modelo de «Fiscalidad y marco legal»" in a for a in sesion.dictamen.avisos)
    assert "fiscal" not in [m.rol.id for m in sesion.dictamen.miembros]


def test_depende_needs_its_condition() -> None:
    proveedores = proveedores_basicos()
    de(proveedores, "fiscal").json_responses[0] = propuesta("depende")
    de(proveedores, "fiscal").json_responses.insert(
        1, propuesta("depende", condicion="Depende de si el diferencial supera el rendimiento esperado.")
    )
    motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    assert "«condicion» es obligatoria" in de(proveedores, "fiscal").json_requests[1]["user"]


def test_a_ranking_that_is_not_a_permutation_is_rejected_and_repaired() -> None:
    proveedores = proveedores_basicos()

    def repetido(peticion: dict[str, Any]) -> dict[str, Any]:
        etiquetas = etiquetas_de(peticion)
        return {"criticas": [critica_de(e) for e in etiquetas], "ranking": [etiquetas[0], etiquetas[0], etiquetas[1]]}

    de(proveedores, "prudente").json_responses[1:2] = [repetido, criticas]
    sesion = motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    peticiones = de(proveedores, "prudente").json_requests
    assert "exactamente una vez" in peticiones[2]["user"] and "A, A, B" in peticiones[2]["user"]
    assert len(sesion.rankings) == 4


def test_a_reviewer_that_fails_twice_loses_its_critiques_not_its_proposal() -> None:
    proveedores = proveedores_basicos()

    def incompleto(peticion: dict[str, Any]) -> dict[str, Any]:
        etiquetas = etiquetas_de(peticion)
        return {"criticas": [critica_de(etiquetas[0])], "ranking": etiquetas}

    de(proveedores, "prudente").json_responses[1:2] = [incompleto, incompleto]
    sesion = motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    assert sesion.estado == "terminada" and sesion.dictamen is not None
    assert "prudente" not in {r.evaluador_id for r in sesion.rankings}
    assert len(sesion.rankings) == 3 and len(sesion.criticas) == 9
    assert "prudente" in sesion.dictamen.autoria.values()
    assert any("críticas de «Prudencia y riesgo financiero»" in a for a in sesion.dictamen.avisos)


def test_a_member_that_cannot_revise_stands_by_its_first_proposal() -> None:
    proveedores = proveedores_basicos(("o1", "o1", "o2", "o2"), revision=("o1", "o1", "o1", "o2"))
    de(proveedores, "liquidez_familia").json_responses[2:] = [{"x": 1}, {"x": 1}]
    sesion = motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    assert sesion.dictamen is not None
    assert [p.miembro_id for p in sesion.propuestas if p.ronda == 2] == ["prudente", "fiscal", "abogado_del_diablo"]
    # liquidez_familia counts with its round-1 vote (o2): 2–2, the council is split.
    assert sesion.dictamen.consenso == "dividido" and sesion.dictamen.recomendacion == "depende"
    assert any("cuenta la de la primera ronda" in a for a in sesion.dictamen.avisos)


def test_strict_validators() -> None:
    exp = expediente()
    p, desconocidas = validar_propuesta(
        {**propuesta("O1", cifras=["c1", "C9", "[C2]"]), "extra": 1}, miembro_id="fiscal", ronda=1, expediente=exp
    )
    assert p.recomendacion == "o1" and p.cifras_usadas == ["C1", "C2"] and desconocidas == ["C9"]
    for malo in (
        {**propuesta(), "confianza": 1.5},
        {**propuesta(), "confianza": True},
        {**propuesta(), "razonamiento": "corto"},
        {**propuesta(), "supuestos": "no es lista"},
        ["no", "es", "objeto"],
    ):
        with pytest.raises(SalidaInvalidaError):
            validar_propuesta(malo, miembro_id="fiscal", ronda=1, expediente=exp)
    with pytest.raises(SalidaInvalidaError):
        validar_propuesta(propuesta(), miembro_id="fiscal", ronda=2, expediente=exp)  # «cambios» is mandatory
    largo = validar_propuesta(
        {**propuesta(), "razonamiento": "x " * 3000}, miembro_id="fiscal", ronda=1, expediente=exp
    )
    assert len(largo[0].razonamiento) <= 3500  # caps cut, never reject

    buenas = {"criticas": [critica_de("A"), critica_de("B")], "ranking": ["b", "A"]}
    lista, ranking = validar_criticas(buenas, evaluador_id="fiscal", etiquetas=["A", "B"])
    assert [c.etiqueta for c in lista] == ["A", "B"] and ranking.orden == ["B", "A"]
    for mala in (
        {"criticas": [critica_de("A")], "ranking": ["A", "B"]},
        {"criticas": [critica_de("A"), critica_de("C")], "ranking": ["A", "B"]},
        {"criticas": [critica_de("A"), critica_de("A")], "ranking": ["A", "B"]},
        {"criticas": [critica_de("A"), critica_de("B")], "ranking": ["A"]},
        {"criticas": [critica_de("A"), critica_de("B")], "ranking": ["A", "B", "C"]},
        {"criticas": [critica_de("A"), {**critica_de("B"), "puntuacion": {"rigor": 9}}], "ranking": ["A", "B"]},
        {"criticas": [critica_de("A", errores=[{"tipo": "otro", "texto": "algo raro aquí"}]), critica_de("B")]},
    ):
        with pytest.raises(SalidaInvalidaError):
            validar_criticas(mala, evaluador_id="fiscal", etiquetas=["A", "B"])


# -- figure discipline -----------------------------------------------------------------------------------------


def test_an_invented_figure_is_caught_shown_to_reviewers_author_and_ruling() -> None:
    proveedores = proveedores_basicos()
    inventada = propuesta(
        "o1",
        razonamiento="Recomiendo o1: entre los intereses (C2) y la deducción (C3) ahorras 7.600 € en total.",
        cifras=["C2", "C3", "C9"],
    )
    de(proveedores, "fiscal").json_responses[0] = inventada
    m = motor(proveedores)
    sesion = m.deliberar(expediente(), sesion_id=SESION)
    puerta = m.registro.puertas["fiscal:1"]
    tipos = {(v.tipo, v.literal) for v in puerta.violaciones}
    assert ("suma_no_combinada", "7.600 €") in tipos  # 6.400 € (hipoteca) + 1.200 € (fiscal): nobody computed it
    assert ("cifra_sin_origen", "C9") in tipos  # a figure id the dossier does not have
    assert all(m.registro.puertas[f"{a}:1"].ok for a in ASIENTOS if a != "fiscal")
    # The reviewers see the check next to that proposal (and only that one).
    for revisor in ASIENTOS:
        if revisor == "fiscal":
            continue
        peticion = de(proveedores, revisor).json_requests[1]
        etiqueta = next(e for e, a in m.registro.presentaciones[revisor].items() if a == "fiscal")
        linea = next(x for x in peticion["user"].splitlines() if f'"etiqueta": "{etiqueta}"' in x)
        assert '"comprobacion_cifras"' in linea and "7.600 €" in linea
        assert peticion["user"].count('"comprobacion_cifras"') == 1
    # The author must fix it in the revision; the ruling lists it.
    assert "<comprobacion_cifras>" in de(proveedores, "fiscal").json_requests[2]["user"]
    assert sesion.dictamen is not None
    assert any("Fiscalidad y marco legal" in a and "7.600 €" in a for a in sesion.dictamen.avisos)


def test_dossier_texts_and_the_unsealed_preference_are_not_violations() -> None:
    proveedores = proveedores_basicos()
    de(proveedores, "prudente").json_responses[0] = propuesta(
        "o1", razonamiento="Con 45 años y la cuota C1 (850 €/mes), amortizar reduce el riesgo del hogar."
    )
    m = motor(proveedores)
    m.deliberar(expediente(), sesion_id=SESION)
    assert m.registro.puertas["prudente:1"].ok


def test_the_chair_gets_one_correction_and_then_figures_are_withdrawn() -> None:
    con_cifra = dictamen(resumen="La mayoría recomienda amortizar: en total ahorras 7.600 € entre todo.")
    proveedores = proveedores_basicos(presidente=[con_cifra, con_cifra])
    sesion = motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    peticiones = presidente(proveedores).json_requests
    assert len(peticiones) == 2
    assert "<tu_dictamen_anterior>" in peticiones[1]["user"] and "7.600 €" in peticiones[1]["user"]
    d = sesion.dictamen
    assert d is not None and MARCA_RETIRADA in d.resumen and "7.600" not in d.resumen
    assert d.puerta is not None and d.puerta.ok
    assert any("Se retiraron del dictamen" in a for a in d.avisos)


def test_a_corrected_chair_draft_is_used() -> None:
    con_cifra = dictamen(resumen="La mayoría recomienda amortizar: en total ahorras 7.600 € entre todo.")
    proveedores = proveedores_basicos(presidente=[con_cifra, dictamen()])
    sesion = motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    assert sesion.dictamen is not None and sesion.dictamen.resumen == dictamen()["resumen"]
    assert not any("Se retiraron" in a for a in sesion.dictamen.avisos)


# -- the chair cannot decide what the council decided ---------------------------------------------------------


def test_the_chair_cannot_override_the_consensus_recommendation_or_confidence() -> None:
    abusivo = dictamen(consenso="unanime", recomendacion="o2", confianza=0.99, acuerdo=1.0)
    proveedores = proveedores_basicos(presidente=[abusivo])
    sesion = motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    d = sesion.dictamen
    assert d is not None
    assert (d.consenso, d.recomendacion) == ("mayoria", "o1")
    assert d.confianza == pytest.approx(0.35)  # 0.7 × (2·0.75 + W=0) / 3: computed, not the chair's 0.99
    assert d.acuerdo == 0.0
    for campo in ("consenso", "recomendacion", "confianza", "acuerdo"):
        assert f"El presidente intentó fijar «{campo}»; se mantiene el valor calculado por el recuento." in d.avisos
    mensaje = presidente(proveedores).json_requests[0]["user"]
    assert "Consenso (fijado por el recuento): mayoría." in mensaje
    assert "o1 («Amortizar anticipadamente parte de la hipoteca»)" in mensaje


def test_the_chair_must_report_dissent_and_falls_back_to_code_when_it_cannot() -> None:
    sin_disenso = dictamen(disenso="")
    proveedores = proveedores_basicos(presidente=[sin_disenso, sin_disenso])
    sesion = motor(proveedores).deliberar(expediente(), sesion_id=SESION)
    peticiones = presidente(proveedores).json_requests
    assert len(peticiones) == 2 and "«disenso» es obligatorio" in peticiones[1]["user"]
    d = sesion.dictamen
    assert d is not None and d.consenso == "mayoria"
    assert "El presidente no pudo redactar la síntesis" in d.resumen
    assert d.disenso and d.disenso.startswith("El mejor argumento de la postura minoritaria (o2")
    assert d.puerta is not None and d.puerta.ok


def test_unanimity_has_no_dissent_and_a_split_council_says_depende() -> None:
    proveedores = proveedores_basicos(("o1",) * 4, presidente=[dictamen(disenso="Algo que no hace falta decir aquí.")])
    d = motor(proveedores).deliberar(expediente(), sesion_id=SESION).dictamen
    assert d is not None and d.consenso == "unanime" and d.disenso is None and d.recomendacion == "o1"
    dividido = dictamen(condicion="Depende de si el diferencial de la hipoteca supera la rentabilidad esperada.")
    proveedores = proveedores_basicos(("o1", "o2", "o1", "o2"), presidente=[dividido])
    d = motor(proveedores).deliberar(expediente(), sesion_id=SESION).dictamen
    assert d is not None and (d.consenso, d.recomendacion) == ("dividido", "depende")
    assert d.condicion and d.condicion.startswith("Depende de si el diferencial")


def test_agreement_and_aggregate_order_from_consistent_reviewers() -> None:
    confianzas = {"prudente": 0.9, "fiscal": 0.8, "liquidez_familia": 0.6, "abogado_del_diablo": 0.5}
    por_confianza = criticas_por(lambda p: p["confianza"])
    proveedores = {
        f"consejo:miembro:{a}": ProveedorConsejo(
            [propuesta("o1", confianza=c), por_confianza, revisada("o1", confianza=c)]
        )
        for a, c in confianzas.items()
    }
    proveedores[PRESIDENTE.proposito] = ProveedorConsejo([dictamen(disenso="")])
    m = motor(proveedores)
    d = m.deliberar(expediente(), sesion_id=SESION).dictamen
    assert d is not None and d.acuerdo == 1.0
    assert m.registro.agregacion is not None
    assert m.registro.agregacion.orden == ["prudente", "fiscal", "liquidez_familia", "abogado_del_diablo"]
    assert d.confianza == pytest.approx(0.7)  # unanimous, W = 1: the mean confidence itself
    mensaje = presidente(proveedores).json_requests[0]["user"]
    assert "Acuerdo entre las valoraciones de las críticas: alto." in mensaje
    orden = [next(e for e, a in d.autoria.items() if a == s) for s in m.registro.agregacion.orden]
    assert f"(de mejor a peor): {', '.join(orden)}." in mensaje


# -- cost, cap and budget --------------------------------------------------------------------------------------


def _con_uso(chair_model: str = "claude-haiku-5-5", **kwargs: Any) -> dict[str, ProveedorConsejo]:
    proveedores = proveedores_basicos(uso=(1000, 500), modelo_uso="claude-haiku-5-5", **kwargs)
    presidente(proveedores).model = chair_model
    return proveedores


def test_cost_is_accounted_per_call_with_the_price_table() -> None:
    sesion = motor(_con_uso()).deliberar(expediente(), sesion_id=SESION)
    # claude-haiku-5-5: 0.10 USD/M input, 0.50 USD/M output → 0.0001 + 0.00025 per call, 13 calls.
    assert sesion.coste.llamadas == 13
    assert sesion.coste.tokens_entrada == 13_000 and sesion.coste.tokens_salida == 6_500
    assert sesion.coste.usd == Decimal("0.004550")
    assert sesion.dictamen is not None and not any("no se conoce" in a for a in sesion.dictamen.avisos)


def test_the_cap_stops_before_the_synthesis_and_rules_partially() -> None:
    proveedores = _con_uso(chair_model="claude-fable-5-2")  # an expensive chair: its projection exceeds the cap
    eventos: list[tuple[str, dict[str, Any]]] = []
    m = motor(proveedores)
    sesion = m.deliberar(
        expediente(), sesion_id=SESION, tope_usd=Decimal("0.02"), emit=lambda t, **d: eventos.append((t, d))
    )
    assert not presidente(proveedores).json_requests
    assert sesion.estado == "terminada" and sesion.fase == "revision"
    assert sesion.coste.llamadas == 12 and sesion.coste.usd <= Decimal("0.02")
    d = sesion.dictamen
    assert d is not None and d.avisos[0].startswith("Dictamen parcial: Se detuvo antes de la síntesis del presidente")
    assert "Este dictamen es parcial" in d.resumen
    assert (d.consenso, d.recomendacion) == ("mayoria", "o1")
    assert "presidente" not in [x.rol.id for x in d.miembros]
    assert d.puerta is not None and d.puerta.ok
    assert ("sintesis", "inicio") not in [(x["fase"], x["estado"]) for t, x in eventos if t == EVENTO_FASE]
    assert m.registro.parada and "tope de coste" in m.registro.parada


def test_the_cap_before_the_critiques_rules_on_round_one_or_fails_if_asked() -> None:
    proveedores = _con_uso()
    sesion = motor(proveedores).deliberar(expediente(), sesion_id=SESION, tope_usd=Decimal("0.0015"))
    assert sesion.fase == "propuestas" and sesion.estado == "terminada"
    assert [len(de(proveedores, a).json_requests) for a in ASIENTOS] == [1, 1, 1, 1]
    assert sesion.dictamen is not None and sesion.dictamen.acuerdo is None and not sesion.rankings
    proveedores = _con_uso()
    sesion = motor(proveedores, dictamen_parcial=False).deliberar(
        expediente(), sesion_id=SESION, tope_usd=Decimal("0.0015")
    )
    assert sesion.estado == "fallida" and sesion.dictamen is None and sesion.fase == "propuestas"
    assert sesion.error and "tope de coste" in sesion.error


def test_a_spent_budget_stops_the_session_without_more_calls() -> None:
    proveedores = proveedores_basicos()
    agotado = PresupuestoAgotadoError("Se ha alcanzado el límite.", limite="llamadas_por_pregunta")
    de(proveedores, "fiscal").json_responses[1] = agotado
    sesion = motor(proveedores, max_paralelo=1).deliberar(expediente(), sesion_id=SESION)
    assert sesion.estado == "terminada" and sesion.fase == "criticas"
    assert len(de(proveedores, "liquidez_familia").json_requests) == 1  # nothing scheduled after the budget ran out
    assert not presidente(proveedores).json_requests
    assert sesion.dictamen is not None and "presupuesto" in sesion.dictamen.avisos[0]


# -- parallelism, context, determinism -------------------------------------------------------------------------

VARIABLE: contextvars.ContextVar[str] = contextvars.ContextVar("variable_de_prueba", default="sin contexto")


def test_members_run_in_parallel_in_a_copy_of_the_callers_context() -> None:
    barrera = threading.Barrier(4)
    vistos: list[str] = []
    hilos: set[str] = set()

    def con_contexto(respuesta: Any) -> Any:
        def responder(peticion: dict[str, Any]) -> dict[str, Any]:
            vistos.append(VARIABLE.get())
            hilos.add(threading.current_thread().name)
            return respuesta(peticion) if callable(respuesta) else respuesta

        return responder

    proveedores = {
        rol.proposito: ProveedorConsejo(
            [con_contexto(propuesta("o1")), con_contexto(criticas), con_contexto(revisada("o1"))], barrera=barrera
        )
        for rol in ROLES_POR_DEFECTO
    }
    proveedores[PRESIDENTE.proposito] = ProveedorConsejo([dictamen(disenso="")])
    token = VARIABLE.set("la pregunta del usuario")
    try:
        # Four calls must be in flight at once to pass each barrier: a sequential engine would time out.
        sesion = motor(proveedores, max_paralelo=4).deliberar(expediente(), sesion_id=SESION)
    finally:
        VARIABLE.reset(token)
    assert sesion.estado == "terminada" and len(sesion.rankings) == 4
    assert vistos == ["la pregunta del usuario"] * 12
    assert len(hilos) > 1 and all(h.startswith("consejo") for h in hilos)


def _sesion(semilla: int, **kwargs: Any) -> tuple[SesionConsejo, Deliberacion]:
    m = motor(proveedores_basicos(**kwargs), semilla=semilla)
    return m.deliberar(expediente(), sesion_id=SESION), m


def test_the_same_seed_reproduces_the_session_exactly() -> None:
    primera, m1 = _sesion(5)
    segunda, m2 = _sesion(5)
    assert primera.model_dump() == segunda.model_dump()
    assert m1.registro is not None and m2.registro is not None
    assert m1.registro.presentaciones == m2.registro.presentaciones
    otras = {str(_sesion(s)[1].registro.presentaciones) for s in range(6)}  # type: ignore[union-attr]
    assert len(otras) > 1  # another seed, other orders...
    assert {_sesion(s)[0].dictamen.consenso for s in range(3)} == {"mayoria"}  # type: ignore[union-attr]


def test_authorship_of_critiques_for_the_bias_harness() -> None:
    sesion, m = _sesion(9)
    autoria = m.autoria_criticas(sesion)
    assert m.registro is not None and len(autoria) == 12
    for (revisor, etiqueta), (asiento, posicion) in autoria.items():
        assert sesion.dictamen is not None and sesion.dictamen.autoria[etiqueta] == asiento
        assert list(m.registro.presentaciones[revisor].values())[posicion] == asiento
    # The same answer from the stored session alone (another engine with the same configuration).
    otro = motor(proveedores_basicos(), semilla=9)
    assert otro.autoria_criticas(sesion) == autoria
    # Balanced design: each proposal at each position exactly once across its reviewers.
    posiciones: dict[str, list[int]] = {}
    for asiento, posicion in autoria.values():
        posiciones.setdefault(asiento, []).append(posicion)
    assert all(sorted(p) == [0, 1, 2] for p in posiciones.values())
    assert m.semilla == 9


def test_sequential_and_parallel_runs_agree() -> None:
    secuencial = motor(proveedores_basicos(), max_paralelo=1).deliberar(expediente(), sesion_id=SESION)
    paralela = motor(proveedores_basicos(), max_paralelo=4).deliberar(expediente(), sesion_id=SESION)
    assert secuencial.model_dump() == paralela.model_dump()


# -- diversity -------------------------------------------------------------------------------------------------


def test_two_families_configured_seats_alternate_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    from asistente.plataforma import settings

    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    votos = {"Prudencia": "o1", "Fiscalidad": "o1", "Liquidez": "o2", "Abogado": "o1"}
    creados: dict[str, ProveedorConsejo] = {}

    def crear(familia: str, esfuerzo: str | None) -> ProveedorConsejo:
        creados[familia] = ProveedorConsejo([], name=familia, model=f"{familia}-modelo", por_defecto=por_esquema(votos))
        return creados[familia]

    fabrica = FabricaProveedores(
        ROLES_POR_DEFECTO, familias="anthropic,openai", disponible=lambda f: True, crear=crear, medir=False
    )
    m = Deliberacion(ROLES_POR_DEFECTO, proveedor_para=fabrica, max_paralelo=1, semilla=2, reloj=lambda: RELOJ)
    sesion = m.deliberar(expediente(), sesion_id=SESION)
    d = sesion.dictamen
    assert d is not None
    assert [(x.rol.id, x.proveedor) for x in d.miembros] == [
        ("prudente", "anthropic"),
        ("fiscal", "openai"),
        ("liquidez_familia", "anthropic"),
        ("abogado_del_diablo", "openai"),
        ("presidente", "anthropic"),
    ]
    assert (d.consenso, d.recomendacion) == ("mayoria", "o1")
    # Members of different families critique each other.
    assert len(creados["openai"].json_requests) == 6 and len(creados["anthropic"].json_requests) == 7


# -- the engine's contract -------------------------------------------------------------------------------------


def test_identity_fields_come_from_the_service_session() -> None:
    base = SesionConsejo(
        id=SESION,
        case_id="caso-1",
        user_id="u1",
        estado="deliberando",
        pregunta_usuario="¿Amortizo o invierto? Yo quiero amortizar.",
        creada=datetime(2026, 1, 1, tzinfo=UTC),
        actualizada=datetime(2026, 1, 1, tzinfo=UTC),
        estimacion_usd=Decimal("0.4"),
        tope_usd=Decimal("0.8"),
    )
    sesion = motor(proveedores_basicos()).deliberar(expediente(), sesion_id=SESION, base=base)
    assert (sesion.case_id, sesion.user_id, sesion.pregunta_usuario) == ("caso-1", "u1", base.pregunta_usuario)
    assert sesion.creada == base.creada and sesion.actualizada == RELOJ
    assert sesion.tope_usd == Decimal("0.8") and sesion.estado == "terminada"


def test_invalid_arguments_are_programming_errors() -> None:
    proveedores = proveedores_basicos()
    with pytest.raises(ValueError):
        motor(proveedores).deliberar(expediente(), sesion_id="cons_corto")
    with pytest.raises(ValueError):
        Deliberacion(ROLES_POR_DEFECTO[:2], proveedor_para=para(proveedores))
    with pytest.raises(ValueError):
        Deliberacion([*ROLES_POR_DEFECTO, ROLES_POR_DEFECTO[0]], proveedor_para=para(proveedores))
    with pytest.raises(ValueError):
        Deliberacion([*ROLES_POR_DEFECTO[:3], PRESIDENTE], proveedor_para=para(proveedores))


def test_production_factory_builds_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    from asistente.plataforma import settings

    monkeypatch.setattr(settings, "CONSEJO_SEMILLA", "17")
    monkeypatch.setattr(settings, "CONSEJO_MAX_PARALELO", 2)
    m = construir_motor()
    assert [r.id for r in m.roles] == [r.id for r in ROLES_POR_DEFECTO]
    assert m.presidente == PRESIDENTE
    assert m._semilla == 17 and m._max_paralelo == 2
    assert isinstance(m._proveedor_para, FabricaProveedores)
