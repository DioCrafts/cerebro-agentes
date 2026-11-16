"""The research method: plan → gather → synthesise → gate, with caps, cache and the web behind consent.

Deterministic around the model:

* **plan** — a small model splits the question (``plan.py``); without it, the question itself.
* **gather** — sources in priority order: the app's legal corpus (the agent's own searcher, with the doctrine
  validity graph), the BOE (consolidated legislation: open-data search + the norm's text), and only when the
  operator switched it on (``INVESTIGACION_WEB``), the user consented and the provider supports it, the open
  web through the provider's server-side search (``web.py``), restricted to the official allow-list unless the
  request asks for the open web, never on a blocked host. Every source gets an id (S1, S2…), its retrieval
  date, and whether it is official.
* **synthesise** — a medium model writes claims with literal quotes (``sintesis.py``).
* **gate** — code keeps only the claims whose quotes are literally in the fetched text, whose figures are in
  their quotes and that carry no injection; flags conflicts; computes the confidence (``puerta.py``). The
  answer is assembled from the surviving claims only.

Caps per research (calls by depth, tokens, USD: ``presupuesto.py``) on top of the daily limits of the metering
wrapper; a report is cached per user, question, settings and day (``almacen.py``). The capability has NO
write or act operation: it reads public sources and returns a report.

What goes to the model provider: the question (and the optional domain hint) — to plan, to search the web and
to synthesise — plus the retrieved public texts. Never the user's case: callers must not put case facts in the
question (the agent tool refuses them, ``herramienta.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
import json
import logging
from typing import Any

from hacienda_gpt.agent import enrutado
from hacienda_gpt.agent.providers.base import ProviderError
from hacienda_gpt.agent.search import CorpusSearcher
from hacienda_gpt.capacidades.investigacion import plan as planificacion
from hacienda_gpt.capacidades.investigacion import puerta, sintesis
from hacienda_gpt.capacidades.investigacion.ajustes import Ajustes, ajustes
from hacienda_gpt.capacidades.investigacion.almacen import AlmacenInvestigacion, clave_cache
from hacienda_gpt.capacidades.investigacion.dominios import es_oficial
from hacienda_gpt.capacidades.investigacion.fuentes import (
    ClienteBOE,
    FuenteRecuperada,
    buscar_boe,
    buscar_corpus,
    extracto,
    palabras_clave,
)
from hacienda_gpt.capacidades.investigacion.modelo import (
    TIPOS_FUENTE,
    EstadoWeb,
    FuenteInforme,
    Informe,
    PeticionInvestigacion,
    TipoFuente,
)
from hacienda_gpt.capacidades.investigacion.presupuesto import (
    Coster,
    PresupuestoAgotado,
    PresupuestoInvestigacion,
    capturar,
    observar,
)
from hacienda_gpt.capacidades.investigacion.web import BuscadorWeb, WebNoDisponible, web_para

logger = logging.getLogger(__name__)

PROPOSITO_WEB = "investigacion:web"
# Characters of each source sent to the synthesis model.
EXTRACTO: dict[TipoFuente, int] = {"corpus": 2500, "boe": 5000, "web": 5000}
SIN_RESPUESTA = "No he podido verificar ninguna respuesta con citas literales de las fuentes encontradas."


def _hoy(ahora: datetime) -> date:
    from hacienda_gpt.ops.consumo import hoy_madrid

    return hoy_madrid(ahora)


def _complete_json(proveedor: Any, *, validar: enrutado.Validador | None = None, **kwargs: Any) -> dict[str, Any]:
    """``complete_json``, with the deterministic check for escalation when the provider is metered."""
    from hacienda_gpt.ops.consumo import MeteredProvider

    if isinstance(proveedor, MeteredProvider):
        return proveedor.complete_json(validar=validar, **kwargs)
    datos: dict[str, Any] = proveedor.complete_json(**kwargs)
    return datos


@dataclass
class _Ejecucion:
    """The state of one research while it runs."""

    peticion: PeticionInvestigacion
    ajustes: Ajustes
    presupuesto: PresupuestoInvestigacion
    web: EstadoWeb
    avisos: list[str] = field(default_factory=list)
    fallo_modelo: bool = False


@dataclass
class Investigador:
    """Runs researches. ``searcher`` / ``boe`` None: that source is unavailable on this server (said so)."""

    searcher: CorpusSearcher | None = None
    boe: ClienteBOE | None = field(default_factory=ClienteBOE)
    grafo: Any | None = None
    almacen: AlmacenInvestigacion | None = None
    # The web searcher that goes with a provider (WebNoDisponible when there is none).
    web: Callable[[Any], BuscadorWeb] = web_para
    reloj: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    ajustes_de: Callable[[], Ajustes] = ajustes
    coster: Coster | None = None

    # ------------------------------------------------------------------ #

    def web_consentida(self, user_id: str | None) -> bool:
        return self.almacen is not None and self.almacen.consentimiento_web(user_id)

    def _motivo_sin_web(self, aj: Ajustes, user_id: str | None) -> str | None:
        if not aj.web:
            return "La búsqueda en la web está desactivada en este servicio."
        if not self.web_consentida(user_id):
            return (
                "No has dado tu consentimiento para buscar en la web: actívalo en «Investigar» si quieres que la "
                "pregunta se envíe al buscador del proveedor del modelo."
            )
        return None

    def investigar(
        self, peticion: PeticionInvestigacion, *, proveedor: Any | None = None, user_id: str | None = None
    ) -> Informe:
        aj = self.ajustes_de()
        lim = aj.limites(peticion.profundidad)
        ahora = self.reloj()
        hoy = _hoy(ahora)
        pedidas = [t for t in TIPOS_FUENTE if t in peticion.fuentes]
        peticion = peticion.model_copy(update={"fuentes": pedidas})

        estado_web = EstadoWeb(pedida="web" in pedidas, usada=False, solo_oficiales=peticion.solo_oficiales)
        buscador: BuscadorWeb | None = None
        if estado_web.pedida:
            motivo = self._motivo_sin_web(aj, user_id)
            if motivo is None:
                try:
                    buscador = self.web(enrutado.para(proveedor, PROPOSITO_WEB) if proveedor is not None else None)
                except WebNoDisponible as exc:
                    motivo = str(exc)
            estado_web.motivo = motivo

        configuracion = json.dumps(
            {
                "web": buscador is not None,
                "permitidos": aj.permitidos_efectivos(),
                "bloqueados": aj.bloqueados,
                "corpus": self.searcher is not None,
                "boe": self.boe is not None,
                "modelo": proveedor is not None,
            },
            sort_keys=True,
        )
        clave = clave_cache(_usuario(user_id), peticion, hoy, configuracion=configuracion)
        if self.almacen is not None:
            self.almacen.purgar(aj.cache_dias, hoy=hoy)
            guardado = self.almacen.informe(clave)
            if guardado is not None:
                return guardado.model_copy(update={"desde_cache": True})

        ejecucion = _Ejecucion(
            peticion=peticion,
            ajustes=aj,
            presupuesto=PresupuestoInvestigacion(
                max_llamadas=lim.llamadas,
                max_tokens=aj.max_tokens,
                max_usd=aj.max_usd,
                precio_busqueda=aj.precio_busqueda_usd,
                **({"coster": self.coster} if self.coster is not None else {}),
            ),
            web=estado_web,
        )
        if estado_web.motivo:
            ejecucion.avisos.append(estado_web.motivo)

        subpreguntas = self._planificar(ejecucion, proveedor, reservadas=1 + (1 if buscador is not None else 0))
        fuentes = self._recoger(ejecucion, subpreguntas, proveedor, buscador)
        informe = self._sintetizar(ejecucion, fuentes, proveedor, ahora)
        if self.almacen is not None and not ejecucion.fallo_modelo and informe.coste.limite_alcanzado is None:
            self.almacen.guardar(clave, user_id, informe, hoy)
        return informe

    # ------------------------------------------------------------------ #
    # Plan
    # ------------------------------------------------------------------ #

    def _planificar(self, ejecucion: _Ejecucion, proveedor: Any | None, *, reservadas: int) -> list[Any]:
        peticion = ejecucion.peticion
        lim = ejecucion.ajustes.limites(peticion.profundidad)
        presupuesto = ejecucion.presupuesto
        # Calls must remain for the web and the synthesis: a plan never eats them.
        if proveedor is None or presupuesto.max_llamadas - presupuesto.llamadas <= reservadas:
            return planificacion.plan_determinista(peticion.pregunta)
        planificador = enrutado.para(proveedor, planificacion.PROPOSITO)
        observar(planificador)
        try:
            presupuesto.comprobar()
            with capturar() as usos:
                try:
                    datos = _complete_json(
                        planificador,
                        system=planificacion.SISTEMA,
                        user=planificacion.mensaje(peticion.pregunta, peticion.ambito, lim.subpreguntas),
                        schema=planificacion.ESQUEMA,
                        schema_name="investigacion_plan",
                        validar=planificacion.validar(lim.subpreguntas),
                    )
                finally:
                    presupuesto.anotar(list(usos), llamadas=max(1, len(usos)))
        except PresupuestoAgotado as exc:
            ejecucion.avisos.append(str(exc))
            return planificacion.plan_determinista(peticion.pregunta)
        except ProviderError as exc:
            logger.warning("research plan failed (%s): deterministic plan used", type(exc).__name__)
            return planificacion.plan_determinista(peticion.pregunta)
        return planificacion.leer_plan(datos, maximo=lim.subpreguntas) or planificacion.plan_determinista(
            peticion.pregunta
        )

    # ------------------------------------------------------------------ #
    # Gather
    # ------------------------------------------------------------------ #

    def _recoger(
        self,
        ejecucion: _Ejecucion,
        subpreguntas: Sequence[Any],
        proveedor: Any | None,
        buscador: BuscadorWeb | None,
    ) -> list[FuenteRecuperada]:
        peticion = ejecucion.peticion
        aj = ejecucion.ajustes
        lim = aj.limites(peticion.profundidad)
        por_tipo: dict[TipoFuente, list[FuenteRecuperada]] = {"corpus": [], "boe": [], "web": []}

        if "corpus" in peticion.fuentes:
            if self.searcher is None:
                ejecucion.avisos.append("El corpus legal de la aplicación no está disponible en este servidor.")
            else:
                for sub in subpreguntas:
                    try:
                        por_tipo["corpus"] += buscar_corpus(
                            self.searcher, sub.consulta_corpus, k=lim.corpus_k, grafo=self.grafo
                        )
                    except Exception:  # one failed search must not lose the others
                        logger.exception("corpus search failed")
                        ejecucion.avisos.append("Una búsqueda en el corpus legal falló.")
        if "boe" in peticion.fuentes:
            if self.boe is None:
                ejecucion.avisos.append("La consulta al BOE no está disponible en este servidor.")
            else:
                for sub in subpreguntas:
                    try:
                        por_tipo["boe"] += buscar_boe(self.boe, sub.terminos_boe, limite=lim.normas_boe)
                    except Exception:
                        logger.exception("BOE search failed")
                        ejecucion.avisos.append("Una búsqueda en el BOE falló.")
        if buscador is not None:
            por_tipo["web"] = self._web(ejecucion, subpreguntas, proveedor, buscador)

        topes: dict[TipoFuente, int] = {
            "corpus": max(2, lim.max_fuentes // 2),
            "boe": lim.normas_boe * 2,
            "web": lim.lecturas_web,
        }
        fuentes: list[FuenteRecuperada] = []
        vistas: set[str] = set()
        for tipo in TIPOS_FUENTE:  # priority order: corpus, BOE, web
            usadas = 0
            for fuente in por_tipo[tipo]:
                if fuente.clave in vistas or usadas >= topes[tipo] or len(fuentes) >= lim.max_fuentes:
                    continue
                vistas.add(fuente.clave)
                fuentes.append(fuente)
                usadas += 1
        terminos = palabras_clave(" ".join([peticion.pregunta, *(s.pregunta for s in subpreguntas)]), 12)
        for indice, fuente in enumerate(fuentes, start=1):
            fuente.id = f"S{indice}"
            fuente.extracto = extracto(fuente.texto, terminos, EXTRACTO[fuente.tipo])
        return fuentes

    def _web(
        self, ejecucion: _Ejecucion, subpreguntas: Sequence[Any], proveedor: Any | None, buscador: BuscadorWeb
    ) -> list[FuenteRecuperada]:
        peticion = ejecucion.peticion
        aj = ejecucion.ajustes
        lim = aj.limites(peticion.profundidad)
        presupuesto = ejecucion.presupuesto
        medidor = enrutado.para(proveedor, PROPOSITO_WEB) if proveedor is not None else None
        permitidos = aj.permitidos_efectivos() if peticion.solo_oficiales else None
        try:
            presupuesto.comprobar()
            comprobar = getattr(medidor, "comprobar_presupuesto", None)
            if callable(comprobar):
                comprobar()  # the service-wide daily limits
            resultado = buscador.buscar(
                pregunta=peticion.pregunta,
                consultas=[s.consulta_web for s in subpreguntas],
                permitidos=permitidos,
                bloqueados=aj.bloqueados,
                max_busquedas=lim.busquedas_web,
                max_lecturas=lim.lecturas_web,
            )
        except PresupuestoAgotado as exc:
            ejecucion.avisos.append(str(exc))
            ejecucion.web.motivo = str(exc)
            return []
        except (ProviderError, WebNoDisponible) as exc:
            ejecucion.avisos.append(f"La búsqueda web no se pudo hacer: {exc}")
            ejecucion.web.motivo = str(exc)
            ejecucion.fallo_modelo = True
            return []
        registrar = getattr(medidor, "registrar_externo", None)
        for usage in resultado.usos:
            if callable(registrar):
                registrar(usage)
        presupuesto.anotar(resultado.usos, llamadas=max(1, len(resultado.usos)), busquedas=resultado.busquedas)
        ejecucion.web.usada = True
        ejecucion.web.busquedas = resultado.busquedas
        ejecucion.web.vistas_sin_leer = [u for u, _t in resultado.vistos][:10]
        if resultado.descartados_por_dominio:
            ejecucion.avisos.append(
                f"Se han descartado {resultado.descartados_por_dominio} resultado(s) web de dominios no permitidos."
            )
        for error in resultado.errores[:3]:
            ejecucion.avisos.append(f"Web: {error}.")
        ahora = self.reloj()
        fuentes = [
            FuenteRecuperada(
                tipo="web",
                titulo=documento.titulo[:200],
                texto=documento.texto,
                url=documento.url,
                oficial=es_oficial(documento.url),
                recuperada_el=documento.recuperado_el or ahora,
            )
            for documento in resultado.documentos
        ]
        ejecucion.web.paginas_leidas = len(fuentes)
        return fuentes

    # ------------------------------------------------------------------ #
    # Synthesis and gate
    # ------------------------------------------------------------------ #

    def _sintetizar(
        self, ejecucion: _Ejecucion, fuentes: list[FuenteRecuperada], proveedor: Any | None, ahora: datetime
    ) -> Informe:
        peticion = ejecucion.peticion
        presupuesto = ejecucion.presupuesto
        por_id = {f.id: f for f in fuentes}
        datos: dict[str, Any] = {}
        no_verificado: list[str] = []
        if not fuentes:
            no_verificado.append("No se ha encontrado ninguna fuente para esta pregunta.")
        elif proveedor is None:
            ejecucion.avisos.append(
                "No hay modelo de IA configurado: se muestran las fuentes encontradas, sin respuesta redactada."
            )
        else:
            sintetizador = enrutado.para(proveedor, sintesis.PROPOSITO)
            observar(sintetizador)

            def validar(salida: dict[str, Any]) -> str | None:
                if salida.get("afirmaciones") and not puerta.revisar(salida, por_id).afirmaciones:
                    return "ninguna afirmación supera la comprobación literal de sus citas"
                return None

            try:
                presupuesto.comprobar()
                with capturar() as usos:
                    try:
                        datos = _complete_json(
                            sintetizador,
                            system=sintesis.SISTEMA,
                            user=sintesis.mensaje(peticion.pregunta, peticion.ambito, fuentes),
                            schema=sintesis.ESQUEMA,
                            schema_name="investigacion_sintesis",
                            validar=validar,
                        )
                    finally:
                        presupuesto.anotar(list(usos), llamadas=max(1, len(usos)))
            except PresupuestoAgotado as exc:
                ejecucion.avisos.append(str(exc))
            except ProviderError as exc:
                ejecucion.fallo_modelo = True
                ejecucion.avisos.append(f"El modelo no pudo redactar la respuesta: {exc}")

        revision = puerta.revisar(datos, por_id)
        no_verificado += revision.descartes + revision.no_verificable
        afirmaciones = revision.afirmaciones
        tabla = [
            FuenteInforme(
                id=f.id,
                tipo=f.tipo,
                titulo=f.titulo[:200],
                url=f.url,
                referencia=f.referencia,
                dominio=f.dominio,
                oficial=f.oficial,
                recuperada_el=f.recuperada_el or ahora,
                citada=f.id in revision.citadas,
                sospechosa=f.sospechosa,
                nota=f.nota,
            )
            for f in fuentes
        ]
        if afirmaciones and not all(a.oficial for a in afirmaciones):
            ejecucion.avisos.append("Parte de la respuesta se apoya solo en fuentes no oficiales: contrástala.")
        return Informe(
            pregunta=peticion.pregunta,
            ambito=peticion.ambito,
            profundidad=peticion.profundidad,
            fuentes_pedidas=list(peticion.fuentes),
            respuesta=puerta.respuesta(afirmaciones) or SIN_RESPUESTA,
            afirmaciones=afirmaciones,
            fuentes=tabla,
            conflictos=revision.conflictos,
            confianza=puerta.confianza(revision, por_id),
            no_verificado=no_verificado,
            avisos=list(dict.fromkeys([*ejecucion.avisos, *revision.avisos])),
            web=ejecucion.web,
            coste=presupuesto.coste(),
            generado_el=ahora,
        )


def _usuario(user_id: str | None) -> str:
    from hacienda_gpt.capacidades.investigacion.almacen import usuario_de

    return usuario_de(user_id)


def texto_informe(informe: Informe) -> str:
    """The report as text for a model (the caller wraps it as untrusted data): answer, quotes, sources."""
    lineas = [f"Pregunta investigada: {informe.pregunta}", f"Confianza: {informe.confianza}", "", "Respuesta:"]
    lineas.append(informe.respuesta)
    if informe.afirmaciones:
        lineas += ["", "Citas literales:"]
        for afirmacion in informe.afirmaciones:
            for cita in afirmacion.citas:
                lineas.append(f"- [{cita.fuente}] «{cita.cita}»")
    if informe.conflictos:
        lineas += ["", "Conflictos entre fuentes:"] + [f"- {c.descripcion}" for c in informe.conflictos]
    if informe.no_verificado:
        lineas += ["", "No verificado:"] + [f"- {t}" for t in informe.no_verificado[:10]]
    lineas += ["", "Fuentes:"]
    for fuente in informe.fuentes:
        donde = fuente.referencia or fuente.url or ""
        marca = "oficial" if fuente.oficial else "NO oficial"
        lineas.append(
            f"- {fuente.id} ({fuente.tipo}, {marca}, consultada {fuente.recuperada_el:%Y-%m-%d}): {fuente.titulo} {donde}"
        )
    if informe.avisos:
        lineas += ["", "Avisos:"] + [f"- {a}" for a in informe.avisos]
    return "\n".join(lineas)


__all__ = ["EXTRACTO", "PROPOSITO_WEB", "SIN_RESPUESTA", "Investigador", "texto_informe"]
