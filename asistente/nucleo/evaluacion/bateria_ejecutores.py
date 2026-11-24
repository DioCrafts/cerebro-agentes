"""Real executors of the battery (``bateria.py``): the deep researcher and the household brain with a model.

Imported only when the battery runs against a model: tests pass their own executors.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import time
from typing import Any

from asistente.nucleo.evaluacion.bateria import CasoBateria, Salida


def ejecutor_investigador() -> Any:
    from asistente.investigador import Presupuesto, investigar

    def ejecutar(caso: CasoBateria, presupuesto_eur: float) -> Salida:
        inicio = time.perf_counter()
        informe = investigar(caso.pregunta, Presupuesto(max_euros=presupuesto_eur))
        lineas = [informe.resumen, ""]
        lineas += [f"- {a.texto} {''.join(f'[{f}]' for f in a.fuentes)}" for a in informe.afirmaciones]
        citadas = informe.fuentes_citadas()
        if citadas:
            lineas += ["", "Fuentes:"] + [f"- [{f.id}] {f.titulo} — {f.url}" for f in citadas]
        evidencias = [f.texto for f in informe.fuentes] + [h.cita for h in informe.hallazgos]
        evidencias += [c for a in informe.afirmaciones for c in a.citas]
        if informe.presupuesto_agotado:
            # Which cap stopped it («Presupuesto agotado: tokens (…)»), so the report says more than "agotado".
            motivo = (informe.motivo_presupuesto or "").removeprefix("Presupuesto agotado: ")
            modo = f"presupuesto_agotado ({motivo})" if motivo else "presupuesto_agotado"
        elif not informe.afirmaciones:
            modo = "abstained"
        else:
            modo = "verified"
        return Salida(
            respuesta="\n".join(lineas),
            modo=modo,
            fuentes=[f.url for f in citadas],
            evidencias=evidencias,
            coste=informe.coste.euros,
            moneda="EUR",
            llamadas_modelo=informe.coste.llamadas,
            latencia_s=time.perf_counter() - inicio,
        )

    return ejecutar


def ejecutor_cerebro(*, max_llamadas_por_caso: int = 60) -> Any:
    """The brain as the evaluation runner builds it (``runner.ejecutar_caso``), with every household module on
    (``modulos_activos_para(None, None)`` under ``MODULOS_ACTIVOS_POR_DEFECTO=todos``)."""
    from asistente.nucleo.agent.agent import TeamAgent
    from asistente.nucleo.agent.providers import create_provider
    from asistente.nucleo.agent.verifier import Verifier
    from asistente.nucleo.evaluacion.ejecutores import ejecutar_cerebro, memoria_para
    from asistente.nucleo.evaluacion.modelos import CasoEvaluacion, Rubrica
    from asistente.nucleo.evaluacion.runner import (
        PROPOSITO_AGENTE,
        PROPOSITO_VERIFICADOR,
        _consumo_de,
        buscador_por_defecto,
    )
    from asistente.nucleo.modulos.preferencias import modulos_activos_para
    from asistente.nucleo.modulos.registro import registro
    from asistente.plataforma import settings
    from asistente.plataforma.ops.consumo import ConsumoStore, MeteredProvider, Presupuesto, contexto_consumo

    consumo = ConsumoStore(str(Path(tempfile.mkdtemp(prefix="bateria-")) / "consumo.sqlite3"))
    limites = Presupuesto(store=consumo, max_llamadas_por_pregunta=max_llamadas_por_caso)
    buscador, _aviso = buscador_por_defecto()
    reg = registro()

    def actua(nombre: str) -> bool:
        encontrada = reg.herramienta(nombre)
        return encontrada is not None and encontrada[1].efecto == "actua"

    def ejecutar(caso: CasoBateria, _presupuesto_eur: float) -> Salida:
        inicio = time.perf_counter()
        clave = f"bateria:{caso.id}:{int(time.time())}"
        provider = MeteredProvider(
            create_provider(), consumo, purpose=PROPOSITO_AGENTE, presupuesto=limites, compartido=True
        )
        agente = TeamAgent(
            provider=provider,
            searcher=buscador,
            verifier=Verifier(provider.con_proposito(PROPOSITO_VERIFICADOR)),
            mode=settings.AGENT_TEAM_MODE,
        )
        # CasoEvaluacion needs an expectation; the battery checks the output itself, so a placeholder rubric.
        evaluacion = CasoEvaluacion(
            id=caso.id,
            categoria="bateria",
            pregunta=caso.pregunta,
            requiere=["cerebro"],
            rubrica=Rubrica(criterios=["(la batería comprueba la salida)"]),
        )
        error = None
        result = None
        with contexto_consumo(case_id=clave):
            try:
                result = ejecutar_cerebro(
                    evaluacion,
                    asesor=agente,
                    proveedor=provider,
                    verificador=agente._verifier,
                    modulos=modulos_activos_para(None, None),
                    case=None,
                    memoria=memoria_para(evaluacion),
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        coste, llamadas, _sin_coste, _tokens = _consumo_de(consumo, clave)
        salida = Salida(
            error=error,
            coste=float(coste or 0),
            moneda="USD",
            llamadas_modelo=llamadas,
            latencia_s=time.perf_counter() - inicio,
        )
        if result is None:
            return salida
        informe = getattr(result, "cerebro", None)
        puerta = informe.puerta if informe is not None else None
        salida.respuesta = result.answer
        salida.modo = result.mode.value
        salida.violaciones_puerta = (
            [f"{v.tipo}: {v.literal} ({v.detalle})" for v in puerta.violaciones] if puerta is not None else None
        )
        salida.evidencias = [e.summary for e in result.tool_trace] + [c.snippet or "" for c in result.citations]
        salida.fuentes = [c.locator for c in result.citations if c.locator.startswith("http")]
        salida.acciones_ejecutadas = [e.tool for e in result.tool_trace if actua(e.tool) and not e.is_error]
        salida.acciones_pendientes = [a.herramienta for a in result.acciones_propuestas]
        return salida

    return ejecutar


def juez_por_defecto() -> Any:
    from asistente.nucleo.agent.providers import create_provider

    return create_provider()


__all__ = ["ejecutor_cerebro", "ejecutor_investigador", "juez_por_defecto"]
