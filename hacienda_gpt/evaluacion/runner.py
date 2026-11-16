"""Run the central agent with a variant over the suite, meter it and report.

Two modes:

* ``modelo`` — the real agent loop on a real provider (or any provider a test injects), with the variant's
  prompts and model; every call metered in a separate usage log so the report has the cost of the run;
* ``reproduccion`` — each case's reference script replayed by :class:`ProveedorReproduccion`: no model, no
  key, no cost. It is the deterministic part CI runs on every pull request.

Either way the agent is the production one (``TeamAgent`` with the evidence ledger, the verifier, the
specialists and the pending actions); only the provider differs.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import logging
from pathlib import Path
import statistics
import time
from typing import Any, Literal
from uuid import uuid4

from hacienda_gpt import settings
from hacienda_gpt.agent.agent import AgentResult, TeamAgent
from hacienda_gpt.agent.providers.base import LLMProvider
from hacienda_gpt.agent.search import CorpusSearcher
from hacienda_gpt.agent.verifier import Verifier
from hacienda_gpt.evals.agent_eval import ExcludingSearcher
from hacienda_gpt.evaluacion.cifras import SinCorpus, caso_desde_expediente
from hacienda_gpt.evaluacion.juez import juzgar
from hacienda_gpt.evaluacion.modelos import (
    CasoEvaluacion,
    CitaRegistrada,
    Comparacion,
    ConfigSuite,
    Ejecucion,
    InformeEvaluacion,
    LlamadaRegistrada,
    ResultadoCaso,
    ResumenCategoria,
    ResumenInforme,
    VeredictoJuez,
)
from hacienda_gpt.evaluacion.reproduccion import ProveedorReproduccion
from hacienda_gpt.evaluacion.suite import CifrasEsperadas, cifras_esperadas, comprobar, modulos_de
from hacienda_gpt.evaluacion.variantes import Variante, argumentos_proveedor, usar_variante
from hacienda_gpt.ops.consumo import ConsumoStore, MeteredProvider, contexto_consumo, hoy_madrid

logger = logging.getLogger(__name__)

Modo = Literal["modelo", "reproduccion"]
# (variant, case, expected figures) -> provider for the agent and the verifier.
FabricaProveedor = Callable[[Variante, CasoEvaluacion, dict[str, str]], LLMProvider]


def proveedor_real(variante: Variante, _caso: CasoEvaluacion, _cifras: dict[str, str]) -> LLMProvider:
    """The provider the variant names (settings for whatever it leaves unset)."""
    from hacienda_gpt.agent.providers import create_provider

    return create_provider(**argumentos_proveedor(variante))


def proveedor_reproduccion(_variante: Variante, caso: CasoEvaluacion, cifras: dict[str, str]) -> LLMProvider:
    return ProveedorReproduccion(caso, cifras)


def buscador_por_defecto() -> tuple[CorpusSearcher, str | None]:
    """The production FAISS searcher, or an empty one (with a warning) when there is no index."""
    try:
        from hacienda_gpt.agent.search import FaissCorpusSearcher

        return FaissCorpusSearcher.from_settings(), None
    except Exception as exc:  # no index on this machine: calculator and module cases still run
        return SinCorpus(), f"Sin corpus ({exc}): los casos que necesitan buscar fuentes no pueden aprobar."


@dataclass
class Entorno:
    proveedor: FabricaProveedor
    consumo: ConsumoStore
    modo: Modo = "modelo"
    buscador: CorpusSearcher = field(default_factory=SinCorpus)
    # Judge for rubric cases (None: rubrics are not assessed and the report says so).
    juez: Callable[[], LLMProvider] | None = None
    equipo: str | None = None
    presupuesto_usd: Decimal | None = None
    avisos: list[str] = field(default_factory=list)
    # (provider, model) of the first case run, for the report.
    identidad: tuple[str, str] | None = None


def _metered(provider: LLMProvider, store: ConsumoStore, purpose: str) -> MeteredProvider:
    return MeteredProvider(provider, store, purpose=purpose, compartido=True)


def _ejecucion_de(result: AgentResult) -> Ejecucion:
    return Ejecucion(
        respuesta=result.answer,
        modo=result.mode.value,
        herramientas=[
            LlamadaRegistrada(
                herramienta=e.tool, agente=e.agent, argumentos=e.arguments, resumen=e.summary, es_error=e.is_error
            )
            for e in result.tool_trace
        ],
        citas=[CitaRegistrada(fragment_id=c.fragment_id, autoridad=c.authority) for c in result.citations],
        acciones_pendientes=[a.herramienta for a in result.acciones_propuestas],
    )


def _consumo_de(store: ConsumoStore, clave: str) -> tuple[Decimal | None, int, int, dict[str, int]]:
    hoy = hoy_madrid()
    resumen = store.resumen(desde=hoy - timedelta(days=1), hasta=hoy + timedelta(days=1), por=("caso",))
    for agregado in resumen.por_caso:
        if agregado.clave == clave:
            tokens = {
                "input_tokens": agregado.input_tokens,
                "output_tokens": agregado.output_tokens,
                "cache_read_tokens": agregado.cache_read_tokens,
                "cache_write_tokens": agregado.cache_write_tokens,
            }
            coste = agregado.coste_usd if agregado.llamadas > agregado.llamadas_sin_coste else None
            return coste, agregado.llamadas, agregado.llamadas_sin_coste, tokens
    return None, 0, 0, {}


def ejecutar_caso(
    caso: CasoEvaluacion,
    variante: Variante,
    entorno: Entorno,
    esperadas: CifrasEsperadas,
    *,
    clave_consumo: str,
) -> tuple[Ejecucion, list[VeredictoJuez], list[str]]:
    """Run the agent once on ``caso``. Never raises: failures are recorded on the run."""
    avisos: list[str] = []
    veredictos: list[VeredictoJuez] = []
    inicio = time.perf_counter()
    try:
        provider = _metered(entorno.proveedor(variante, caso, esperadas.cifras), entorno.consumo, "evaluacion:agente")
        if entorno.identidad is None:
            entorno.identidad = (provider.name, provider.model)
        buscador: CorpusSearcher = entorno.buscador
        if caso.ocultar_fuente:
            buscador = ExcludingSearcher(buscador, caso.ocultar_fuente)
        agente = TeamAgent(
            provider=provider,
            searcher=buscador,
            verifier=Verifier(provider.con_proposito("evaluacion:verificador")),
            mode=entorno.equipo or ("never" if entorno.modo == "reproduccion" else settings.AGENT_TEAM_MODE),
        )
        modulos = modulos_de(caso)
        extra: dict[str, Any] = {"modulos": modulos} if modulos else {}
        with usar_variante(variante), contexto_consumo(case_id=clave_consumo):
            result = agente.run(
                caso.pregunta,
                chat_history=list(caso.historial),
                case=caso_desde_expediente(caso.expediente, case_id=f"eval_{caso.id}"),
                **extra,
            )
            ejecucion = _ejecucion_de(result)
            if caso.rubrica is not None:
                if entorno.juez is None:
                    avisos.append("Rúbrica no evaluada: esta ejecución no tiene juez.")
                else:
                    juez = _metered(entorno.juez(), entorno.consumo, "evaluacion:juez")
                    try:
                        veredictos = juzgar(
                            juez, pregunta=caso.pregunta, respuesta=ejecucion.respuesta, rubrica=caso.rubrica
                        )
                    except Exception as exc:  # the judge failing is not the agent failing, but it cannot pass
                        veredictos = [
                            VeredictoJuez(criterio=c, cumple=False, motivo=f"El juez falló: {exc}")
                            for c in caso.rubrica.criterios
                        ]
    except Exception as exc:
        logger.warning("evaluation case %s failed: %s", caso.id, exc)
        ejecucion = Ejecucion(error=f"{type(exc).__name__}: {exc}")
    ejecucion.latencia_s = round(time.perf_counter() - inicio, 3)
    coste, llamadas, sin_coste, tokens = _consumo_de(entorno.consumo, clave_consumo)
    ejecucion.coste_usd = coste
    ejecucion.llamadas_modelo = llamadas
    ejecucion.llamadas_sin_coste = sin_coste
    ejecucion.tokens = tokens
    return ejecucion, veredictos, avisos


def resumir(resultados: Sequence[ResultadoCaso], config: ConfigSuite) -> ResumenInforme:
    por_categoria: dict[str, ResumenCategoria] = {}
    for nombre, categoria in config.categorias.items():
        por_categoria[nombre] = ResumenCategoria(critica=categoria.critica)
    for r in resultados:
        cat = por_categoria.setdefault(r.categoria, ResumenCategoria(critica=r.critica))
        cat.total += 1
        cat.aprobados += r.aprobado
    for cat in por_categoria.values():
        cat.tasa = round(cat.aprobados / cat.total, 4) if cat.total else 0.0
    latencias = sorted(r.ejecucion.latencia_s for r in resultados)
    total = len(resultados)
    aprobados = sum(r.aprobado for r in resultados)
    return ResumenInforme(
        total=total,
        aprobados=aprobados,
        tasa=round(aprobados / total, 4) if total else 0.0,
        por_categoria={k: v for k, v in por_categoria.items() if v.total},
        errores=sum(1 for r in resultados if r.ejecucion.error),
        coste_usd=sum((r.ejecucion.coste_usd or Decimal(0) for r in resultados), Decimal(0)),
        llamadas_sin_coste=sum(r.ejecucion.llamadas_sin_coste for r in resultados),
        llamadas_modelo=sum(r.ejecucion.llamadas_modelo for r in resultados),
        latencia_media_s=round(statistics.fmean(latencias), 3) if latencias else 0.0,
        latencia_p95_s=latencias[min(len(latencias) - 1, int(0.95 * len(latencias)))] if latencias else 0.0,
        juez_sin_cita=sum(1 for r in resultados for v in r.juez if not v.cita_valida),
    )


def comparar(actual: InformeEvaluacion, base: InformeEvaluacion) -> Comparacion:
    """Regressions (passed in ``base``, fail now) and improvements, on the cases both ran."""
    antes = {r.id: r.aprobado for r in base.casos}
    ahora = {r.id: r.aprobado for r in actual.casos}
    comunes = sorted(set(antes) & set(ahora))
    categorias = set(actual.resumen.por_categoria) | set(base.resumen.por_categoria)
    delta: dict[str, float] = {}
    for categoria in sorted(categorias):
        a = actual.resumen.por_categoria.get(categoria)
        b = base.resumen.por_categoria.get(categoria)
        delta[categoria] = round((a.tasa if a else 0.0) - (b.tasa if b else 0.0), 4)
    return Comparacion(
        frente_a=base.variante,
        regresiones=[i for i in comunes if antes[i] and not ahora[i]],
        mejoras=[i for i in comunes if ahora[i] and not antes[i]],
        delta_tasa=round(actual.resumen.tasa - base.resumen.tasa, 4),
        delta_por_categoria=delta,
        delta_coste_usd=actual.resumen.coste_usd - base.resumen.coste_usd,
    )


def evaluar(
    casos: Sequence[CasoEvaluacion],
    variante: Variante,
    entorno: Entorno,
    *,
    config: ConfigSuite,
    suite_huella: str,
    repeticiones: int = 1,
    progreso: Callable[[CasoEvaluacion, ResultadoCaso], None] | None = None,
) -> InformeEvaluacion:
    """Run every case ``repeticiones`` times; a case passes only if every repetition passes."""
    ejecucion_id = uuid4().hex[:12]
    resultados: list[ResultadoCaso] = []
    avisos = list(entorno.avisos)
    gastado = Decimal(0)
    incompleto = False
    for caso in casos:
        categoria = config.categorias.get(caso.categoria)
        critica = bool(categoria and categoria.critica)
        if entorno.presupuesto_usd is not None and gastado >= entorno.presupuesto_usd:
            incompleto = True
            resultados.append(
                ResultadoCaso(
                    id=caso.id,
                    version=caso.version,
                    categoria=caso.categoria,
                    critica=critica,
                    aprobado=False,
                    fallos=["no ejecutado: presupuesto de la evaluación agotado"],
                    ejecucion=Ejecucion(error="presupuesto agotado"),
                )
            )
            continue
        esperadas = cifras_esperadas(caso)
        fallos: list[str] = []
        avisos_caso = list(esperadas.derivas)
        juez: list[VeredictoJuez] = []
        aprobadas = 0
        ejecucion = Ejecucion()
        for repeticion in range(1, repeticiones + 1):
            if entorno.modo == "reproduccion" and caso.guion is None:
                ejecucion = Ejecucion(error="el caso no tiene guion de referencia")
                fallos_rep = ["sin guion de referencia: no se puede reproducir sin modelo"]
                juez_rep: list[VeredictoJuez] = []
            else:
                ejecucion, juez_rep, avisos_rep = ejecutar_caso(
                    caso, variante, entorno, esperadas, clave_consumo=f"eval:{ejecucion_id}:{caso.id}:{repeticion}"
                )
                avisos_caso.extend(a for a in avisos_rep if a not in avisos_caso)
                fallos_rep = comprobar(caso, ejecucion, esperadas)
                fallos_rep += [f"rúbrica: «{v.criterio}» — {v.motivo}" for v in juez_rep if not v.cumple]
            gastado += ejecucion.coste_usd or Decimal(0)
            juez = juez_rep
            if not fallos_rep:
                aprobadas += 1
            prefijo = f"[repetición {repeticion}] " if repeticiones > 1 else ""
            fallos.extend(prefijo + f for f in fallos_rep)
        resultado = ResultadoCaso(
            id=caso.id,
            version=caso.version,
            categoria=caso.categoria,
            critica=critica,
            aprobado=aprobadas == repeticiones,
            repeticiones_aprobadas=aprobadas,
            fallos=fallos,
            avisos=avisos_caso,
            juez=juez,
            ejecucion=ejecucion,
        )
        resultados.append(resultado)
        if progreso is not None:
            progreso(caso, resultado)
    if incompleto:
        avisos.append("Presupuesto agotado: el informe está incompleto y no sirve para promover una variante.")
    nombre_proveedor, modelo = _identidad(variante, entorno)
    from hacienda_gpt.evaluacion.variantes import ROLES, huella, texto_prompt

    return InformeEvaluacion(
        variante=variante.id,
        descripcion_variante=variante.descripcion,
        proveedor=nombre_proveedor,
        modelo=modelo,
        prompts={rol: variante.prompts[rol] for rol in ROLES},
        huellas_prompts={rol: huella(texto_prompt(variante, rol)) for rol in ROLES},
        modo=entorno.modo,
        suite_version=config.version,
        suite_huella=suite_huella,
        generado_el=datetime.now(UTC),
        repeticiones=repeticiones,
        resumen=resumir(resultados, config),
        casos=resultados,
        incompleto=incompleto,
        avisos=avisos,
    )


def _identidad(variante: Variante, entorno: Entorno) -> tuple[str, str]:
    if entorno.identidad is not None:
        return entorno.identidad
    if entorno.modo == "reproduccion":
        return ProveedorReproduccion.name, ProveedorReproduccion.model
    return variante.proveedor or settings.LLM_PROVIDER, variante.modelo or "?"


def guardar(informe: InformeEvaluacion, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(informe.model_dump_json(indent=2) + "\n", encoding="utf-8")


def cargar_informe(ruta: Path) -> InformeEvaluacion:
    return InformeEvaluacion.model_validate_json(ruta.read_text(encoding="utf-8"))


def formatear(informe: InformeEvaluacion) -> str:
    """Plain-text summary for the terminal and CI logs (Spanish)."""
    r = informe.resumen
    lineas = [
        f"Variante «{informe.variante}» ({informe.proveedor}/{informe.modelo}, modo {informe.modo}) — suite "
        f"{informe.suite_version} [{informe.suite_huella[:12]}]",
        f"Aprobados: {r.aprobados}/{r.total} ({r.tasa:.1%}); errores de ejecución: {r.errores}",
        f"Coste: {r.coste_usd} USD en {r.llamadas_modelo} llamadas"
        + (f" ({r.llamadas_sin_coste} sin precio conocido: cota inferior)" if r.llamadas_sin_coste else ""),
        f"Latencia: media {r.latencia_media_s:.2f} s, p95 {r.latencia_p95_s:.2f} s",
        "Por categoría:",
    ]
    for nombre, cat in sorted(r.por_categoria.items()):
        marca = " (crítica)" if cat.critica else ""
        lineas.append(f"  - {nombre}{marca}: {cat.aprobados}/{cat.total} ({cat.tasa:.1%})")
    if r.juez_sin_cita:
        lineas.append(f"Veredictos del juez descartados por no citar la respuesta: {r.juez_sin_cita}")
    if informe.comparacion is not None:
        c = informe.comparacion
        lineas.append(f"Frente a «{c.frente_a}»: {c.delta_tasa:+.1%}; coste {c.delta_coste_usd:+} USD")
        if c.regresiones:
            lineas.append("  Regresiones: " + ", ".join(c.regresiones))
        if c.mejoras:
            lineas.append("  Mejoras: " + ", ".join(c.mejoras))
    fallidos = [caso for caso in informe.casos if not caso.aprobado]
    if fallidos:
        lineas.append("Casos que fallan:")
        for caso in fallidos:
            lineas.append(f"  - {caso.id} [{caso.categoria}]")
            lineas.extend(f"      · {f}" for f in caso.fallos[:6])
    for aviso in informe.avisos:
        lineas.append(f"Aviso: {aviso}")
    derivas = [a for caso in informe.casos for a in caso.avisos]
    for aviso in derivas:
        lineas.append(f"Aviso: {aviso}")
    return "\n".join(lineas)


__all__ = [
    "Entorno",
    "FabricaProveedor",
    "buscador_por_defecto",
    "cargar_informe",
    "comparar",
    "ejecutar_caso",
    "evaluar",
    "formatear",
    "guardar",
    "proveedor_real",
    "proveedor_reproduccion",
    "resumir",
]
