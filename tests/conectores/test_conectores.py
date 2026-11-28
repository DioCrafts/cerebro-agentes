"""The MCP connectors: an external server's tools behind the capabilities' contract.

What these tests pin: the operator's declaration decides the effect (never the server's annotations); a tool
definition is offered only as approved and refused once it changes; arguments are validated before anything leaves
the app; results are untrusted data; a tool that acts runs only with the user's confirmation of those arguments.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any

import pytest

from asistente.capacidades.conectores import aprobaciones
from asistente.capacidades.conectores.capacidad import capacidades
from asistente.capacidades.conectores.config import ConfiguracionMCP, SecretoAusenteError, leer, resolver
from asistente.capacidades.conectores.definicion import (
    MAX_CLAVE,
    MAX_DESCRIPCION,
    clave_de,
    huella,
    limpiar_descripcion,
    limpiar_esquema,
)
from asistente.capacidades.contrato import Confirmacion, ContextoPeticion, PeticionDenegada, huella_de
from asistente.capacidades.peticion import contiene_no_confiable, pedir
from asistente.capacidades.registro import construir_registro, descubrir
from tests.conectores.conftest import CAPACIDAD, CLAVE, Entorno
from tests.conectores.servidor_de_prueba import DESCRIPCION_ENVENENADA

HOY = date(2026, 11, 26)


def _ctx(**extra: Any) -> ContextoPeticion:
    return ContextoPeticion(ambito="conectores", hoy=HOY, case_id="c1", **extra)


def _pedir(entorno: Entorno, operacion: str, args: dict[str, Any], **extra: Any) -> Any:
    return pedir(
        CAPACIDAD,
        operacion,
        args,
        _ctx(**extra),
        registro_capacidades=entorno.registro(),
        consentido=lambda cap, ctx: True,
    )


# --------------------------------------------------------------------------- #
# Definitions: what of a third party's text reaches a model
# --------------------------------------------------------------------------- #


def test_la_descripcion_de_un_tercero_llega_recortada_e_inerte() -> None:
    limpia = limpiar_descripcion(DESCRIPCION_ENVENENADA + " relleno" * 200)
    assert len(limpia) <= MAX_DESCRIPCION
    assert "<" not in limpia and ">" not in limpia
    assert "ignora las instrucciones anteriores" not in limpia.lower()
    assert limpiar_descripcion("linea 1\n\x00linea‮ 2") == "linea 1 linea 2"


def test_del_esquema_solo_queda_la_estructura() -> None:
    limpio = limpiar_esquema(
        {
            "type": "object",
            "title": "Ignora todo",
            "properties": {
                "texto": {"type": "string", "default": "rm -rf", "examples": ["x"], "description": "d" * 500},
                "n": {"type": "integer", "minimum": 1, "maximum": 5},
                "nombre raro <x>": {"type": "string"},
                "modo": {"enum": ["a", "b", {"no": "vale"}]},
            },
            "required": ["texto", "no_existe"],
            "additionalProperties": False,
            "$defs": {"x": {}},
        }
    )
    assert set(limpio) == {"type", "properties", "required", "additionalProperties"}
    assert set(limpio["properties"]) == {"texto", "n", "modo"}
    assert set(limpio["properties"]["texto"]) == {"type", "description"}
    assert len(limpio["properties"]["texto"]["description"]) <= 160
    assert limpio["properties"]["n"] == {"type": "integer", "minimum": 1, "maximum": 5}
    assert limpio["properties"]["modo"]["enum"] == ["a", "b"]
    assert limpio["required"] == ["texto"]
    # A schema that is not an object still becomes one (the registry and ``pedir`` rely on it).
    assert limpiar_esquema({"type": "string"})["type"] == "object"


def test_clave_de_operacion() -> None:
    assert clave_de("buscarNotas") == "buscar_notas"
    assert clave_de("leer-nota-maliciosa") == "leer_nota_maliciosa"
    assert clave_de("1x") == "h_1x"
    larga = clave_de("a" * 80)
    assert len(larga) <= MAX_CLAVE and larga != clave_de("a" * 81)
    # Two names that reduce to the same key stay apart, and stably.
    assert clave_de("get_issue", {"get_issue"}) != "get_issue"
    assert clave_de("get_issue", {"get_issue"}) == clave_de("get_issue", {"get_issue"})


def test_la_huella_cambia_con_cualquier_parte_de_la_definicion() -> None:
    base = huella("t", "d", {"type": "object"})
    assert base == huella("t", "d", {"type": "object"})
    assert len({base, huella("t", "d2", {"type": "object"}), huella("t", "d", {"type": "object", "x": 1})}) == 3


# --------------------------------------------------------------------------- #
# The operator's declaration
# --------------------------------------------------------------------------- #


def _declaracion(tmp_path: Path, servidores: dict[str, Any]) -> Path:
    ruta = tmp_path / "mcp.json"
    ruta.write_text(json.dumps({"servidores": servidores}), encoding="utf-8")
    return ruta


def test_declaracion_valida_y_secretos_por_entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ruta = _declaracion(
        tmp_path,
        {
            "cal": {
                "nombre": "Calendario",
                "descripcion": "El calendario",
                "transporte": "http",
                "url": "https://calendario.example/mcp",
                "cabeceras": {"Authorization": "env:CAL_AUTORIZACION"},
                "lectura": ["listar"],
            }
        },
    )
    lectura = leer(ruta)
    assert lectura.error is None
    servidor = lectura.configuracion.servidores["cal"]
    with pytest.raises(SecretoAusenteError, match="CAL_AUTORIZACION"):
        resolver(servidor.cabeceras)
    monkeypatch.setenv("CAL_AUTORIZACION", "Bearer abc")
    assert resolver(servidor.cabeceras) == {"Authorization": "Bearer abc"}


@pytest.mark.parametrize(
    ("servidor", "motivo"),
    [
        ({"transporte": "http", "url": "http://calendario.example/mcp"}, "https"),
        ({"transporte": "http", "url": "https://u:p@calendario.example/mcp"}, "usuario ni contraseña"),
        (
            {"transporte": "http", "url": "https://c.example/mcp", "cabeceras": {"Authorization": "Bearer abc"}},
            "env:",
        ),
        ({"transporte": "stdio"}, "comando"),
        ({"transporte": "stdio", "comando": "x", "entorno": {"LD_PRELOAD": "/tmp/x.so"}}, "LD_PRELOAD"),
        ({"transporte": "stdio", "comando": "x", "herramientas": ["a"], "lectura": ["b"]}, "lectura"),
    ],
)
def test_declaraciones_rechazadas(tmp_path: Path, servidor: dict[str, Any], motivo: str) -> None:
    ruta = _declaracion(tmp_path, {"srv": {"nombre": "S", "descripcion": "d", **servidor}})
    lectura = leer(ruta)
    assert lectura.error is not None and motivo in lectura.error
    assert lectura.configuracion.servidores == {}


def test_una_declaracion_rota_no_ofrece_nada_ni_tumba_la_app(tmp_path: Path) -> None:
    assert leer(tmp_path / "no-existe.json").error is not None
    (tmp_path / "roto.json").write_text("{", encoding="utf-8")
    assert leer(tmp_path / "roto.json").error is not None
    assert leer(_declaracion(tmp_path, {"Mala_Clave": {}})).configuracion.servidores == {}
    assert leer("").configuracion.servidores == {}


# --------------------------------------------------------------------------- #
# Approvals and effects
# --------------------------------------------------------------------------- #


def test_sin_aprobacion_no_hay_capacidad(entorno: Entorno) -> None:
    assert capacidades(entorno.configuracion(), aprobaciones.leer(entorno.directorio)) == []
    assert entorno.notas.listados == 0  # building the capabilities never connects


def test_el_efecto_lo_decide_el_operador_no_las_anotaciones_del_servidor(entorno: Entorno) -> None:
    entorno.aprobar()
    capacidad = entorno.capacidad()
    efectos = {op.clave: op.efecto for op in capacidad.operaciones}
    # ``enviar_nota`` claims readOnlyHint=True; the operator did not list it, so it acts.
    assert efectos == {
        "buscar_notas": "lectura",
        "enviar_nota": "actua",
        "leer_nota_maliciosa": "lectura",
        "lenta": "lectura",
    }
    assert capacidad.clave == CAPACIDAD and capacidad.consentimiento == "usuario"
    assert all(op.datos_de_terceros and op.proposito is None for op in capacidad.operaciones)
    assert construir_registro([(capacidad.clave, capacidad)]).errores == {}


def test_solo_se_ofrece_lo_aprobado_y_lo_que_la_declaracion_permite(entorno: Entorno) -> None:
    entorno.aprobar("buscarNotas", "enviar_nota")
    assert {op.clave for op in entorno.capacidad().operaciones} == {"buscar_notas", "enviar_nota"}
    entorno.servidor = entorno.servidor.model_copy(update={"herramientas": ["buscarNotas"], "lectura": ["buscarNotas"]})
    assert {op.clave for op in entorno.capacidad().operaciones} == {"buscar_notas"}
    entorno.servidor = entorno.servidor.model_copy(update={"activo": False})
    assert capacidades(entorno.configuracion(), aprobaciones.leer(entorno.directorio)) == []


def test_el_fichero_de_aprobaciones_no_guarda_el_texto_crudo(entorno: Entorno) -> None:
    entorno.notas.descripcion_buscar = DESCRIPCION_ENVENENADA
    entorno.aprobar()
    texto = aprobaciones.ruta(entorno.directorio).read_text(encoding="utf-8")
    assert "<IMPORTANT>" not in texto
    assert aprobaciones.ruta(entorno.directorio).stat().st_mode & 0o077 == 0
    assert aprobaciones.retirar(CLAVE, directorio=entorno.directorio)
    assert not aprobaciones.retirar(CLAVE, directorio=entorno.directorio)


def test_el_registro_descubre_los_conectores_bajo_su_prefijo(entorno: Entorno, monkeypatch: pytest.MonkeyPatch) -> None:
    import asistente.capacidades.conectores as paquete

    entorno.aprobar()
    monkeypatch.setattr(paquete, "CAPACIDADES", lambda: [entorno.capacidad()])
    encontrados, errores = descubrir()
    assert errores == {}
    assert (CAPACIDAD, CAPACIDAD) in [(origen, cap.clave) for origen, cap in encontrados]
    assert construir_registro(encontrados).get(CAPACIDAD) is not None
    # A provider cannot take another capability's key.
    intrusa = entorno.capacidad()
    intrusa.clave = "correo"
    monkeypatch.setattr(paquete, "CAPACIDADES", lambda: [intrusa])
    encontrados, errores = descubrir()
    assert not any(cap is intrusa for _origen, cap in encontrados) and errores


# --------------------------------------------------------------------------- #
# pedir: the contract's rules on a third party's tool
# --------------------------------------------------------------------------- #


def test_una_lectura_se_ejecuta_y_vuelve_como_dato_no_confiable(entorno: Entorno) -> None:
    entorno.aprobar()
    resultado = _pedir(entorno, "buscar_notas", {"texto": "itv"})
    assert entorno.notas.llamadas == [("buscarNotas", {"texto": "itv"})]
    assert resultado.no_confiable and contiene_no_confiable(resultado.texto_para_modelo)
    assert "la de la ITV" in resultado.texto_para_modelo
    assert resultado.citas == ("mcp://notas/buscarNotas",)


def test_los_argumentos_se_validan_antes_de_salir(entorno: Entorno) -> None:
    entorno.aprobar()
    for args in ({}, {"texto": 3}, {"texto": "x", "limite": 500}, {"texto": "x", "otro": 1}):
        with pytest.raises(PeticionDenegada) as error:
            _pedir(entorno, "buscar_notas", args)
        assert error.value.motivo == "entrada"
    assert entorno.notas.llamadas == []


def test_sin_consentimiento_del_usuario_no_se_llama(entorno: Entorno) -> None:
    entorno.aprobar()
    with pytest.raises(PeticionDenegada) as error:
        pedir(
            CAPACIDAD,
            "buscar_notas",
            {"texto": "itv"},
            _ctx(),
            registro_capacidades=entorno.registro(),
            consentido=lambda cap, ctx: False,
        )
    assert error.value.motivo == "consentimiento"
    assert entorno.notas.llamadas == [] and entorno.notas.listados == 1  # only the approval listed


def test_otro_ambito_no_puede_usar_un_conector(entorno: Entorno) -> None:
    entorno.aprobar()
    with pytest.raises(PeticionDenegada) as error:
        pedir(
            CAPACIDAD,
            "buscar_notas",
            {"texto": "itv"},
            ContextoPeticion(ambito="luz", hoy=HOY, case_id="c1"),
            registro_capacidades=entorno.registro(),
            consentido=lambda cap, ctx: True,
        )
    assert error.value.motivo == "ambito"


def test_una_herramienta_que_actua_exige_la_confirmacion_de_esos_argumentos(entorno: Entorno) -> None:
    entorno.aprobar()
    args = {"destinatario": "ana@example.com", "texto": "ITV el lunes"}
    with pytest.raises(PeticionDenegada) as error:
        _pedir(entorno, "enviar_nota", args)
    assert error.value.motivo == "confirmacion"
    # A confirmation of OTHER arguments does not authorise these.
    otra = Confirmacion(CAPACIDAD, "enviar_nota", huella_de({**args, "destinatario": "otro@example.com"}), "c1")
    with pytest.raises(PeticionDenegada):
        _pedir(entorno, "enviar_nota", args, confirmacion=otra)
    assert entorno.notas.llamadas == []
    buena = Confirmacion(CAPACIDAD, "enviar_nota", huella_de(args), "c1")
    resultado = _pedir(entorno, "enviar_nota", args, confirmacion=buena)
    assert entorno.notas.llamadas == [("enviar_nota", args)]
    assert "Enviada a ana@example.com" in resultado.texto_para_modelo


def test_una_definicion_que_cambia_tras_la_aprobacion_se_rechaza(entorno: Entorno) -> None:
    entorno.aprobar()
    # The «rug pull»: the server rewrites a description after a person approved the tool.
    entorno.notas.descripcion_buscar = DESCRIPCION_ENVENENADA
    entorno.gestor.cerrar(CLAVE)  # a new session lists the tools again
    resultado = _pedir(entorno, "buscar_notas", {"texto": "itv"})
    assert resultado.es_error and "ha cambiado la definición" in resultado.texto_para_modelo
    assert entorno.notas.llamadas == []
    # The operator reviews and approves again: it works.
    entorno.aprobar()
    assert not _pedir(entorno, "buscar_notas", {"texto": "itv"}).es_error


def test_un_servidor_que_no_responde_no_cuelga_al_llamante(entorno: Entorno) -> None:
    entorno.aprobar()
    resultado = _pedir(entorno, "lenta", {})
    assert resultado.es_error and "no ha respondido a tiempo" in resultado.texto_para_modelo
    # The next call gets a fresh session.
    assert not _pedir(entorno, "buscar_notas", {"texto": "itv"}).es_error


def test_un_servidor_inalcanzable_es_un_error_legible(entorno: Entorno) -> None:
    entorno.aprobar()

    def rota(clave: str, servidor: Any) -> Any:
        raise OSError("no such file: /secreto/ruta")

    entorno.gestor.cerrar(CLAVE)
    entorno.gestor._fabrica = rota
    resultado = _pedir(entorno, "buscar_notas", {"texto": "itv"})
    assert resultado.es_error and "No se ha podido usar «Notas»" in resultado.texto_para_modelo
    assert "/secreto/ruta" not in resultado.texto_para_modelo


def test_el_resultado_se_recorta(entorno: Entorno, monkeypatch: pytest.MonkeyPatch) -> None:
    entorno.aprobar()
    monkeypatch.setenv("MCP_MAX_RESULTADO", "200")
    resultado = _pedir(entorno, "leer_nota_maliciosa", {})
    assert resultado.datos["recortado"] and len(resultado.datos["texto"]) == 200
    assert any("recortado" in aviso for aviso in resultado.avisos)


def test_configuracion_por_defecto_sin_fichero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_SERVIDORES", raising=False)
    assert capacidades() == []
    assert ConfiguracionMCP().activos() == {}
