"""The learning loop: every «esto está mal» becomes a masked candidate; a reviewer accepts it into the private
regression cases (with a negative control) or rejects it; privacy follows the case and the account."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path

import pytest

from asistente.nucleo.cerebro.contrato import (
    AtribucionCifra,
    Cifra,
    Encargo,
    InformeCerebro,
    Intencion,
    ResultadoPuerta,
    ResumenInforme,
    Violacion,
)
from asistente.nucleo.decision.audit import ELIMINADO
from asistente.nucleo.decision.schemas import CaseState, Fact, FactValueType
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.evaluacion import privado, revision
from asistente.nucleo.evaluacion import suite as s
from asistente.nucleo.evaluacion.captura import CONSENTIMIENTO_VERSION, FeedbackEntrada, FeedbackStore, capturar
from asistente.nucleo.evaluacion.modelos import CasoEvaluacion, ExpCifra, ExpCifraAusente, ExpEspecialistas
from asistente.nucleo.evaluacion.reproduccion import ProveedorReproduccion, reproducir_capturas
from asistente.nucleo.evaluacion.revision import (
    AceptacionRechazadaError,
    CifraEsperadaEdicion,
    ComportamientoEsperado,
    EdicionCaso,
)
from asistente.nucleo.evaluacion.runner import Entorno, evaluar
from asistente.nucleo.evaluacion.variantes import VARIANTE_BASE
from asistente.plataforma.ops.consumo import ConsumoStore

PRESTAMO = {
    "capital_inicial": "100000",
    "fecha_firma": "2020-01-15",
    "plazo_meses": 300,
    "tipo": "fijo",
    "tipo_inicial": "3",
}
PREGUNTA = "Soy Ana Pérez: ¿cuánto pago entre la hipoteca y la luz este mes?"
RESPUESTA = "Entre la cuota (474,21 €) y la luz (57,79 €) pagas 532,00 € [F1]. Escríbeme a ana@example.com."


def _hecho(nombre: str, valor, tipo: FactValueType = FactValueType.STRING) -> Fact:  # type: ignore[no-untyped-def]
    return Fact(fact_id=f"fact_{nombre}", name=nombre, value=valor, value_type=tipo, source="usuario", confidence=1.0)


def informe_cerebro(*, violaciones: bool = True) -> dict:  # type: ignore[type-arg]
    intencion = Intencion(
        tipo="cruzada",
        encargos=[
            Encargo(id="e1", especialista="hipoteca", pregunta="Cuota de la hipoteca de Ana Pérez", motivo="cuota"),
            Encargo(id="e2", especialista="luz", pregunta="Factura de luz del mes"),
        ],
        confianza=0.92,
        origen="reglas",
        resumen="Consulto a Hipoteca y a Luz en paralelo",
    )
    cuota = Cifra(
        id="C1",
        especialista="hipoteca",
        concepto="cuota mensual",
        valor=Decimal("474.21"),
        unidad="EUR",
        literal="474,21 €",
        herramienta="hipoteca_cuadro",
        fragment_id="F1",
    )
    luz = Cifra(
        id="C2", especialista="luz", concepto="factura", valor=Decimal("57.79"), unidad="EUR", literal="57,79 €"
    )
    puerta = ResultadoPuerta(
        violaciones=(
            [Violacion(tipo="suma_no_combinada", literal="532,00 €", detalle="suma de C1 y C2", cifras=["C1", "C2"])]
            if violaciones
            else []
        ),
        atribuciones=[
            AtribucionCifra(literal="474,21 €", valor=Decimal("474.21"), cifra_id="C1", especialista="hipoteca")
        ],
    )
    return InformeCerebro(
        intencion=intencion,
        ruta="sintesis",
        informes=[
            ResumenInforme(
                encargo_id="e1",
                especialista="hipoteca",
                nombre="Hipoteca",
                estado="ok",
                modo="verified",
                cifras=[cuota],
            ),
            ResumenInforme(encargo_id="e2", especialista="luz", nombre="Luz", estado="ok", cifras=[luz]),
        ],
        puerta=puerta,
        recuerdos_usados=["r1"],
        conversacion_id="conv_0123456789",
    ).model_dump(mode="json")


@dataclass
class Entornito:
    db: str
    cases: SQLiteCaseStateStore
    feedback: FeedbackStore
    candidatos: revision.CandidatoStore


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Entornito]:
    db = str(tmp_path / "state.sqlite3")
    cases = SQLiteCaseStateStore(db)
    cases.save_case(
        CaseState(
            case_id="case_1",
            user_id="u1",
            jurisdiction="ES",
            tax_period="2026",
            facts=[
                _hecho("hipoteca_prestamo", json.dumps(PRESTAMO)),
                _hecho("datos_conyuge", json.dumps({"nombre": "Marta López"})),
            ],
        )
    )
    yield Entornito(db, cases, FeedbackStore(db), revision.candidatos_para(db))


def _turno(cases: SQLiteCaseStateStore, case_id: str = "case_1", *, pregunta: str = PREGUNTA, **extra) -> None:  # type: ignore[no-untyped-def]
    evento = {
        "event_type": "agent_answer",
        "input": pregunta,
        "mode": "partially_verified",
        "provider": "anthropic",
        "model": "claude-opus-5-5",
        "tool_trace": [
            {
                "tool": "hipoteca_cuadro",
                "agent": "especialista_hipoteca",
                "arguments": {"fecha": "2026-03-20"},
                "summary": "cuadro",
            },
        ],
        "citations": [{"fragment_id": "F1", "locator": "x"}],
        "claims": [],
        "cerebro": informe_cerebro(),
    }
    evento.update(extra)
    cases.append_audit_event(case_id, evento)


def _feedback(env: Entornito, **cambios) -> str:  # type: ignore[no-untyped-def]
    datos = {
        "case_id": "case_1",
        "pregunta": PREGUNTA,
        "respuesta": RESPUESTA,
        "valoracion": "negativa",
        "tipo": "suma_indebida",
        "comentario": "No se pueden sumar, me lo dijo Marta",
        "guardar_para_evaluacion": True,
        "version_consentimiento": CONSENTIMIENTO_VERSION,
        **cambios,
    }
    registro = capturar(
        FeedbackEntrada.model_validate(datos),
        env.feedback,
        case=env.cases.get_case("case_1"),
        cases=env.cases,
        user_id="u1",
        nombres_extra=["ana.perez"],  # the account's e-mail local part, as the API passes it
    )
    return registro.id


# --------------------------------------------------------------------------- #
# Capture
# --------------------------------------------------------------------------- #


def test_feedback_stores_the_brain_report_and_becomes_a_masked_candidate(env: Entornito) -> None:
    _turno(env.cases)
    feedback_id = _feedback(env)
    evento = next(e for e in env.cases.list_audit_events("case_1") if e["event_type"] == "evaluacion_valoracion")
    guardado = evento["caso"]
    # What the brain did: plan, specialists and their modes, figures, the gate, the ids (not the text) of memories.
    assert guardado["cerebro"]["intencion"]["tipo"] == "cruzada" and guardado["cerebro"]["recuerdos_usados"] == ["r1"]
    assert guardado["modos_especialistas"] == {"hipoteca": "verified", "luz": None}
    assert {
        "agente",
        "verificador",
        "cerebro",
        "cerebro:intencion",
        "especialista:hipoteca",
        "especialista:luz",
    } <= set(guardado["modelos"])
    assert guardado["variante"] == "base"

    (candidato,) = env.candidatos.todos()
    assert candidato.origen == "valoracion" and candidato.feedback_id == feedback_id and candidato.user_id == "u1"
    assert (
        candidato.tipo_fallo == "suma_indebida" and candidato.categoria_sugerida == "procedencia" and candidato.critica
    )
    c = candidato.contenido
    # Masked: the user's and the partner's names, the e-mail; the amounts are kept (they are the point).
    assert "Ana" not in c.pregunta and "[nombre]" in c.pregunta
    assert "ana@example.com" not in (c.respuesta or "") and "532,00 €" in (c.respuesta or "")
    assert "Marta" not in json.dumps(c.correccion, ensure_ascii=False)
    # The snapshot keeps only the facts the turn used, masked too (a fact's «nombre» is a person's name).
    assert c.instantanea["hechos"]["datos_conyuge"] == {"nombre": "[nombre]"}
    assert c.instantanea["hechos"]["hipoteca_prestamo"]["capital_inicial"] == "100000"
    assert c.cerebro is not None and "Ana" not in json.dumps(c.cerebro, ensure_ascii=False)
    assert c.cerebro["informes"][0]["cifras"][0]["valor"] == "474.21"
    assert {x.tipo for x in c.enmascarado} >= {"nombre", "correo"} and all("Ana" not in x.vista for x in c.enmascarado)
    assert candidato.importe_en_juego == Decimal("532.00")
    assert any("crítica" in m for m in candidato.motivos_prioridad)


def test_a_plain_vote_becomes_a_candidate_only_with_the_owners_standing_consent(env: Entornito) -> None:
    _turno(env.cases)
    _feedback(env, guardar_para_evaluacion=False, version_consentimiento=None)
    assert env.candidatos.todos() == []
    env.candidatos.dar_consentimiento("case_1")
    _feedback(env, guardar_para_evaluacion=False, version_consentimiento=None)
    votos = [c for c in env.candidatos.todos() if c.origen == "voto"]
    assert len(votos) == 1 and "[nombre]" in votos[0].contenido.pregunta
    # Withdrawing the consent deletes the pending candidates it produced.
    assert env.candidatos.retirar_consentimiento("case_1") >= 1
    assert [c for c in env.candidatos.todos() if c.origen != "valoracion"] == []


def test_weak_signals_become_low_priority_candidates_only_after_the_consent(env: Entornito) -> None:
    _turno(env.cases, pregunta="Turno de antes del consentimiento: ¿cuánto pago de luz?")
    otorgado = env.candidatos.dar_consentimiento("case_1").otorgado_el
    _turno(env.cases)  # the gate still unhappy with the final answer
    _turno(env.cases, pregunta="¿Cuándo caduca el pasaporte de Marta?", mode="abstained", cerebro=None)
    _turno(
        env.cases,
        pregunta="¿Me deduzco la hipoteca en la renta de 2025?",
        cerebro=None,
        claims=[{"text": "Te deduces el 15 %", "verdict": "no_respaldada"}],
    )
    _turno(env.cases, pregunta="¿Me deduzco la hipoteca en la renta de 2025 si la compré en 2011?", cerebro=None)
    nuevos = revision.detectar_en_caso(env.candidatos, env.cases, env.cases.get_case("case_1"))
    assert sorted(c.tipo_fallo for c in nuevos) == ["abstencion", "puerta_cifras", "repregunta", "verificador"]
    assert all(c.origen == "senal" and c.creado_el >= otorgado for c in nuevos)
    assert not any("antes del consentimiento" in c.contenido.pregunta for c in nuevos)
    assert all("Marta" not in c.contenido.pregunta for c in nuevos)
    repregunta = next(c for c in nuevos if c.tipo_fallo == "repregunta")
    assert repregunta.contenido.siguiente_pregunta and "2011" in repregunta.contenido.siguiente_pregunta
    # Idempotent: the same turns never produce the same candidate twice.
    assert revision.detectar_en_caso(env.candidatos, env.cases, env.cases.get_case("case_1")) == []
    # A weak signal weighs less than the user's own correction of the same kind of mistake.
    _feedback(env)
    valoracion = next(c for c in env.candidatos.todos() if c.origen == "valoracion")
    puerta = next(c for c in nuevos if c.tipo_fallo == "puerta_cifras")
    assert valoracion.prioridad > puerta.prioridad


def test_without_consent_nothing_is_detected(env: Entornito) -> None:
    _turno(env.cases)
    assert revision.detectar_en_caso(env.candidatos, env.cases, env.cases.get_case("case_1")) == []


# --------------------------------------------------------------------------- #
# Triage
# --------------------------------------------------------------------------- #


def _candidato(pregunta: str, tipo: str, *, case_id: str = "c", origen: str = "senal", respuesta: str | None = None):  # type: ignore[no-untyped-def]
    contenido = revision.ContenidoCandidato(pregunta=pregunta, respuesta=respuesta)
    return revision.nuevo_candidato(
        contenido, tipo=tipo, origen=origen, case_id=case_id, user_id=None, referencia_turno=pregunta + case_id  # type: ignore[arg-type]
    )


def test_duplicates_are_grouped_and_the_queue_is_ordered_by_priority() -> None:
    a = _candidato("¿Cuánto me sale la renta?", "repregunta", case_id="c1")
    b = _candidato("¿cuánto me sale  la RENTA", "repregunta", case_id="c2")  # same question, normalised
    distinto = _candidato("¿Cuánto me sale la renta?", "abstencion", case_id="c3")  # same question, another failure
    corregida = _candidato(
        "¿Cuánto pago de hipoteca?", "cifra_erronea", origen="valoracion", respuesta="Pagas 1.250,00 €."
    )
    grupos = revision.agrupar([a, b, distinto, corregida])
    assert [g.frecuencia for g in grupos if g.representante.tipo_fallo == "repregunta"] == [2]
    assert len(grupos) == 3
    # The user's correction about money comes first; frequency adds weight, and every term is explained.
    assert grupos[0].representante.id == corregida.id and corregida.importe_en_juego == Decimal("1250.00")
    assert any("€ en juego" in m for m in grupos[0].motivos_prioridad)
    repetida = next(g for g in grupos if g.frecuencia == 2)
    assert any("2 veces" in m for m in repetida.motivos_prioridad) and repetida.prioridad > a.prioridad
    # Critical categories first: a gate violation (procedencia, critical) beats an abstention (regresion… also
    # critical) only by its weight; a non-critical suggestion has no +100.
    modulo = _candidato("¿Cuánto me costará la luz?", "cifra_erronea", origen="valoracion")
    assert modulo.categoria_sugerida == "modulo" and not modulo.critica
    assert corregida.prioridad - modulo.prioridad >= 0


# --------------------------------------------------------------------------- #
# Acceptance and rejection
# --------------------------------------------------------------------------- #


def _aceptable(env: Entornito) -> revision.Candidato:
    _turno(env.cases)
    _feedback(env, tipo="cifra_erronea", cifra_correcta="474,21 €")
    return next(c for c in env.candidatos.todos() if c.origen == "valoracion")


def test_accepting_stores_a_private_case_whose_expectations_catch_the_mistake(env: Entornito) -> None:
    candidato = _aceptable(env)
    duplicado = revision.nuevo_candidato(
        candidato.contenido,
        tipo=candidato.tipo_fallo,
        origen="senal",
        case_id="case_1",
        user_id="u1",
        referencia_turno="otro",
    )
    env.candidatos.anadir(duplicado)
    edicion = EdicionCaso(
        categoria="procedencia",
        esperado=ComportamientoEsperado(
            especialistas_obligatorios=["hipoteca", "luz"],
            cifras_prohibidas=["532,00 €"],
            cifra_esperada=CifraEsperadaEdicion(
                herramienta="hipoteca_cuadro", argumentos={"fecha": "2026-03-20"}, especialista="hipoteca"
            ),
            puerta_sin_violaciones=True,
        ),
    )
    real = revision.aceptar(
        env.candidatos, candidato, edicion, revisor="propietario", cases=env.cases, nombres=["Ana", "Marta"]
    )
    caso = real.caso
    assert caso.requiere == ["cerebro"] and caso.origen.tipo == "feedback" and caso.origen.revisado_por == "propietario"
    assert caso.modulos == ["hipoteca", "luz"]
    cifra = next(e for e in caso.expectativas if isinstance(e, ExpCifra))
    # The expected figure is a calculator recipe located by the user's figure, never the figure itself.
    assert cifra.calculo.herramienta == "hipoteca_cuadro" and "474" not in cifra.calculo.patron
    assert s.cifras_esperadas(caso).cifras == {"cifra_esperada": "474,21 €"}
    assert any(isinstance(e, ExpCifraAusente) and e.valor == "532,00 €" for e in caso.expectativas)
    assert any(isinstance(e, ExpEspecialistas) for e in caso.expectativas)
    # Stored privately, linked to its case and user; the queue and the feedback reflect it; the user is told.
    guardado = privado.store_de(env.db).get(real.id)
    assert guardado is not None and guardado.case_id == "case_1" and guardado.user_id == "u1"
    assert guardado.guion_capturado is not None and "532,00 €" in guardado.model_dump_json()
    assert {c.estado for c in env.candidatos.del_grupo(candidato.grupo)} == {"aceptado"}
    assert env.feedback.get(candidato.feedback_id).estado == "promovido"  # type: ignore[arg-type, union-attr]
    ultimo = env.cases.list_audit_events("case_1")[-1]
    assert ultimo["event_type"] == "evaluacion_revision" and ultimo["caso_real"] == real.id
    # Never in the repository.
    assert not list(s.CASOS_DIR.rglob(f"{real.id}.json"))


def test_expectations_the_wrong_answer_passes_are_refused_unless_the_reviewer_insists(env: Entornito) -> None:
    candidato = _aceptable(env)
    flojas = EdicionCaso(categoria="regresion", esperado=ComportamientoEsperado(textos_obligatorios=["cuota"]))
    with pytest.raises(AceptacionRechazadaError, match="aprobaría"):
        revision.aceptar(env.candidatos, candidato, flojas, revisor="operador")
    real = revision.aceptar(
        env.candidatos, candidato, flojas.model_copy(update={"aunque_no_reproduzca": True}), revisor="operador"
    )
    assert any("aprobaría" in a for a in real.avisos)


@pytest.mark.parametrize(
    ("cambio", "mensaje"),
    [
        ({"pregunta": "Mi DNI es 12345678Z, ¿cuánto pago?"}, "datos personales"),
        ({"pregunta": "Marta quiere saber cuánto pago"}, "nombre"),
        ({"categoria": "inventada"}, "Categoría desconocida"),
        ({"esperado": {}}, "al menos un comportamiento"),
        (
            {
                "esperado": {
                    "cifra_esperada": {
                        "herramienta": "hipoteca_cuadro",
                        "argumentos": {"fecha": "2026-03-20"},
                        "referencia": "999,99 €",
                    }
                }
            },
            "solo puede salir de una calculadora",
        ),
    ],
)
def test_what_cannot_be_accepted(env: Entornito, cambio: dict, mensaje: str) -> None:  # type: ignore[type-arg]
    candidato = _aceptable(env)
    datos = {"categoria": "regresion", "esperado": {"cifras_prohibidas": ["532,00 €"]}, **cambio}
    with pytest.raises(AceptacionRechazadaError, match=mensaje):
        revision.aceptar(
            env.candidatos, candidato, EdicionCaso.model_validate(datos), revisor="operador", nombres=["Marta"]
        )
    assert privado.casos_vigentes(privado.ruta_por_defecto(env.db)) == []


def test_rejecting_needs_a_reason(env: Entornito) -> None:
    candidato = _aceptable(env)
    with pytest.raises(AceptacionRechazadaError, match="por qué"):
        revision.rechazar(env.candidatos, candidato, "   ", revisor="propietario")
    (rechazado,) = revision.rechazar(
        env.candidatos, candidato, "La cifra era correcta", revisor="propietario", cases=env.cases
    )
    assert rechazado.estado == "rechazado" and rechazado.motivo_rechazo == "La cifra era correcta"
    assert env.feedback.get(candidato.feedback_id).estado == "descartado"  # type: ignore[arg-type, union-attr]
    with pytest.raises(AceptacionRechazadaError, match="rechazado"):
        revision.rechazar(env.candidatos, candidato, "otra vez", revisor="propietario")


# --------------------------------------------------------------------------- #
# An accepted case joins the evaluation
# --------------------------------------------------------------------------- #


def test_an_accepted_case_runs_with_the_suite_and_keeps_catching_its_mistake(env: Entornito, tmp_path: Path) -> None:
    # A legacy turn (no brain): the advisor consulted the mortgage specialist and gave a wrong figure.
    env.cases.append_audit_event(
        "case_1",
        {
            "event_type": "agent_answer",
            "input": "¿Cuál es la cuota de mi hipoteca?",
            "mode": "verified",
            "tool_trace": [
                {"tool": "consultar_hipoteca", "agent": "asesor", "arguments": {"pregunta": "cuota"}},
                {"tool": "hipoteca_cuadro", "agent": "especialista_hipoteca", "arguments": {"fecha": "2026-03-20"}},
            ],
            "claims": [],
        },
    )
    capturar(
        FeedbackEntrada(
            case_id="case_1",
            pregunta="¿Cuál es la cuota de mi hipoteca?",
            respuesta="Tu cuota es de 500,00 €.",
            valoracion="negativa",
            tipo="cifra_erronea",
            cifra_correcta="474,21 €",
            guardar_para_evaluacion=True,
            version_consentimiento=CONSENTIMIENTO_VERSION,
        ),
        env.feedback,
        case=env.cases.get_case("case_1"),
        cases=env.cases,
    )
    candidato = next(c for c in env.candidatos.todos() if "cuota de mi hipoteca" in c.contenido.pregunta)
    edicion = EdicionCaso(
        categoria="regresion",
        esperado=ComportamientoEsperado(
            especialistas_obligatorios=["hipoteca"],
            cifra_esperada=CifraEsperadaEdicion(herramienta="hipoteca_cuadro", argumentos={"fecha": "2026-03-20"}),
            cifras_prohibidas=["500,00 €"],
        ),
    )
    real = revision.aceptar(env.candidatos, candidato, edicion, revisor="operador")
    assert real.caso.requiere == [] and real.caso.modulos == ["hipoteca"]
    reales = privado.casos_vigentes(privado.ruta_por_defecto(env.db))
    assert [c.id for c in reales] == [real.id]

    # Re-run against a variant (a scripted «model» in tests): the one that answers right passes, the one that
    # repeats the captured mistake fails.
    correcto = {
        "especialista:hipoteca": [
            {"herramientas": [{"nombre": "hipoteca_cuadro", "argumentos": {"fecha": "2026-03-20"}}]},
            {"texto": "Cuota {cifra:cifra_esperada} {fragmento:hipoteca_cuadro}."},
        ],
        "asesor": [
            {"herramientas": [{"nombre": "consultar_hipoteca", "argumentos": {"pregunta": "cuota"}}]},
            {"texto": "Tu cuota es de {cifra:cifra_esperada} {fragmento:hipoteca_cuadro}."},
        ],
    }

    def fabrica(variante, caso, cifras):  # type: ignore[no-untyped-def]
        guion = correcto if variante.id == "arreglada" else real.model_dump(mode="json")["guion_capturado"]
        return ProveedorReproduccion(
            CasoEvaluacion.model_validate(caso.model_dump(mode="json") | {"guion": guion}), cifras
        )

    entorno = Entorno(proveedor=fabrica, consumo=ConsumoStore(str(tmp_path / "c.sqlite3")), modo="modelo")
    arreglada = evaluar(
        reales,
        VARIANTE_BASE.model_copy(update={"id": "arreglada"}),
        entorno,
        config=s.cargar_config(),
        suite_huella="h",
        casos_reales=1,
    )
    assert (
        arreglada.casos[0].aprobado
        and arreglada.con_casos_reales == 1
        and any("privados" in a for a in arreglada.avisos)
    )
    igual = evaluar(reales, VARIANTE_BASE, entorno, config=s.cargar_config(), suite_huella="h")
    assert not igual.casos[0].aprobado and any("500,00 €" in f for f in igual.casos[0].fallos)

    # The standing negative control: replaying the captured run still fails (the case still bites).
    _informe, siguen_aprobando = reproducir_capturas(
        [(real.caso, real.guion_capturado)], config=s.cargar_config(), suite_huella="h"
    )
    assert siguen_aprobando == []


# --------------------------------------------------------------------------- #
# Privacy: fact deletion, case deletion, account export and erasure
# --------------------------------------------------------------------------- #


def test_deleting_a_fact_redacts_candidates_and_retires_accepted_cases(env: Entornito) -> None:
    candidato = _aceptable(env)
    assert candidato.contenido.instantanea["hechos"]
    real = revision.aceptar(
        env.candidatos,
        candidato,
        EdicionCaso(categoria="regresion", esperado=ComportamientoEsperado(cifras_prohibidas=["532,00 €"])),
        revisor="operador",
    )
    case = env.cases.get_case("case_1")
    assert case is not None
    env.cases.save_case(case.model_copy(update={"facts": [f for f in case.facts if f.name != "hipoteca_prestamo"]}))
    hechos = env.candidatos.get(candidato.id).contenido.instantanea["hechos"]  # type: ignore[union-attr]
    assert hechos["hipoteca_prestamo"] == ELIMINADO
    retirado = privado.store_de(env.db).get(real.id)
    assert retirado is not None and not retirado.vigente and "hipoteca_prestamo" in (retirado.motivo_baja or "")
    assert retirado.caso.expediente.hechos["hipoteca_prestamo"] == ELIMINADO  # type: ignore[union-attr]
    assert privado.casos_vigentes(privado.ruta_por_defecto(env.db)) == []


def test_deleting_the_case_erases_candidates_consent_and_accepted_cases(env: Entornito) -> None:
    candidato = _aceptable(env)
    env.candidatos.dar_consentimiento("case_1")
    real = revision.aceptar(
        env.candidatos,
        candidato,
        EdicionCaso(categoria="regresion", esperado=ComportamientoEsperado(cifras_prohibidas=["532,00 €"])),
        revisor="operador",
    )
    # Another user's duplicate accepted into the same real case: it goes back to the queue, not away.
    otro = revision.nuevo_candidato(
        candidato.contenido,
        tipo=candidato.tipo_fallo,
        origen="senal",
        case_id="case_2",
        user_id="u2",
        referencia_turno="x",
    )
    otro.estado, otro.caso_aceptado = "aceptado", real.id
    env.candidatos.anadir(otro)
    assert env.cases.delete_case("case_1")
    assert [c.id for c in env.candidatos.todos()] == [otro.id]
    assert env.candidatos.get(otro.id).estado == "pendiente"  # type: ignore[union-attr]
    assert env.candidatos.consentimiento("case_1") is None
    assert privado.store_de(env.db).get(real.id) is None


def test_export_and_erasure_of_a_user(env: Entornito) -> None:
    candidato = _aceptable(env)
    env.candidatos.dar_consentimiento("case_1")
    real = revision.aceptar(
        env.candidatos,
        candidato,
        EdicionCaso(categoria="regresion", esperado=ComportamientoEsperado(cifras_prohibidas=["532,00 €"])),
        revisor="operador",
    )
    exportado = revision.exportar_usuario(env.db, user_id="u1", case_ids=["case_1"])
    assert [c["case_id"] for c in exportado["aprendizaje_automatico"]] == ["case_1"]
    assert {c["id"] for c in exportado["candidatos"]} == {candidato.id}
    assert [c["id"] for c in exportado["casos_aceptados"]] == [real.id]
    assert revision.exportar_usuario(env.db, user_id="u2", case_ids=[])["casos_aceptados"] == []
    borrado = revision.borrar_de_usuario(env.db, user_id="u1", case_ids=["case_1"])
    assert borrado["candidatos"] >= 1 and borrado["casos_aceptados"] == 1
    assert env.candidatos.todos() == [] and privado.store_de(env.db).todos() == []


def test_the_versioned_suite_refuses_real_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for caso in s.cargar_casos():
        assert caso.origen.tipo not in s.ORIGENES_PRIVADOS, caso.id
    copia = tmp_path / "casos"
    (copia / "modulo").mkdir(parents=True)
    (copia / "suite.json").write_text((s.CASOS_DIR / "suite.json").read_text(encoding="utf-8"), encoding="utf-8")
    datos = json.loads((s.CASOS_DIR / "modulo" / "hipoteca-proxima-cuota.json").read_text(encoding="utf-8"))
    datos["origen"] = {"tipo": "feedback", "referencia": "fb-1"}
    (copia / "modulo" / "real.json").write_text(json.dumps(datos), encoding="utf-8")
    monkeypatch.setattr(s, "CASOS_DIR", copia)  # as if it were the repository's suite
    with pytest.raises(s.SuiteInvalidaError, match="no puede estar en el repositorio"):
        s.cargar_casos(copia)
