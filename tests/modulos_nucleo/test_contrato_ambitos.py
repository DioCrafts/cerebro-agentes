"""Every domain in ``asistente/ambitos`` meets the common contract and passes its OWN evaluation.

Walks the real registry: a new domain (or a regression in one) that breaks a contract rule, declares no
capabilities, has no evaluation cases (with hard and abstention ones), fails its evaluation, or asks another domain
for a datum that domain does not offer, fails here. Offline: no REE token, no BOE fetch, an empty aid store.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from asistente.nucleo.evaluacion.modulos import RAIZ, cargar_casos, evaluar_modulo
from asistente.nucleo.modulos.contrato import ModuloBase
from asistente.nucleo.modulos.datos import DatoNoDeclaradoError, pedir_dato, problemas_de_datos
from asistente.nucleo.modulos.ganchos import Declaracion, declaracion_de, es_v2
from asistente.nucleo.modulos.registro import PAQUETE, construir_registro, descubrir, problemas_de

AMBITOS = ("ayudas", "caducidades", "fiscal", "hipoteca", "luz", "suscripciones")


@pytest.fixture(autouse=True)
def _sin_red(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from asistente.ambitos.luz import esios, herramientas

    monkeypatch.delenv(esios.TOKEN_ENV, raising=False)
    monkeypatch.setitem(herramientas._FETCH, "fetch", None)
    monkeypatch.setenv("AYUDAS_DATA_DIR", str(tmp_path / "ayudas"))
    for var in ("AYUDAS_BOE_AUTOMATICO", "AYUDAS_BDNS", "AYUDAS_CLASIFICADOR_LLM"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(scope="module")
def ambitos() -> list[Any]:
    candidatos, errores = descubrir(PAQUETE)
    assert not errores, errores
    registro = construir_registro(candidatos)
    assert not registro.errores, registro.errores
    return list(registro)


def test_estan_todos_los_ambitos(ambitos: list[Any]) -> None:
    assert {m.clave for m in ambitos} >= set(AMBITOS)


def test_cada_ambito_cumple_el_contrato_completo(ambitos: list[Any]) -> None:
    fallos: dict[str, list[str]] = {}
    for modulo in ambitos:
        problemas = list(problemas_de(modulo, paquete=modulo.clave))
        if not es_v2(modulo):
            problemas.append("no declara ganchos v2")
        declaracion = declaracion_de(modulo)
        if not declaracion.capacidades:
            problemas.append("no declara capacidades")
        if not modulo.herramientas:
            problemas.append("no tiene herramientas")
        if not modulo.fuentes:
            problemas.append("no declara fuentes")
        if declaracion.evaluacion is None:
            problemas.append("no declara casos de evaluación")
        elif not (RAIZ / declaracion.evaluacion).is_file():
            problemas.append(f"no existe {declaracion.evaluacion}")
        else:
            casos = cargar_casos(declaracion.evaluacion)
            tipos = {c.tipo for c in casos}
            if len(casos) < 6:
                problemas.append(f"solo {len(casos)} casos de evaluación (mínimo 6)")
            for tipo in ("dificil", "abstencion"):
                if tipo not in tipos:
                    problemas.append(f"sin casos de tipo {tipo}")
            if len({c.id for c in casos}) != len(casos):
                problemas.append("ids de casos repetidos")
        if problemas:
            fallos[modulo.clave] = problemas
    assert not fallos, fallos


@pytest.mark.parametrize("clave", AMBITOS)
def test_cada_ambito_supera_su_evaluacion(ambitos: list[Any], clave: str) -> None:
    modulo = next(m for m in ambitos if m.clave == clave)
    informe = evaluar_modulo(modulo)
    detalle = {r.caso.id: r.fallos for r in informe.resultados if not r.supera}
    assert informe.error is None, informe.error
    assert informe.aprobado, f"{clave}: {informe.puntuacion:.0%} < {informe.umbral:.0%}; {detalle}"


def test_lo_que_se_pide_lo_ofrece_su_duenio(ambitos: list[Any]) -> None:
    assert problemas_de_datos(ambitos) == []


def test_pedir_dato_solo_entre_ambitos_que_lo_declaran(ambitos: list[Any]) -> None:
    from asistente.nucleo.modulos.contrato import ContextoModulo

    perfil = {
        "hipoteca_prestamo": {
            "capital_inicial": "150000",
            "fecha_firma": "2022-03-15",
            "plazo_meses": 240,
            "tipo": "fijo",
            "tipo_inicial": "2,1",
            "vivienda_habitual": True,
            "adquisicion_antes_2013": True,
        }
    }
    escrito: list[Any] = []
    contexto = ContextoModulo(perfil=perfil, hoy=date(2026, 11, 22), registrar=escrito.append)
    respuesta = pedir_dato(ambitos, "fiscal", "hipoteca", "pagos_del_ejercicio", contexto)
    assert respuesta.disponible and respuesta.resultado is not None
    assert "Capital pendiente" in respuesta.resultado.contenido
    assert escrito == []  # the owner's tool ran read-only
    hecho = pedir_dato(ambitos, "ayudas", "luz", "tarifa_actual", contexto)
    assert hecho.origen == "hecho:luz_tarifa_actual" and not hecho.disponible
    with pytest.raises(DatoNoDeclaradoError):
        pedir_dato(ambitos, "luz", "hipoteca", "prestamo", contexto)  # luz never declared it
    with pytest.raises(DatoNoDeclaradoError):
        pedir_dato(ambitos, "fiscal", "hipoteca", "no_existe", contexto)


def test_un_modulo_v1_sigue_cargando_con_declaracion_vacia() -> None:
    v1 = ModuloBase(clave="antiguo", nombre="Antiguo", descripcion="Un módulo v1.", instrucciones="Responde.")
    assert declaracion_de(v1) == Declaracion()
    assert problemas_de(v1) == []
