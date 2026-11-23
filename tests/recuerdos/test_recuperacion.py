"""Retrieval quality, measured: an offline benchmark over a synthetic household memory (recall@k, MRR, false
memories on unrelated questions), plus the unit behaviour of the ranking (calibration, references, MMR)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from asistente.nucleo.recuerdos import recuperacion as rec
from asistente.nucleo.recuerdos.almacen import Fuente, Recuerdo, Titular
from asistente.nucleo.recuerdos.servicio import ServicioMemoria
from .conftest import ANA, BEA, Reloj

HOY = date(2026, 11, 12)

# (id, tipo, clave, texto, cita, days ago)
MEMORIA: list[tuple[str, str, str, str, str, int]] = [
    (
        "r_hipoteca_banco",
        "dato",
        "hipoteca.banco",
        "Tiene una hipoteca a tipo variable con ING",
        "tengo una hipoteca variable con ING",
        200,
    ),
    (
        "r_hipoteca_cuota",
        "dato",
        "hipoteca.cuota",
        "Paga unos 850 € al mes de hipoteca",
        "pago unos 850 euros al mes de hipoteca",
        190,
    ),
    ("r_hipoteca_fin", "dato", "hipoteca.plazo", "Le quedan 18 años de hipoteca", "me quedan 18 años de hipoteca", 180),
    (
        "r_vivienda_ciudad",
        "dato",
        "vivienda.ciudad",
        "Vive en Zaragoza, en un piso en propiedad",
        "vivo en Zaragoza en un piso que es mío",
        300,
    ),
    (
        "r_comunidad",
        "dato",
        "vivienda.comunidad",
        "Paga 90 € al mes de comunidad de vecinos",
        "pago 90 euros de comunidad",
        150,
    ),
    ("r_hijos", "dato", "familia.hijos", "Tiene dos hijas de 4 y 7 años", "tengo dos niñas de 4 y 7 años", 320),
    (
        "r_pareja",
        "dato",
        "familia.pareja",
        "Está casado y su mujer trabaja como enfermera",
        "mi mujer es enfermera",
        310,
    ),
    (
        "r_colegio",
        "dato",
        "hijos.colegio",
        "Sus hijas van a un colegio concertado",
        "las niñas van a un colegio concertado",
        100,
    ),
    (
        "r_trabajo",
        "dato",
        "trabajo.empleo",
        "Trabaja como ingeniero en una empresa de software",
        "soy ingeniero en una empresa de software",
        280,
    ),
    ("r_nomina", "dato", "trabajo.nomina", "Cobra unos 2.400 € netos al mes", "cobro 2.400 netos al mes", 270),
    (
        "r_teletrabajo",
        "contexto",
        "trabajo.teletrabajo",
        "Teletrabaja tres días a la semana desde casa",
        "teletrabajo tres días por semana desde casa",
        60,
    ),
    ("r_coche", "dato", "coche.modelo", "Tiene un coche diésel de 2012", "mi coche es un diésel de 2012", 250),
    (
        "r_coche_cambio",
        "contexto",
        "coche.cambio",
        "Está pensando en comprar un coche eléctrico este año",
        "estoy pensando en comprarme un coche eléctrico este año",
        40,
    ),
    (
        "r_seguro_coche",
        "dato",
        "seguro.coche",
        "El seguro del coche es a todo riesgo con Mapfre",
        "tengo el coche a todo riesgo con Mapfre",
        240,
    ),
    (
        "r_seguro_hogar",
        "dato",
        "seguro.hogar",
        "Tiene el seguro de hogar con la aseguradora del banco",
        "el seguro de casa lo tengo con el banco",
        230,
    ),
    (
        "r_luz",
        "dato",
        "luz.comercializadora",
        "Tiene la luz con Iberdrola en una tarifa de precio fijo",
        "la luz la tengo con Iberdrola a precio fijo",
        120,
    ),
    (
        "r_potencia",
        "dato",
        "luz.potencia",
        "Tiene contratados 4,6 kW de potencia eléctrica",
        "tengo 4,6 kW de potencia contratados",
        118,
    ),
    (
        "r_gas",
        "dato",
        "gas.calefaccion",
        "La calefacción de su casa es de gas natural",
        "la calefacción es de gas natural",
        200,
    ),
    (
        "r_movil",
        "dato",
        "telefonia.operador",
        "Tiene fibra y móvil con Movistar por 75 € al mes",
        "pago 75 euros de fibra y móvil con Movistar",
        90,
    ),
    (
        "r_suscripciones",
        "dato",
        "suscripciones.plataformas",
        "Está suscrito a Netflix, Spotify y Disney+",
        "tengo Netflix, Spotify y Disney+",
        80,
    ),
    (
        "r_gimnasio",
        "contexto",
        "suscripciones.gimnasio",
        "Quiere darse de baja del gimnasio porque no va",
        "quiero darme de baja del gimnasio, no voy nunca",
        30,
    ),
    (
        "r_ahorro",
        "contexto",
        "ahorro.objetivo",
        "Quiere ahorrar para comprar un apartamento en la playa",
        "quiero ahorrar para comprar algo en la playa",
        70,
    ),
    (
        "r_fondo",
        "dato",
        "ahorro.fondo",
        "Tiene un fondo indexado en MyInvestor",
        "tengo un fondo indexado en MyInvestor",
        160,
    ),
    (
        "r_pension",
        "dato",
        "pension.plan",
        "Aporta al plan de pensiones de su empresa",
        "aporto al plan de pensiones de la empresa",
        170,
    ),
    (
        "r_renta_conjunta",
        "dato",
        "renta.modalidad",
        "Hace la declaración de la renta conjunta con su mujer",
        "hacemos la declaración de la renta conjunta",
        210,
    ),
    (
        "r_renta_vivienda",
        "dato",
        "renta.deduccion_vivienda",
        "Compró el piso en 2011 y aplica la deducción por vivienda habitual",
        "compré el piso en 2011 y me aplico la deducción por vivienda",
        205,
    ),
    (
        "r_garaje",
        "dato",
        "ingresos.alquiler_garaje",
        "Alquila una plaza de garaje por 60 € al mes",
        "alquilo una plaza de garaje por 60 euros",
        140,
    ),
    (
        "r_perro",
        "dato",
        "mascotas.perro",
        "Tiene un perro, un labrador de 5 años",
        "tenemos un labrador de 5 años",
        110,
    ),
    (
        "r_estilo",
        "preferencia",
        "preferencia.estilo",
        "Prefiere respuestas breves y con ejemplos",
        "prefiero respuestas breves y con ejemplos",
        330,
    ),
    ("r_tuteo", "preferencia", "preferencia.trato", "Prefiere que le hables de tú", "háblame de tú", 330),
    (
        "r_avisos",
        "preferencia",
        "preferencia.avisos",
        "Prefiere recibir los avisos por la mañana y nunca en fin de semana",
        "avísame por las mañanas, nunca el fin de semana",
        100,
    ),
    (
        "r_riesgo",
        "preferencia",
        "preferencia.inversion",
        "No quiere productos de inversión con riesgo",
        "no quiero inversiones con riesgo",
        160,
    ),
    (
        "r_banco_online",
        "preferencia",
        "preferencia.banco",
        "Prefiere bancos online sin comisiones",
        "prefiero bancos online sin comisiones",
        150,
    ),
    (
        "r_idioma",
        "preferencia",
        "preferencia.idioma",
        "Prefiere que le respondas en castellano aunque a veces escriba en catalán",
        "respóndeme en castellano",
        90,
    ),
    (
        "r_viaje",
        "contexto",
        "viajes.verano",
        "Este verano viaja a Portugal con la familia",
        "este verano nos vamos a Portugal",
        150,
    ),
    (
        "r_reforma",
        "contexto",
        "vivienda.reforma",
        "Está pensando en reformar la cocina",
        "estoy pensando en reformar la cocina",
        20,
    ),
    (
        "r_madre",
        "contexto",
        "familia.madre",
        "Su madre vive con ellos desde este año",
        "mi madre se ha venido a vivir con nosotros",
        50,
    ),
    (
        "r_agua",
        "dato",
        "agua.factura",
        "La factura del agua le llega cada dos meses",
        "el agua me llega cada dos meses",
        130,
    ),
    ("r_ibi", "dato", "impuestos.ibi", "Paga el IBI de la vivienda en dos plazos", "el IBI lo pago en dos plazos", 125),
    (
        "r_bici",
        "contexto",
        "transporte.bici",
        "Va al trabajo en bicicleta cuando no teletrabaja",
        "voy al curro en bici",
        45,
    ),
]

# (conversation id, title, summary, quote, days ago)
CONVERSACIONES: list[tuple[str, str, str, str, int]] = [
    (
        "c-hipoteca",
        "Revisión de la hipoteca variable",
        "Preguntó si le conviene pasar la hipoteca a tipo fijo; quedamos en comparar ofertas de otros bancos.",
        "¿me conviene pasar la hipoteca a fijo?",
        35,
    ),
    (
        "c-luz",
        "Cambio de tarifa de la luz",
        "Comparamos tarifas de la luz con discriminación horaria frente a precio fijo.",
        "¿qué tarifa de luz me conviene?",
        25,
    ),
    (
        "c-renta",
        "Declaración de la renta conjunta",
        "Revisamos si le compensa hacer la declaración conjunta o individual.",
        "¿hago la renta conjunta?",
        160,
    ),
    (
        "c-coche",
        "Compra de coche eléctrico",
        "Hablamos de las ayudas del plan MOVES para comprar un coche eléctrico.",
        "¿qué ayudas hay para el coche eléctrico?",
        38,
    ),
    (
        "c-gimnasio",
        "Baja del gimnasio",
        "Preparamos la carta para darse de baja del gimnasio.",
        "ayúdame a darme de baja del gimnasio",
        28,
    ),
    (
        "c-seguro",
        "Seguro del hogar",
        "Revisó las coberturas del seguro de hogar del banco.",
        "¿qué cubre mi seguro de casa?",
        60,
    ),
    (
        "c-viaje",
        "Viaje a Portugal",
        "Planificamos el presupuesto del viaje de verano a Portugal.",
        "¿cuánto nos costará el viaje a Portugal?",
        140,
    ),
    (
        "c-cocina",
        "Reforma de la cocina",
        "Hablamos de cómo financiar la reforma de la cocina.",
        "¿cómo pago la reforma de la cocina?",
        10,
    ),
]

# (query, the relevant ids: each must be in the top 5)
CONSULTAS: list[tuple[str, set[str]]] = [
    ("¿Me conviene pasar la hipoteca a tipo fijo?", {"r_hipoteca_banco", "conv-c-hipoteca"}),
    ("¿Cuánto pago de hipoteca al mes?", {"r_hipoteca_cuota"}),
    ("¿Cuántos años me quedan para terminar el préstamo hipotecario?", {"r_hipoteca_fin"}),
    ("¿En qué ciudad vivo?", {"r_vivienda_ciudad"}),
    ("¿Cuánto pago de comunidad de vecinos?", {"r_comunidad"}),
    ("¿Qué edad tienen mis hijas?", {"r_hijos"}),
    ("¿A qué se dedica mi mujer?", {"r_pareja"}),
    ("¿Puedo deducirme el colegio de las niñas?", {"r_colegio"}),
    ("¿De qué trabajo yo?", {"r_trabajo"}),
    ("¿Cuánto cobro de nómina?", {"r_nomina"}),
    ("¿Puedo deducir gastos por teletrabajar desde casa?", {"r_teletrabajo"}),
    ("¿Me conviene cambiar mi coche diésel?", {"r_coche", "r_coche_cambio"}),
    ("¿Qué ayudas hay para comprar un coche eléctrico?", {"r_coche_cambio", "conv-c-coche"}),
    ("¿Con quién tengo asegurado el coche?", {"r_seguro_coche"}),
    ("Quiero revisar la póliza de la casa", {"r_seguro_hogar", "conv-c-seguro"}),
    ("¿Me interesa cambiar de compañía eléctrica?", {"r_luz", "conv-c-luz"}),
    ("¿Cuánta potencia tengo contratada?", {"r_potencia"}),
    ("¿Cómo reduzco el gasto de calefacción este invierno?", {"r_gas"}),
    ("¿Pago mucho por la fibra y el móvil?", {"r_movil"}),
    ("¿Qué suscripciones tengo?", {"r_suscripciones", "r_gimnasio"}),
    ("Ayúdame con la baja del gimnasio", {"r_gimnasio", "conv-c-gimnasio"}),
    ("¿Cómo ahorro para el apartamento de la playa?", {"r_ahorro"}),
    ("¿Cómo va mi fondo indexado?", {"r_fondo"}),
    ("¿Cuánto debería aportar al plan de pensiones?", {"r_pension"}),
    ("¿Hago la declaración conjunta o por separado?", {"r_renta_conjunta", "conv-c-renta"}),
    ("¿Puedo seguir con la deducción por vivienda habitual?", {"r_renta_vivienda"}),
    ("¿Cómo declaro lo que gano alquilando la plaza de garaje?", {"r_garaje"}),
    ("¿Cuánto cuesta un seguro para mi perro?", {"r_perro"}),
    ("Explícamelo de forma breve", {"r_estilo"}),
    ("¿A qué hora me mandas los avisos?", {"r_avisos"}),
    ("¿En qué puedo invertir mis ahorros?", {"r_riesgo", "r_fondo"}),
    ("Busco un banco nuevo sin comisiones", {"r_banco_online"}),
    ("¿Cuánto nos gastaremos en el viaje de verano?", {"r_viaje", "conv-c-viaje"}),
    ("¿Cómo financio la obra de la cocina?", {"r_reforma", "conv-c-cocina"}),
    ("¿Puedo desgravar algo por tener a mi madre viviendo en casa?", {"r_madre"}),
    ("¿Cada cuánto me llega la factura del agua?", {"r_agua"}),
    ("¿Cuándo tengo que pagar el IBI?", {"r_ibi"}),
    ("¿Qué te dije de la hipoteca?", {"r_hipoteca_banco", "r_hipoteca_cuota", "r_hipoteca_fin", "conv-c-hipoteca"}),
    ("¿Te acuerdas de lo que hablamos del gimnasio?", {"r_gimnasio", "conv-c-gimnasio"}),
    ("Respóndeme en castellano, por favor", {"r_idioma"}),
    ("¿Me compensa ir en bici al trabajo?", {"r_bici"}),
    ("Háblame de tú", {"r_tuteo"}),
    ("Mi labrador está enfermo, ¿el veterinario desgrava?", {"r_perro"}),
    ("¿Cuánto me cuesta la luz al mes con Iberdrola?", {"r_luz"}),
]

NEGATIVAS = [
    "¿Cuál es la capital de Australia?",
    "¿Qué tiempo hará mañana?",
    "Cuéntame un chiste",
    "¿Cuántos días tiene febrero?",
    "Traduce hello al francés",
    "¿Quién ganó el mundial de 2010?",
    "Hola, ¿qué tal estás?",
    "¿Cuál es la raíz cuadrada de 144?",
]


@pytest.fixture
def benchmark(memoria: ServicioMemoria, reloj: Reloj) -> ServicioMemoria:
    reloj.momento = datetime(2026, 11, 12, 12, tzinfo=UTC)
    store = memoria.store
    for id_, tipo, clave, texto, cita, dias in MEMORIA:
        fuente = Fuente(conversacion_id=f"conv-{id_}", fecha=HOY - timedelta(days=dias), cita=cita, n=1)
        momento = reloj.momento - timedelta(days=dias)
        store.guardar(
            ANA,
            Recuerdo(
                id=id_,
                tipo=tipo,
                clave=clave,
                texto=texto,
                origen=fuente,
                fuentes=[fuente],
                creado=momento,
                actualizado=momento,  # type: ignore[arg-type]
            ),
        )
    for conversacion, titulo, resumen, cita, dias in CONVERSACIONES:
        momento = reloj.momento - timedelta(days=dias)
        store.registrar_turno(ANA, conversacion, fecha=momento, pregunta=cita, respuesta="…")
        store.guardar_resumen(
            ANA, conversacion, titulo=titulo, resumen=resumen, cita=cita, cubiertos=2, momento=momento
        )
    # Another household with the same topics: it must never show up.
    for id_, tipo, clave, texto, cita, dias in MEMORIA[:12]:
        fuente = Fuente(conversacion_id="conv-bea", fecha=HOY - timedelta(days=dias), cita=cita, n=1)
        store.guardar(
            BEA,
            Recuerdo(
                id=f"bea_{id_}",
                tipo=tipo,
                clave=clave,
                texto=texto,
                origen=fuente,
                fuentes=[fuente],
                creado=reloj.momento,
                actualizado=reloj.momento,  # type: ignore[arg-type]
            ),
        )
    return memoria


def _ids(memoria: ServicioMemoria, consulta: str, limite: int = 8) -> list[str]:
    return memoria.recuperar(case_id=ANA.case_id, user_id=ANA.user_id, consulta=consulta, limite=limite).ids()


def test_the_benchmark_has_enough_queries() -> None:
    assert len(CONSULTAS) >= 40 and len(NEGATIVAS) >= 5
    ids = {m[0] for m in MEMORIA} | {f"conv-{c[0]}" for c in CONVERSACIONES}
    assert all(relevantes <= ids for _, relevantes in CONSULTAS)


def test_retrieval_quality_on_the_benchmark(benchmark: ServicioMemoria) -> None:
    recall5, recall1, mrr, fallos = [], [], [], []
    for consulta, relevantes in CONSULTAS:
        ids = _ids(benchmark, consulta)
        assert not any(i.startswith("bea_") for i in ids), consulta  # isolation, under load
        recall5.append(rec.recall_en(ids, relevantes, 5))
        recall1.append(1.0 if ids[:1] and ids[0] in relevantes else 0.0)
        mrr.append(rec.rango_reciproco(ids, relevantes))
        if recall5[-1] < 1:
            fallos.append((consulta, sorted(relevantes - set(ids[:5])), ids[:5]))
    medias = {
        "recall@5": sum(recall5) / len(recall5),
        "acierto@1": sum(recall1) / len(recall1),
        "MRR": sum(mrr) / len(mrr),
    }
    print(f"\nMemory retrieval benchmark ({len(CONSULTAS)} queries): {medias}")
    for fallo in fallos:
        print("  miss:", fallo)
    # Regression thresholds (measured: see the printed line; lower them only with a reason).
    # Measured on 2026-11-18: recall@5 0.989, acierto@1 0.932, MRR 0.966.
    assert medias["recall@5"] >= 0.97, medias
    assert medias["MRR"] >= 0.92, medias
    assert medias["acierto@1"] >= 0.88, medias


def test_unrelated_questions_bring_no_false_memories(benchmark: ServicioMemoria) -> None:
    con_algo = [q for q in NEGATIVAS if _ids(benchmark, q)]
    print(f"\nFalse memories: {len(con_algo)}/{len(NEGATIVAS)} unrelated questions retrieved something: {con_algo}")
    # Measured: 0 of 8.
    assert len(con_algo) / len(NEGATIVAS) <= 0.125


def test_what_do_you_know_about_me_returns_facts_and_preferences(benchmark: ServicioMemoria) -> None:
    bloque = benchmark.recuperar(case_id=ANA.case_id, user_id=ANA.user_id, consulta="¿Qué sabes de mí?", limite=8)
    assert len(bloque.recuerdos) == 8 and {r.tipo for r in bloque.recuerdos} <= {"dato", "preferencia"}


def test_last_time_brings_the_latest_conversation(benchmark: ServicioMemoria) -> None:
    ids = _ids(benchmark, "Seguimos con lo de la otra vez")
    assert ids[:1] == ["conv-c-cocina"]


def test_the_relevance_is_calibrated_and_explained(benchmark: ServicioMemoria) -> None:
    bloque = benchmark.recuperar(case_id=ANA.case_id, user_id=ANA.user_id, consulta="¿Cuánto pago de hipoteca al mes?")
    relevancias = [r.relevancia for r in bloque.recuerdos]
    assert relevancias and all(0 < r <= 1 for r in relevancias)
    assert bloque.recuerdos[0].id == "r_hipoteca_cuota" and relevancias[0] == max(relevancias) >= 0.5
    contexto = bloque.como_contexto()
    assert "<memoria_del_usuario>" in contexto and "[r_hipoteca_cuota] (dato, " in contexto


# --------------------------------------------------------------------------- unit behaviour


def _doc(id_: str, texto: str, tipo: str = "dato", dias: int = 0, **kw) -> rec.Documento:
    return rec.Documento(id=id_, tipo=tipo, terminos=rec.analizar(texto), fecha=HOY - timedelta(days=dias), **kw)  # type: ignore[arg-type]


def test_spanish_normalisation() -> None:
    assert rec.analizar("Las HIPOTECAS de mis hijas") == ["hipotec", "hij"]
    assert rec.raiz("luces") == rec.raiz("luz") == "luz"
    assert rec.terminos_de_consulta("¿Cuánto cuesta el piso?")["viviend"] == rec.PESO_SINONIMO


def test_types_and_recency_are_priors() -> None:
    docs = [
        _doc("viejo", "hipoteca con ING", dias=900),
        _doc("nuevo", "hipoteca con ING", dias=1),
        _doc("resumen", "hipoteca con ING", tipo="resumen", dias=1),
    ]
    puntuados = {p.documento.id: p.relevancia for p in rec.puntuar(docs, "hipoteca ING", hoy=HOY)}
    assert puntuados["nuevo"] > puntuados["viejo"] >= 0.6 * puntuados["nuevo"]
    assert puntuados["nuevo"] > puntuados["resumen"]


def test_mmr_keeps_near_duplicates_from_crowding_the_rest() -> None:
    docs = [
        _doc("a", "hipoteca variable con ING"),
        _doc("b", "hipoteca variable en ING"),
        _doc("c", "hipoteca variable con Kutxabank"),
    ]
    puntuados = rec.puntuar(docs, "hipoteca variable", hoy=HOY)
    assert len({p.relevancia for p in puntuados}) == 1  # equally relevant…
    elegidos = [p.documento.id for p in rec.seleccionar(puntuados, limite=2)]
    assert "c" in elegidos and len({"a", "b"} & set(elegidos)) == 1  # …so the near-duplicate gives way


def test_semantic_scores_blend_in_when_vectors_exist() -> None:
    docs = [
        _doc("lexico", "seguro del coche"),
        rec.Documento(id="semantico", tipo="dato", terminos=["mapfr"], fecha=HOY, vector=[1.0, 0.0]),
    ]
    sin = {p.documento.id: p.relevancia for p in rec.puntuar(docs, "seguro del coche", hoy=HOY)}
    con = {
        p.documento.id: p.relevancia for p in rec.puntuar(docs, "seguro del coche", hoy=HOY, vector_consulta=[1.0, 0.0])
    }
    assert sin["semantico"] == 0 and con["semantico"] > rec.UMBRAL


def test_vectors_round_trip() -> None:
    assert rec.vector_de_bytes(rec.vector_a_bytes([0.5, -1.0])) == [0.5, -1.0]
    assert rec.vector_de_bytes(None) is None and rec.vector_de_bytes(b"abc") is None


def test_owner_isolation_holds_in_the_service(benchmark: ServicioMemoria) -> None:
    bloque = benchmark.recuperar(case_id=BEA.case_id, user_id=BEA.user_id, consulta="¿Qué te dije de la hipoteca?")
    assert bloque.ids() and all(i.startswith("bea_") for i in bloque.ids())
    nadie = benchmark.recuperar(case_id=ANA.case_id, user_id=BEA.user_id, consulta="hipoteca")
    assert nadie.recuerdos == [] and Titular(ANA.case_id, BEA.user_id) != ANA
