"""Contract v2 of a domain: the v1 adapter, the registry's checks of declared hooks, and no key special-cases."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from hacienda_gpt.decision.schemas import CaseState
from hacienda_gpt.modulos.contrato import AvisoModulo, ContextoModulo
from hacienda_gpt.modulos.ganchos import (
    Ganchos,
    GanchosAgente,
    GanchosBandeja,
    GanchosDocumentos,
    GanchosEventos,
    PuenteAmbito,
    desde_v1,
    es_v2,
    escribe_su_bandeja,
    ganchos_de,
    obligatorio,
    proxima_revision_de_caso,
    tiene_especialista,
)
from hacienda_gpt.modulos.registro import construir_registro, problemas_de
from tests.modulos_nucleo.fakes import Llamadas, ModuloFalso, agua

HOY = date(2026, 11, 16)


def _caso(case_id: str = "c1") -> CaseState:
    ahora = datetime(2026, 11, 12, tzinfo=UTC)
    return CaseState(
        case_id=case_id, user_id="u", jurisdiction="ES", tax_period="2026", created_at=ahora, updated_at=ahora
    )


def _evento() -> Any:
    from hacienda_gpt.eventos.tipos import Evento

    return Evento(
        id=1, uid="e1", tipo="perfil.cambiado", case_id="c1", payload={}, created_at=datetime(2026, 11, 16, tzinfo=UTC)
    )


class AguaV1(ModuloFalso):
    hechos_leidos = ("comunidad_autonoma",)
    herramienta_de_documento = "agua_consumo"

    def leer_documento(self, provider: Any, *, filename: str, data: bytes) -> dict[str, Any]:
        return {"campos": []}

    def proxima_revision(self, contexto: ContextoModulo) -> date | None:
        return date(2026, 12, 1)

    def tarea_diaria(self, hoy: date) -> None:
        return None


def test_the_v1_adapter_reads_every_optional_hook() -> None:
    llamadas = Llamadas()
    v1 = agua(llamadas, herramientas_directas=True)
    ganchos = ganchos_de(v1)
    assert ganchos.version == 1 and not es_v2(v1)
    # Only the read-only tools of a flagged v1 module were ever offered directly.
    assert ganchos.agente.directas == ("agua_consumo", "agua_roto") and not ganchos.agente.especialista
    clase = AguaV1(
        clave="agua", nombre="Agua", descripcion="Agua.", instrucciones="Agua.", eventos=("perfil.cambiado",)
    )
    g = desde_v1(clase)
    assert g.eventos.tipos == ("perfil.cambiado",) and g.eventos.hechos_leidos == ("comunidad_autonoma",)
    assert g.documentos is not None and g.documentos.herramienta_destino == "agua_consumo"
    assert g.tarea_diaria is not None and g.borrar_datos_de_caso is not None and g.bandeja is None
    assert g.agente.especialista and not g.obligatorio

    # A v1 module with its own inbox runner gets the same law-event handling the v1 core gave it.
    class ConBandeja(ModuloFalso):
        tipos_en_bandeja = ("contador",)

        def ejecutar_en_bandeja(self, case: CaseState, *, hoy: date, store: Any) -> dict[str, int]:
            return {"contador": 0}

    propia = desde_v1(ConBandeja(clave="contador", nombre="C", descripcion="C.", instrucciones="C."))
    assert propia.bandeja is not None and propia.bandeja.tipos == ("contador",) and propia.bandeja.al_evento


def _v2(**ganchos: Any) -> ModuloFalso:
    llamadas = Llamadas()
    modulo = agua(llamadas)
    modulo.ganchos = Ganchos(**ganchos)  # type: ignore[attr-defined]
    return modulo


def test_a_direct_tool_must_exist_and_only_read() -> None:
    assert problemas_de(_v2(agente=GanchosAgente(directas=("agua_consumo",), especialista=False))) == []
    problemas = problemas_de(_v2(agente=GanchosAgente(directas=("agua_cambiar_tarifa", "agua_reclamacion", "x"))))
    assert any("agua_cambiar_tarifa" in p and "no es de solo lectura" in p for p in problemas)
    assert any("'x' no es una herramienta del módulo" in p for p in problemas)


def test_only_declared_direct_tools_keep_unprefixed_names() -> None:
    from tests.modulos_nucleo.fakes import spec

    llamadas = Llamadas()
    modulo = ModuloFalso(
        clave="impuestos",
        nombre="Impuestos",
        descripcion="Impuestos.",
        instrucciones="Calcula.",
        herramientas=(llamadas.herramienta("calcular_cuota"),),
    )
    assert any("debe empezar por impuestos_" in p for p in problemas_de(modulo))
    modulo.ganchos = Ganchos(agente=GanchosAgente(directas=("calcular_cuota",)))  # type: ignore[attr-defined]
    assert problemas_de(modulo) == []
    del spec


def test_declared_hooks_are_checked() -> None:
    problemas = "; ".join(
        problemas_de(
            _v2(
                documentos=GanchosDocumentos(leer=lambda *a, **k: {}, herramienta_destino="agua_nada"),
                bandeja=GanchosBandeja(ejecutar=lambda *a, **k: {}, tipos=("modulo:agua",)),
                puentes=(PuenteAmbito(destino="agua", proponer=lambda *a: None),),
                entradas={"factura": "herramienta:agua_cambiar_tarifa", "lectura": "hecho:agua_nada", "x": "otro"},
                capacidades=("Correo!",),
            )
        )
    )
    for esperado in (
        "herramienta_destino 'agua_nada'",
        "son los de los avisos de los módulos",
        "debe proponer a otro ámbito",
        "actúa fuera de la app",
        "'agua_nada' no es un hecho del módulo",
        "debe ser «herramienta:<nombre>»",
        "capacidad no válida",
    ):
        assert esperado in problemas, esperado


def test_nothing_is_told_apart_by_key_any_domain_can_be_the_base() -> None:
    """An always-on domain with its own inbox and no specialist, keyed anything: the core treats it like fiscal."""
    creados: list[str] = []

    def ejecutar(case: CaseState, *, hoy: date, store: Any) -> dict[str, int]:
        creados.append(case.case_id)
        return {"recordatorio": 1}

    base = _v2(
        obligatorio=True,
        bandeja=GanchosBandeja(
            ejecutar=ejecutar, tipos=("recordatorio",), proxima_revision=lambda case, hoy: date(2026, 12, 24)
        ),
        agente=GanchosAgente(directas=("agua_consumo",), especialista=False),
    )
    reg = construir_registro(
        [("agua", base), ("seguros", ModuloFalso(clave="seguros", nombre="S", descripcion="S.", instrucciones="S."))]
    )
    assert list(reg.modulos) == ["agua", "seguros"]  # always-on first
    assert obligatorio(base) and escribe_su_bandeja(base) and not tiene_especialista(base)
    assert proxima_revision_de_caso(base, _caso(), hoy=HOY) == date(2026, 12, 24)

    from hacienda_gpt.eventos.manejadores import despertar_modulo
    from hacienda_gpt.proactive.store import NotificationStore

    store = NotificationStore(":memory:")
    resultado = despertar_modulo(base, _evento(), _caso(), hoy=HOY, store=store)
    assert creados == ["c1"] and resultado.avisos == 1 and resultado.proxima_revision == date(2026, 12, 24)


def test_v2_event_hooks_drive_the_handler() -> None:
    def al_evento(evento: Any, contexto: ContextoModulo) -> list[AvisoModulo]:
        return [AvisoModulo(clave="v2", titulo="Desde v2", cuerpo="b", proxima_revision=date(2026, 11, 30))]

    modulo = _v2(eventos=GanchosEventos(tipos=("perfil.cambiado",), al_evento=al_evento))
    from hacienda_gpt.eventos.manejadores import despertar_modulo
    from hacienda_gpt.eventos.rutas import hechos_que_lee, suscripciones_de
    from hacienda_gpt.proactive.store import NotificationStore

    store = NotificationStore(":memory:")
    resultado = despertar_modulo(modulo, _evento(), _caso(), hoy=HOY, store=store)
    assert resultado.avisos == 1 and resultado.proxima_revision == date(2026, 11, 30)
    assert suscripciones_de(modulo) == frozenset({"perfil.cambiado"})
    # A v2 domain reads what it declares; "*" = every shared fact.
    assert hechos_que_lee(modulo, {"comunidad_autonoma"}) == {h.nombre for h in modulo.hechos}
    todo = _v2(eventos=GanchosEventos(hechos_leidos=("*",)))
    assert "comunidad_autonoma" in hechos_que_lee(todo, {"comunidad_autonoma"})


def test_every_current_domain_is_v2_and_declares_its_integrations() -> None:
    from hacienda_gpt.modulos.registro import registro

    reg = registro()
    for clave in ("fiscal", "luz", "caducidades", "ayudas", "suscripciones", "hipoteca"):
        assert es_v2(reg.get(clave)), clave
        assert ganchos_de(reg.get(clave)).ficheros_legales, clave
    fiscal = ganchos_de(reg.get("fiscal"))
    assert fiscal.obligatorio and fiscal.bandeja is not None and not fiscal.agente.especialista
    assert fiscal.bandeja.tipos == ("plazo", "fin_de_ejercicio") and fiscal.bandeja.proxima_revision is not None
    assert ganchos_de(reg.get("caducidades")).entradas == {"compra": "herramienta:caducidades_registrar_compra"}
    assert ganchos_de(reg.get("luz")).entradas == {"potencia_contratada": "hecho:luz_potencia_p1_kw"}
    assert [p.destino for p in ganchos_de(reg.get("hipoteca")).puentes] == ["fiscal"]
    assert ganchos_de(reg.get("ayudas")).tarea_diaria is not None
    # The capabilities' chat fronts stay v1 modules (adapter).
    assert not es_v2(reg.get("correo")) and not es_v2(reg.get("navegador"))
