"""Regresiones de la auditoría de cálculos de dinero en los ámbitos."""

from __future__ import annotations

from decimal import Decimal

import pytest

from asistente.ambitos.numeros import importe


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("150.000", Decimal("150000")),
        ("150.000,50", Decimal("150000.50")),
        ("2,5", Decimal("2.5")),
        ("2.5", Decimal("2.5")),
        ("45,000", Decimal("45000")),
        ("1,234.50", Decimal("1234.50")),
        ("-3.000", Decimal("-3000")),
        ("12 €", Decimal("12")),
        ("0,123", Decimal("0.123")),
        (2.5, Decimal("2.5")),
        (7, Decimal("7")),
    ],
)
def test_parser_comun_de_importes(texto, esperado):
    assert importe(texto) == esperado


@pytest.mark.parametrize("texto", ["abc", "", None, True, "1.2.3", "1,2,3"])
def test_parser_rechaza_lo_que_no_es_numero(texto):
    assert importe(texto) is None


def test_coma_decimal_para_columnas_de_kwh():
    assert importe("1,234", coma_decimal=True) == Decimal("1.234")


def test_hipoteca_lee_150_mil_con_punto_de_miles():
    from asistente.ambitos.hipoteca.modelo import _decimal

    assert _decimal("150.000") == Decimal("150000")


# --- 2. Ejercicio fiscal sin parámetros -------------------------------------------


def _ejecucion(periodo: str | None):
    from asistente.ambitos.fiscal.herramientas import EjecucionFiscal
    from asistente.nucleo.decision.schemas import CaseState

    case = None
    if periodo is not None:
        case = CaseState(case_id="c", user_id="u", jurisdiction="ES", tax_period=periodo, facts=[])
    return EjecucionFiscal(ledger=None, case=case)  # type: ignore[arg-type]


def test_ejercicio_como_texto_se_respeta():
    assert _ejecucion("2025")._ejercicio("2026", [2025, 2026], "IRPF") == 2026


def test_ejercicio_ilegible_es_error():
    from asistente.ambitos.fiscal.profile import DatosInvalidosError

    with pytest.raises(DatosInvalidosError):
        _ejecucion(None)._ejercicio("el año pasado", [2026], "IRPF")


def test_ejercicio_del_caso_sin_parametros_no_se_cambia_en_silencio():
    from asistente.ambitos.fiscal.calculo.params import EjercicioNoDisponibleError

    with pytest.raises(EjercicioNoDisponibleError, match="2024"):
        _ejecucion("2024")._ejercicio(None, [2025, 2026], "IRPF")


def test_calculadora_con_ejercicio_del_caso_sin_parametros_da_error():
    from asistente.ambitos.fiscal.especificaciones import PATRIMONIO_TOOL

    resultado = _ejecucion("2025").manejadores()[PATRIMONIO_TOOL.name]({"comunidad": "madrid"})
    assert resultado.es_error
    assert "2025" in resultado.contenido


def test_calcular_irpf_con_ano_explicito_no_cargado_da_error():
    from asistente.ambitos.fiscal.especificaciones import CALCULATE_TOOL

    resultado = _ejecucion("2026").manejadores()[CALCULATE_TOOL.name]({"ejercicio": "2019", "datos": {}})
    assert resultado.es_error
    assert "2019" in resultado.contenido


def test_ayudas_sin_cifras_del_ano_avisan_o_fallan():
    from datetime import date

    from asistente.ambitos.ayudas.parametros import (
        EjercicioNoDisponibleError,
        aviso_ejercicio,
        ejercicio_para,
        ejercicios_disponibles,
    )

    primero, ultimo = min(ejercicios_disponibles()), max(ejercicios_disponibles())
    with pytest.raises(EjercicioNoDisponibleError):
        ejercicio_para(date(primero - 1, 6, 1))
    assert ejercicio_para(date(ultimo + 1, 6, 1)) == ultimo
    assert aviso_ejercicio(ultimo, date(ultimo + 1, 6, 1))
    assert aviso_ejercicio(ultimo, date(ultimo, 6, 1)) is None


# --- 3. Hipoteca: índice ausente en una revisión pasada ---------------------------


def _serie_hipoteca(valores):
    from pathlib import Path
    import sys

    sys.path.insert(0, str(Path(__file__).parent / "hipoteca"))
    from hipoteca_util import serie_sintetica

    return serie_sintetica(valores)


def _prestamo_variable():
    from asistente.ambitos.hipoteca.modelo import Prestamo

    return Prestamo.model_validate(
        dict(
            capital_inicial="150.000",
            fecha_firma="2024-01-10",
            plazo_meses=36,
            tipo="variable",
            tipo_inicial="2",
            meses_tipo_inicial=12,
            indice="euribor_12m",
            diferencial="1",
            indice_regla="meses_antes",
            indice_meses_antes=2,
        )
    )


def test_hueco_en_la_serie_pide_el_tipo_del_banco_en_vez_de_proyectar():
    from datetime import date

    from asistente.ambitos.hipoteca.calculo import dato_para_revision

    p = _prestamo_variable()
    assert p.capital_inicial == Decimal("150000")
    serie = _serie_hipoteca({"2024-10": "2.900", "2024-12": "2.800", "2025-10": "2.000"})
    # Revisión de enero de 2025 pide noviembre de 2024: falta, y hay datos posteriores.
    assert dato_para_revision(p, serie, date(2025, 1, 10), date(2026, 11, 13)) == (None, False)


def test_mes_posterior_al_ultimo_dato_se_proyecta():
    from datetime import date

    from asistente.ambitos.hipoteca.calculo import dato_para_revision

    p = _prestamo_variable()
    serie = _serie_hipoteca({"2024-10": "2.900", "2024-11": "3.000"})
    dato, proyeccion = dato_para_revision(p, serie, date(2026, 1, 10), date(2025, 12, 20))
    assert proyeccion and dato is not None and dato.mes == "2024-11"


@pytest.mark.parametrize(
    ("mes", "valor", "publicado"),
    [("2024-13", "1", "2025-02-02"), ("2024-10", "250", "2024-11-02"), ("2024-10", "2", "2024-10-15")],
)
def test_serie_de_indices_invalida_se_rechaza(mes, valor, publicado):
    from asistente.ambitos.hipoteca.indices import serie_desde_json

    raw = {
        "consultado_el": "2026-01-01",
        "series": {"euribor_12m": {mes: {"valor": valor, "literal": valor, "boe_id": "x", "publicado": publicado}}},
    }
    with pytest.raises(ValueError, match="Serie de índices"):
        serie_desde_json(raw)


# --- 4. Luz: PVPC en tramos parciales y CSV que se fusionan -----------------------


def _contrato_luz():
    from asistente.ambitos.luz.factura import Contrato

    return Contrato(Decimal("4.6"), Decimal("4.6"))


def _energia_pvpc(factura) -> Decimal:
    return next(linea.importe for linea in factura.lineas if linea.concepto.startswith("Energía consumida PVPC"))


def test_pvpc_estimado_cobra_solo_los_kwh_del_tramo():
    from datetime import date, timedelta

    from asistente.ambitos.luz.factura import calcular_factura
    from asistente.ambitos.luz.ofertas import Oferta

    desde, hasta = date(2026, 10, 10), date(2026, 10, 14)
    dias = {desde + timedelta(days=i): [Decimal(2), Decimal(3), Decimal(5)] for i in range(5)}
    f = calcular_factura(
        Oferta(nombre="PVPC", tipo="pvpc"),
        _contrato_luz(),
        desde,
        hasta,
        dias_kwh=dias,
        precio_pvpc_medio={"2026-10": Decimal("0.15")},
    )
    assert _energia_pvpc(f) == Decimal("7.50")  # 5 días × 10 kWh × 0,15 €/kWh


def test_pvpc_sin_estimacion_del_mes_es_error_no_cero():
    from datetime import date

    from asistente.ambitos.luz.factura import FacturaError, calcular_factura
    from asistente.ambitos.luz.ofertas import Oferta

    with pytest.raises(FacturaError, match="2026-11"):
        calcular_factura(
            Oferta(nombre="PVPC", tipo="pvpc"),
            _contrato_luz(),
            date(2026, 10, 30),
            date(2026, 11, 2),
            totales={"p1": Decimal(10), "p2": Decimal(10), "p3": Decimal(10)},
            precio_pvpc_medio={"2026-10": Decimal("0.15")},
        )


def _lecturas(dias, kwh):
    from datetime import date

    from asistente.ambitos.luz.consumo import Lectura

    return [Lectura(date(2026, 10, d), h, Decimal(kwh)) for d in dias for h in range(24)]


def test_segundo_csv_del_mismo_mes_se_suma_al_resumen():
    from asistente.ambitos.luz.consumo import compactar

    primero = compactar(_lecturas(range(1, 11), 1))
    juntos = compactar(_lecturas(range(11, 21), 1), previo=primero)
    assert juntos.meses["2026-10"].kwh == Decimal(20 * 24)
    assert sum(juntos.meses["2026-10"].perfil) == Decimal(20 * 24)


def test_csv_solapado_sustituye_los_dias_repetidos():
    from asistente.ambitos.luz.consumo import compactar

    primero = compactar(_lecturas(range(1, 11), 1))
    juntos = compactar(_lecturas(range(10, 21), 2), previo=primero)
    assert juntos.meses["2026-10"].kwh == Decimal(9 * 24 + 11 * 48)


# --- 5-7. IRPF: forales, avisos desde los datos, claves de línea, Escala ----------


def test_navarra_es_error_explicito_de_territorio_foral():
    from asistente.ambitos.fiscal.calculo.irpf import IRPFInput, calcular_irpf
    from asistente.ambitos.fiscal.calculo.params import TerritorioForalError

    with pytest.raises(TerritorioForalError, match="foral"):
        calcular_irpf(IRPFInput(ejercicio=2026, comunidad_autonoma="Gipuzkoa", rendimiento_trabajo_bruto=Decimal(1)))


def test_las_lineas_que_se_releen_llevan_clave_estable():
    from asistente.ambitos.fiscal.calculo.irpf import IRPFInput, calcular_irpf, importe_linea
    from asistente.ambitos.fiscal.calculo.lineas import ClaveLinea

    r = calcular_irpf(
        IRPFInput(
            ejercicio=2026,
            comunidad_autonoma="Madrid",
            rendimiento_trabajo_bruto=Decimal(16000),
            cotizaciones_seguridad_social=Decimal(1016),
            donativos=Decimal(250),
        )
    )
    assert importe_linea(r, ClaveLinea.RENDIMIENTO_NETO_TRABAJO) > 0
    assert importe_linea(r, ClaveLinea.REDUCCION_TRABAJO) < 0
    assert importe_linea(r, ClaveLinea.DEDUCCION_DONATIVOS) < 0
    # Cambiar el texto de una línea no cambia lo que se relee por clave.
    renombrada = r.model_copy(
        update={"lineas": [ln.model_copy(update={"concepto": ln.concepto.upper()}) for ln in r.lineas]}
    )
    assert importe_linea(renombrada, ClaveLinea.DEDUCCION_DONATIVOS) == importe_linea(r, ClaveLinea.DEDUCCION_DONATIVOS)


def test_aviso_de_donativos_lee_el_limite_de_los_datos():
    from asistente.ambitos.fiscal.calculo.irpf import IRPFInput, calcular_irpf
    from asistente.ambitos.fiscal.calculo.params import load_params

    limite = load_params(2026).donativos.limite_base_liquidable * 100
    r = calcular_irpf(IRPFInput(ejercicio=2026, rendimiento_trabajo_bruto=Decimal(20000), donativos=Decimal(15000)))
    assert any(f"superan el {limite:.0f} %" in a for a in r.avisos)


@pytest.mark.parametrize(
    "tramos",
    [
        [{"hasta": "100", "tipo": "0.1"}, {"hasta": "50", "tipo": "0.2"}, {"hasta": None, "tipo": "0.3"}],
        [{"hasta": None, "tipo": "0.1"}, {"hasta": None, "tipo": "0.2"}],
        [{"hasta": "100", "tipo": "1.5"}, {"hasta": None, "tipo": "0.2"}],
        [{"hasta": "100", "tipo": "-0.1"}],
    ],
)
def test_escala_incoherente_se_rechaza(tramos):
    from pydantic import ValidationError

    from asistente.ambitos.fiscal.calculo.params import Escala

    fuente = {"referencia": "x", "en_vigor_desde": "2026-01-01", "verificado": True}
    with pytest.raises(ValidationError):
        Escala.model_validate({"tramos": tramos, "fuente": fuente})


# --- 8. Suscripciones -------------------------------------------------------------


def _servicio(**cambios):
    from datetime import date

    from asistente.ambitos.suscripciones.modelos import ServicioGuardado

    datos = dict(
        id="srv-0123456789",
        etiqueta="Netflix",
        categoria="streaming_video",
        sector="general",
        periodicidad="mensual",
        importe=Decimal("12.99"),
        primera_fecha=date(2025, 1, 5),
        ultima_fecha=date(2025, 3, 5),
        proxima_fecha=date(2025, 4, 5),
        estado="activa",
        confianza=Decimal("0.9"),
        num_cargos=3,
    )
    datos.update(cambios)
    return ServicioGuardado.model_validate(datos)


def test_servicio_sin_cargos_en_el_extracto_nuevo_deja_de_sumar():
    from datetime import date

    from asistente.ambitos.suscripciones import almacen
    from asistente.ambitos.suscripciones.modelos import ServiciosGuardados

    anterior = ServiciosGuardados(
        actualizado_el=date(2025, 3, 10), extracto_hasta=date(2025, 3, 10), servicios=[_servicio()]
    )
    fusion = almacen.fusionar(anterior, [], date(2026, 1, 10), date(2026, 1, 5))
    assert fusion.servicios[0].estado == "inactiva"
    assert almacen.total(fusion.servicios) == 0


def test_total_no_suma_importes_en_otra_divisa():
    from asistente.ambitos.suscripciones import almacen

    eur = _servicio()
    usd = _servicio(id="srv-abcdefabcd", divisa="USD", importe=Decimal("20"))
    assert almacen.total([eur, usd]) == eur.coste_anual


def test_deteccion_separa_las_divisas():
    from datetime import date

    from asistente.ambitos.suscripciones.deteccion import analizar
    from asistente.ambitos.suscripciones.modelos import Extracto, Movimiento

    movs = []
    for mes in range(1, 7):
        movs.append(Movimiento(date(2025, mes, 5), Decimal("-12.99"), "NETFLIX.COM"))
        movs.append(Movimiento(date(2025, mes, 7), Decimal("-15.49"), "NETFLIX.COM", divisa="USD"))
    analisis = analizar(Extracto(formato="csv", movimientos=tuple(movs)), usar_etiquetador=False)
    divisas = {s.guardado.divisa: s.guardado.importe for s in analisis.servicios}
    assert divisas == {"EUR": Decimal("12.99"), "USD": Decimal("15.49")}


# --- 9. Redondeo comercial --------------------------------------------------------


def test_coste_anual_redondea_medio_centimo_hacia_arriba():
    servicio = _servicio(periodicidad="anual", importe=Decimal("10.005"))
    assert servicio.coste_anual == Decimal("10.01")  # ROUND_HALF_EVEN daría 10.00


def test_deduccion_hipoteca_redondea_medio_centimo_hacia_arriba():
    from asistente.ambitos.hipoteca.deduccion import base_deducible, cargar_fundamento

    clave = cargar_fundamento().incluidos()[0].clave
    assert base_deducible({clave: Decimal("100.005")}) == Decimal("100.01")


# --- 10. Fechas -------------------------------------------------------------------


def test_renovacion_mensual_desde_fin_de_mes_no_deriva():
    from datetime import date

    from asistente.ambitos.caducidades.calculo import _proxima

    fecha, n = _proxima(date(2025, 1, 31), 1, date(2025, 3, 30))
    assert (fecha, n) == (date(2025, 3, 31), 2)


def test_antes_del_15_es_hasta_el_14():
    from datetime import date

    from asistente.ambitos.ayudas.convocatorias import Documento, extraer_plazos

    def fin(frase: str) -> list[date]:
        doc = Documento(parrafos=[f"El plazo de presentación de solicitudes {frase} de marzo de 2026."])
        return [p.fin for p in extraer_plazos(doc, date(2026, 2, 1))]

    assert fin("termina antes del 15") == [date(2026, 3, 14)]
    assert fin("finaliza el 15") == [date(2026, 3, 15)]
