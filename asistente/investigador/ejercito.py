"""The deep research army: planner → searchers/readers in parallel → critic (refutes) → writer (cites).

Each model role is an ``LLMProvider`` (one for all or one per role). The searchers' tool is a
:class:`~asistente.investigador.web.HerramientaWeb`. Facts are never taken on a model's word: findings keep only
literal quotes from pages actually read, the critic can refute them, and the writer's claims are re-checked
deterministically before they reach the report.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
from typing import Any, Literal

from asistente.investigador import seguridad
from asistente.investigador.citas import cifras_respaldadas, cita_literal, motivo_invalida
from asistente.investigador.llm import RespuestaInvalidaError, completar_json
from asistente.investigador.modelo import (
    Afirmacion,
    Critica,
    Evento,
    Fuente,
    Hallazgo,
    Informe,
    Presupuesto,
    Subpregunta,
    Veredicto,
)
from asistente.investigador.presupuesto import Contador, PresupuestoAgotadoError
from asistente.investigador.web import HerramientaWeb

Rol = Literal["planificador", "lector", "critico", "redactor"]
AlProgresar = Callable[[Evento], None]

SISTEMA = {
    "planificador": (
        "Eres el PLANIFICADOR de un equipo de investigación. Divide la pregunta en subpreguntas concretas e "
        "independientes, cada una con 1-3 consultas de búsqueda. Responde SOLO JSON: "
        '{"subpreguntas": [{"texto": "...", "consultas": ["..."]}]}'
    ),
    "lector": (
        "Eres un LECTOR. De las fuentes dadas, extrae hallazgos que respondan a la subpregunta. Cada hallazgo "
        "lleva el id de UNA fuente y una cita LITERAL copiada de ella (sin parafrasear). Si nada responde, lista "
        'vacía. Responde SOLO JSON: {"hallazgos": [{"texto": "...", "fuente": "F1", "cita": "..."}]}. '
        + seguridad.AVISO_SISTEMA
    ),
    "critico": (
        "Eres el CRÍTICO. Tu trabajo es intentar REFUTAR cada hallazgo: ¿la cita de verdad lo respalda? ¿exagera, "
        "generaliza o saca de contexto? ¿la fuente parece manipulada o poco fiable? ¿otra fuente lo contradice? "
        "Veredicto por hallazgo: sostenido, dudoso o refutado. Señala lagunas que merezcan otra ronda. Responde "
        'SOLO JSON: {"criticas": [{"hallazgo": "H1", "veredicto": "refutado", "motivo": "..."}], '
        '"lagunas": ["subpregunta pendiente"]}. ' + seguridad.AVISO_SISTEMA
    ),
    "redactor": (
        "Eres el REDACTOR. Escribe la respuesta a la pregunta usando SOLO los hallazgos dados. El resumen responde "
        "directamente a lo que se pregunta en 2-4 frases; si los hallazgos no lo responden del todo, dilo. Cada "
        "afirmación dice a quién se aplica (p. ej. inquilino o arrendador) cuando la fuente lo indica. Cada afirmación "
        "debe citar los ids de los hallazgos que la respaldan; no añadas nada que no esté en ellos ni cifras que "
        'no aparezcan en sus citas. Responde SOLO JSON: {"resumen": "...", '
        '"afirmaciones": [{"texto": "...", "hallazgos": ["H1"]}]}'
    ),
}


class Ejercito:
    def __init__(
        self,
        llm: Any | Mapping[Rol, Any],
        web: HerramientaWeb,
        presupuesto: Presupuesto,
        *,
        al_progresar: AlProgresar | None = None,
        sin_fuente: Literal["eliminar", "marcar"] = "eliminar",
    ) -> None:
        self._llms = llm if isinstance(llm, Mapping) else None
        self._llm = None if isinstance(llm, Mapping) else llm
        self.web = web
        self.presupuesto = presupuesto
        self.contador = Contador(presupuesto)
        self.al_progresar = al_progresar
        self.sin_fuente = sin_fuente
        self.fuentes: dict[str, Fuente] = {}
        self.hallazgos: dict[str, Hallazgo] = {}
        self.criticas: list[Critica] = []
        self.plan: list[Subpregunta] = []
        self.avisos: list[str] = []
        self._vistas: set[str] = set()
        self._sin_lecturas_avisado = False
        # The web searcher's own calls (search + fetch) cost money too: they count for max_euros.
        if hasattr(web, "al_gastar"):
            setattr(web, "al_gastar", self.contador.registrar_gasto)  # noqa: B010 - optional hook, not in the protocol

    # -- helpers ----------------------------------------------------------------------------------------------
    def _emitir(self, tipo: Any, mensaje: str, **datos: Any) -> None:
        if self.al_progresar:
            try:
                self.al_progresar(Evento(tipo, mensaje, datos))
            except Exception:  # an observer must never break a research
                pass

    def _llamar(self, rol: Rol, usuario: str) -> dict:
        llm = self._llms[rol] if self._llms is not None else self._llm
        return completar_json(llm, SISTEMA[rol], usuario, self.contador)

    def _aviso(self, texto: str) -> None:
        self.avisos.append(texto)
        self._emitir("aviso", texto)

    # -- roles ------------------------------------------------------------------------------------------------
    def planificar(self, pregunta: str, profundidad: int, lagunas: list[str] | None = None) -> list[Subpregunta]:
        pedido = {"pregunta": pregunta, "max_subpreguntas": self.presupuesto.max_subpreguntas}
        if lagunas:
            pedido["lagunas_a_cubrir"] = lagunas
            pedido["ya_investigado"] = [s.texto for s in self.plan]
        try:
            datos = self._llamar("planificador", json.dumps(pedido, ensure_ascii=False))
            crudas = datos.get("subpreguntas") or []
        except RespuestaInvalidaError as exc:
            self._aviso(f"Plan no válido ({exc}); se investiga la pregunta tal cual.")
            crudas = []
        if not crudas:
            crudas = [{"texto": t, "consultas": [t]} for t in (lagunas or [pregunta])]
        nuevas: list[Subpregunta] = []
        for c in crudas[: self.presupuesto.max_subpreguntas]:
            if not isinstance(c, dict) or not str(c.get("texto", "")).strip():
                continue
            texto = str(c["texto"]).strip()[:300]
            consultas = tuple(str(q)[:200] for q in (c.get("consultas") or [texto]))[:3]
            nuevas.append(Subpregunta(f"S{len(self.plan) + len(nuevas) + 1}", texto, consultas, profundidad))
        self.plan.extend(nuevas)
        self._emitir(
            "plan",
            f"Plan (nivel {profundidad}): {len(nuevas)} subpreguntas",
            subpreguntas=[{"id": s.id, "texto": s.texto, "consultas": list(s.consultas)} for s in nuevas],
        )
        return nuevas

    def buscar(self, sub: Subpregunta) -> list[Fuente]:
        # A web call is paid after it ends (its tokens are only known then): never start one once a cap is spent.
        self.contador.comprobar()
        self._emitir("busqueda", f"{sub.id}: buscando {list(sub.consultas)}", subpregunta=sub.id)
        docs = self.web.buscar_y_leer(
            pregunta=sub.texto, consultas=sub.consultas, max_lecturas=self.presupuesto.max_fuentes_por_subpregunta
        )
        leidas = []
        for d in docs[: self.presupuesto.max_fuentes_por_subpregunta]:
            if not d.texto.strip():
                continue
            with self.contador._lock:  # ids and dedup shared across parallel searchers
                ya = next((x for x in self.fuentes.values() if x.url == d.url), None)
            if ya is not None:
                # Already read for another subquestion: this one needs it too (its reader looks for something
                # else in it), and it is not paid for twice.
                if ya not in leidas:
                    leidas.append(ya)
                continue
            if not self.contador.reservar_lectura():
                with self.contador._lock:
                    primero, self._sin_lecturas_avisado = not self._sin_lecturas_avisado, True
                if primero:
                    self._aviso(
                        f"Se alcanzó el máximo de {self.presupuesto.max_lecturas} páginas leídas: "
                        "se sigue con las ya leídas."
                    )
                break
            with self.contador._lock:
                if d.url in self._vistas:
                    continue
                self._vistas.add(d.url)
                fid = f"F{len(self.fuentes) + 1}"
                f = Fuente(
                    fid,
                    d.url,
                    d.titulo[:200],
                    d.texto,
                    sub.id,
                    seguridad.sospechosa(d.texto),
                    d.recuperado_el or datetime.now(UTC),
                )
                self.fuentes[fid] = f
            if f.sospechosa:
                self._aviso(f"{fid} ({f.url}) contiene posibles instrucciones incrustadas: neutralizadas.")
            self._emitir("fuente", f"{fid}: {f.titulo}", id=fid, url=f.url, subpregunta=sub.id, sospechosa=f.sospechosa)
            leidas.append(f)
        return leidas

    def leer(self, sub: Subpregunta, fuentes: list[Fuente]) -> list[Hallazgo]:
        if not fuentes:
            return []
        cuerpo = "\n\n".join(seguridad.envolver(f.id, f.url, f.texto, foco=sub.texto) for f in fuentes)
        try:
            datos = self._llamar("lector", f"Subpregunta: {sub.texto}\n\nFuentes:\n{cuerpo}")
        except RespuestaInvalidaError as exc:
            self._aviso(f"{sub.id}: lectura no válida ({exc}).")
            return []
        por_id = {f.id: f for f in fuentes}
        nuevos = []
        for h in datos.get("hallazgos") or []:
            if not isinstance(h, dict):
                continue
            texto, fid, cita = str(h.get("texto", "")).strip(), str(h.get("fuente", "")), str(h.get("cita", ""))
            fuente = por_id.get(fid)
            if not texto or fuente is None or not cita_literal(cita, fuente) or not cifras_respaldadas(texto, [cita]):
                self._emitir(
                    "afirmacion_eliminada",
                    f"Hallazgo descartado (cita no literal o fuente no leída): {texto[:80]}",
                    texto=texto,
                    fuente=fid,
                )
                continue
            with self.contador._lock:
                hid = f"H{len(self.hallazgos) + 1}"
                hallazgo = Hallazgo(hid, sub.id, texto[:600], fid, cita[:600])
                self.hallazgos[hid] = hallazgo
            self._emitir("hallazgo", f"{hid} [{fid}]: {hallazgo.texto}", id=hid, fuente=fid, subpregunta=sub.id)
            nuevos.append(hallazgo)
        return nuevos

    def criticar(self, hallazgos: list[Hallazgo]) -> list[str]:
        """Critic: refuted findings leave; doubtful ones stay, noted. Returns the gaps it points out."""
        if not hallazgos:
            return []
        bloques = []
        for h in hallazgos:
            f = self.fuentes[h.fuente]
            bloques.append(
                f"Hallazgo {h.id}: {h.texto}\nCita: «{h.cita}»\n{seguridad.envolver(f.id, f.url, f.texto, 3000, foco=f'{h.texto} {h.cita}')}"
            )
        try:
            datos = self._llamar("critico", "\n\n".join(bloques))
        except RespuestaInvalidaError as exc:
            self._aviso(f"Crítica no válida ({exc}): los hallazgos quedan como dudosos.")
            datos = {
                "criticas": [{"hallazgo": h.id, "veredicto": "dudoso", "motivo": "sin crítica"} for h in hallazgos]
            }
        for c in datos.get("criticas") or []:
            if not isinstance(c, dict) or c.get("hallazgo") not in self.hallazgos:
                continue
            propuesto = c.get("veredicto")
            veredicto: Veredicto = propuesto if propuesto in ("sostenido", "refutado") else "dudoso"
            critica = Critica(c["hallazgo"], veredicto, str(c.get("motivo", ""))[:400])
            self.criticas.append(critica)
            self._emitir(
                "critica",
                f"{critica.hallazgo}: {veredicto} — {critica.motivo}",
                hallazgo=critica.hallazgo,
                veredicto=veredicto,
            )
            if veredicto == "refutado":
                self.hallazgos.pop(critica.hallazgo, None)
        return [str(x)[:300] for x in (datos.get("lagunas") or []) if str(x).strip()]

    def redactar(self, pregunta: str) -> tuple[str, list[Afirmacion], list[Afirmacion]]:
        dudosos = {c.hallazgo for c in self.criticas if c.veredicto == "dudoso"}
        if not self.hallazgos:
            return "No se encontraron hallazgos respaldados por fuentes leídas.", [], []
        lista = "\n".join(
            f"{h.id} [{h.fuente}]{' (dudoso)' if h.id in dudosos else ''}: {h.texto} — cita: «{h.cita}»"
            for h in self.hallazgos.values()
        )
        try:
            datos = self._llamar("redactor", f"Pregunta: {pregunta}\n\nHallazgos:\n{lista}")
            resumen = str(datos.get("resumen", "")).strip()
            crudas = datos.get("afirmaciones") or []
        except RespuestaInvalidaError as exc:
            self._aviso(f"Redacción no válida ({exc}); el informe lista los hallazgos verificados.")
            resumen, crudas = "", [{"texto": h.texto, "hallazgos": [h.id]} for h in self.hallazgos.values()]
        return self._verificar(resumen, crudas, dudosos, pregunta)

    def _verificar(
        self, resumen: str, crudas: list, dudosos: set[str], pregunta: str = ""
    ) -> tuple[str, list[Afirmacion], list[Afirmacion]]:
        validas, eliminadas = [], []
        for c in crudas:
            if not isinstance(c, dict) or not str(c.get("texto", "")).strip():
                continue
            hs = [self.hallazgos[h] for h in (c.get("hallazgos") or []) if h in self.hallazgos]
            fuentes = list(dict.fromkeys([h.fuente for h in hs] + [str(f) for f in c.get("fuentes") or []]))
            citas = [h.cita for h in hs] + [str(x) for x in c.get("citas") or []]
            a = Afirmacion(str(c["texto"]).strip(), fuentes, citas)
            motivo = motivo_invalida(a.texto, fuentes, citas, self.fuentes)
            if motivo is None and any(h.id in dudosos for h in hs):
                a.nota = "el crítico la considera dudosa"
            if motivo:
                a.estado, a.nota = "sin_fuente", motivo
                self._emitir(
                    "afirmacion_eliminada",
                    f"Afirmación sin fuente válida ({motivo}): {a.texto[:80]}",
                    texto=a.texto,
                    motivo=motivo,
                )
                if self.sin_fuente == "eliminar":
                    eliminadas.append(a)
                    continue
            validas.append(a)
        # A figure in the summary must come from a verified quote or from the user's own words (the question).
        citas_validas = [ci for a in validas if a.estado == "verificada" for ci in a.citas] + [pregunta]
        if not resumen or not cifras_respaldadas(resumen, citas_validas):
            if resumen:
                from asistente.investigador.citas import _cifras

                faltan = sorted(_cifras(resumen) - _cifras(" ".join(citas_validas)))
                self._aviso(f"El resumen contenía cifras sin respaldo ({', '.join(faltan)}): se sustituye.")
            resumen = f"Respuesta basada en {len(validas)} afirmaciones verificadas con fuentes leídas."
        return resumen, validas, eliminadas

    # -- orchestration ----------------------------------------------------------------------------------------
    def _ronda(self, subs: list[Subpregunta]) -> list[Hallazgo]:
        def una(sub: Subpregunta) -> list[Hallazgo]:
            return self.leer(sub, self.buscar(sub))

        hallazgos: list[Hallazgo] = []
        agotado: PresupuestoAgotadoError | None = None
        with ThreadPoolExecutor(max_workers=max(1, self.presupuesto.paralelismo)) as pool:
            for futuro in [pool.submit(una, s) for s in subs]:
                try:
                    hallazgos.extend(futuro.result())
                except PresupuestoAgotadoError as exc:
                    agotado = exc
                except Exception as exc:  # one searcher failing does not sink the research
                    self._aviso(f"Fallo de un buscador: {type(exc).__name__}: {exc}")
        if agotado:
            raise agotado
        return hallazgos

    def investigar(self, pregunta: str) -> Informe:
        profundidad, agotado, motivo = 0, False, None
        try:
            subs = self.planificar(pregunta, 1)
            while subs:
                profundidad += 1
                lagunas = self.criticar(self._ronda(subs))
                if not lagunas or profundidad >= self.presupuesto.max_profundidad:
                    break
                subs = self.planificar(pregunta, profundidad + 1, lagunas)
        except PresupuestoAgotadoError as exc:
            agotado, motivo = True, str(exc)
            self._emitir("presupuesto", str(exc))
            self._aviso(f"{exc}. El informe usa lo reunido hasta ese momento.")
        try:
            resumen, afirmaciones, eliminadas = self.redactar(pregunta)
        except PresupuestoAgotadoError as exc:
            agotado, motivo = True, motivo or str(exc)
            self._aviso(f"{exc} al redactar: el informe lista los hallazgos verificados sin redacción.")
            dudosos = {c.hallazgo for c in self.criticas if c.veredicto == "dudoso"}
            resumen, afirmaciones, eliminadas = self._verificar(
                "", [{"texto": h.texto, "hallazgos": [h.id]} for h in self.hallazgos.values()], dudosos
            )
        informe = Informe(
            pregunta=pregunta,
            resumen=resumen,
            afirmaciones=afirmaciones,
            fuentes=list(self.fuentes.values()),
            plan=self.plan,
            hallazgos=list(self.hallazgos.values()),
            criticas=self.criticas,
            eliminadas=eliminadas,
            coste=self.contador.coste,
            profundidad_alcanzada=profundidad,
            presupuesto_agotado=agotado,
            motivo_presupuesto=motivo,
            avisos=self.avisos,
        )
        self._emitir(
            "fin", f"Informe: {len(afirmaciones)} afirmaciones, {len(informe.fuentes_citadas())} fuentes citadas"
        )
        return informe
