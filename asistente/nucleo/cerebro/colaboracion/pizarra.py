"""The brain's switchboard: a specialist asks another one for a datum THROUGH the brain.

Specialists never talk to each other. A specialist that needs a datum only another one has (the electricity
specialist needs the user's marginal income-tax rate) calls ``pedir_dato``; the call reaches this switchboard through
the specialist's :class:`LineaPizarra` (its encargo's handle, which knows the chain of requests it is part of, its
depth and its cancellation token), and the switchboard decides deterministically whether and how to answer it:

1. **Refused** (``denegada``): an empty datum; a request in another specialist's name; a request to itself; to a
   specialist that is not active in this turn (unknown, or a module the user has not switched on); or one that would
   close a cycle (A asks B, which asks A: B's line carries the chain A→B and A is in it). Why: none of them can be
   answered by anyone else, and a cycle would never end.
2. **Limited** (``limite``): past the depth limit (``min(contrato.MAX_PROFUNDIDAD_PETICIONES,
   settings.CEREBRO_MAX_PROFUNDIDAD_PETICIONES)``: a request may trigger a request, not more), past the per-turn cap
   (``MAX_PETICIONES_POR_TURNO``; every request counts, whatever its outcome, so the audit trail is bounded too),
   once the question's model budget ran out, or once the asking encargo was cancelled. Why: a turn's cost and time
   must be bounded whatever the models do.
3. **Answered from what the turn already knows** (``reutilizada=True``): an identical earlier request (same
   destination, same datum once normalised), or one still in flight, which it waits for; or a finished report of the
   destination with a figure that covers the datum (the figure's concept carries at least ``UMBRAL_COBERTURA`` of the
   datum's words and every best match has the same value: an ambiguous match is never reused). Why: the same
   question to the same specialist in the same turn has the same answer, and paying twice for it is waste.
4. **Answered by the destination**: it runs on a focused sub-encargo (depth + 1), inline in the asking thread, and
   its report's text, figures and fragment ids go back to the requester.

Every request that gets an id is recorded (:attr:`Pizarra.registros`, :attr:`Pizarra.peticiones`) and emitted as
``EVENTO_PETICION``.

**Privacy** (data minimisation): what crosses from the destination to the requester is ONLY the destination's report
on that datum — its text, its figures, the ids of its fragments — never its view of the household profile, its
module's private facts or its tool trace. Towards the destination goes only what the requester wrote in ``dato`` and
``motivo`` (300 characters each) and the requester's display name: the switchboard never attaches the requester's
profile or facts, and the destination answers with its own tools over its own view (shared facts + its module's).
Both directions are neutralised (prompt-injection bait redacted, no tag delimiters): a report may quote untrusted
text (a mail, a web page) and must reach the other model as data.

**Concurrency and termination.** Sub-encargos run inline in the asking thread, never in the dispatcher's pool, so no
pool slot ever waits for another one. Depth is bounded, so every chain ends; the cap bounds the total. Identical
requests are single-flight: the second waits for the first. A wait could deadlock across threads (thread 1 serves
request R1 and, inside it, waits for R2, which thread 2 serves while waiting for R1), so before waiting the switchboard
walks the wait-for graph under its lock: every thread waits for at most one request and every request in flight has
one owner thread, so the graph is a set of chains and the walk is O(threads). If waiting would close a cycle the
request runs again instead (duplicate work, never a deadlock). Check and registration happen under the same lock, so
two threads can never both decide to wait for each other. A wait also ends when the waiter's encargo is cancelled.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import re
import threading
import time
from typing import Any

from asistente.nucleo.cerebro.colaboracion.aislamiento import Cancelacion, EstadoTurno, neutralizar
from asistente.nucleo.cerebro.colaboracion.cifras import formatear_valor, plano
from asistente.nucleo.cerebro.contrato import (
    EVENTO_PETICION,
    MAX_PETICIONES_POR_TURNO,
    MAX_PROFUNDIDAD_PETICIONES,
    Cifra,
    ContextoEncargo,
    ContextoTurno,
    Encargo,
    Especialista,
    InformeEspecialista,
    PeticionDato,
    RespuestaPeticion,
)
from asistente.plataforma import settings

UMBRAL_COBERTURA = 0.75
MAX_DATO = 300
MAX_TEXTO_RESPUESTA = 4000
_CLAVE_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
_PALABRAS_VACIAS = frozenset(
    "de del la el los las lo un una unos unas y e o u a al en por para con sin que mi mis tu tus su sus se es son "
    "cual cuales cuanto cuanta cuantos cuantas como me te le les nos este esta estos estas ese esa esos esas "
    "dato datos cifra cifras valor importe cantidad usuario hogar necesito saber dime".split()
)

# Runs one specialist on one encargo and always returns its report (the dispatcher's isolated runner).
Ejecutor = Callable[[Especialista, ContextoEncargo], InformeEspecialista]


@dataclass(frozen=True)
class RegistroPeticion:
    """One request in the audit trail: what was asked, what came back, and from where in the turn."""

    peticion: PeticionDato
    respuesta: RespuestaPeticion
    raiz: str  # the plan encargo whose tree asked (dispatcher's token: "<call>:<encargo id>")
    encargo_solicitante: str  # the encargo (plan or sub-encargo) whose specialist asked
    directa: bool  # asked by the plan encargo itself (not by one of its sub-encargos)
    subencargo: str | None = None  # the sub-encargo run to answer it
    duracion_ms: int = 0


@dataclass
class _EnVuelo:
    dueno: int  # the thread serving it (sub-encargos run inline, so it is the asking thread)
    respuesta: RespuestaPeticion | None = None
    terminado: bool = False
    esperando: list[threading.Event] = field(default_factory=list)


def _limpio(texto: str, maximo: int) -> str:
    return neutralizar(" ".join(str(texto or "").split()))[:maximo]


def _normal(texto: str) -> str:
    return " ".join(re.sub(r"[^\w%]+", " ", plano(texto)).split())


def _tokens(texto: str) -> set[str]:
    return {t for t in re.findall(r"[a-zñ%]+", plano(texto)) if t not in _PALABRAS_VACIAS and len(t) > 1}


def _casan(a: str, b: str) -> bool:
    """Same word, or one a short inflection of the other («cuota»/«cuotas», «anual»/«anuales»)."""
    if a == b:
        return True
    corta, larga = sorted((a, b), key=len)
    return len(corta) >= 4 and larga.startswith(corta) and len(larga) - len(corta) <= 3


def _anio(texto: str) -> int | None:
    anios = re.findall(r"(?<!\d)(19[9]\d|20\d{2}|2100)(?!\d)", texto)
    return int(anios[-1]) if anios else None


class LineaPizarra:
    """One encargo's handle on the switchboard; implements ``contrato.Pizarra``.

    It carries what the switchboard needs to judge a request: who asks (``clave``), the chain of specialists above it
    (``cadena``, itself included), its depth (0 for a plan encargo), its cancellation token and the turn context the
    destination will run with.
    """

    def __init__(
        self,
        pizarra: Pizarra,
        *,
        clave: str,
        turno: ContextoTurno,
        cadena: tuple[str, ...],
        profundidad: int,
        token: Cancelacion,
        encargo_id: str,
        raiz: str,
    ) -> None:
        self._pizarra = pizarra
        self.clave = clave
        self.turno = turno
        self.cadena = cadena
        self.profundidad = profundidad
        self.token = token
        self.encargo_id = encargo_id
        self.raiz = raiz
        self._propias: list[PeticionDato] = []

    def pedir(self, solicitante: str, destinatario: str, dato: str, motivo: str = "") -> RespuestaPeticion:
        return self._pizarra._pedir(self, solicitante, destinatario, dato, motivo)

    @property
    def puede_pedir(self) -> bool:
        """Whether a request from here could be answered at all (depth, cap, budget, cancellation)."""
        return (
            self.profundidad < self._pizarra.max_profundidad
            and self._pizarra.quedan_peticiones
            and not self.token.cancelada
            and not self._pizarra.estado.agotado
            and bool(self.destinos())
        )

    @property
    def cancelada(self) -> bool:
        return self.token.cancelada

    def destinos(self) -> list[tuple[str, str, str]]:
        """(clave, nombre, descripcion) of the specialists this line may ask (not itself, not its chain)."""
        return [
            (clave, str(getattr(e, "nombre", clave)), " ".join(str(getattr(e, "descripcion", "")).split()))
            for clave, e in self._pizarra.especialistas.items()
            if clave not in self.cadena
        ]

    @property
    def peticiones(self) -> list[PeticionDato]:
        """The requests made through this line, in order."""
        return list(self._propias)


class Pizarra:
    """The turn's switchboard for requests between specialists (one per turn, shared by every encargo).

    ``ejecutar`` runs a specialist safely (the dispatcher's isolated runner). ``pedir`` on the switchboard itself
    asks as a plan-level request of ``solicitante`` (depth 0); specialists ask through their :class:`LineaPizarra`.
    """

    def __init__(
        self,
        especialistas: Mapping[str, Especialista],
        turno: ContextoTurno,
        *,
        ejecutar: Ejecutor,
        estado: EstadoTurno | None = None,
        max_profundidad: int | None = None,
        max_peticiones: int | None = None,
    ) -> None:
        self.especialistas = dict(especialistas)
        self.turno = turno
        self.estado = estado or EstadoTurno()
        profundidad = settings.CEREBRO_MAX_PROFUNDIDAD_PETICIONES if max_profundidad is None else max_profundidad
        self.max_profundidad = max(0, min(MAX_PROFUNDIDAD_PETICIONES, int(profundidad)))
        tope = MAX_PETICIONES_POR_TURNO if max_peticiones is None else max_peticiones
        self.max_peticiones = max(0, min(MAX_PETICIONES_POR_TURNO, int(tope)))
        self._ejecutar = ejecutar
        self._lock = threading.Lock()
        self._contador = 0
        self.descartadas = 0  # requests refused without an id (cap reached, cancelled encargo)
        self._registros: list[RegistroPeticion] = []
        self._hechas: dict[tuple[str, str], RespuestaPeticion] = {}
        self._en_vuelo: dict[tuple[str, str], _EnVuelo] = {}
        self._bloqueos: dict[int, tuple[str, str]] = {}  # thread -> request it waits for
        self._informes: list[InformeEspecialista] = []  # finished reports (plan and sub-encargos), for reuse
        self._subinformes: list[InformeEspecialista] = []
        self._ids_usados: set[str] = set()

    # ------------------------------------------------------------------ public

    @property
    def quedan_peticiones(self) -> bool:
        return self._contador < self.max_peticiones

    @property
    def registros(self) -> list[RegistroPeticion]:
        with self._lock:
            return sorted(self._registros, key=lambda r: int(r.peticion.id[1:]))

    @property
    def peticiones(self) -> list[PeticionDato]:
        return [r.peticion for r in self.registros]

    @property
    def subinformes(self) -> list[InformeEspecialista]:
        """Reports of the sub-encargos run to answer requests, in request order."""
        with self._lock:
            return list(self._subinformes)

    def linea(
        self,
        clave: str,
        *,
        turno: ContextoTurno | None = None,
        token: Cancelacion | None = None,
        encargo_id: str = "",
        raiz: str = "",
    ) -> LineaPizarra:
        """The handle of a plan encargo of ``clave`` (depth 0, chain = itself)."""
        return LineaPizarra(
            self,
            clave=clave,
            turno=turno or self.turno,
            cadena=(clave,),
            profundidad=0,
            token=token or Cancelacion(),
            encargo_id=encargo_id,
            raiz=raiz or encargo_id,
        )

    def pedir(self, solicitante: str, destinatario: str, dato: str, motivo: str = "") -> RespuestaPeticion:
        """``contrato.Pizarra``: a request of ``solicitante`` outside any encargo (depth 0)."""
        if not _CLAVE_RE.fullmatch(solicitante or ""):
            return RespuestaPeticion(peticion_id="", estado="denegada", texto="Solicitante no válido.")
        return self.linea(solicitante).pedir(solicitante, destinatario, dato, motivo)

    def anotar_informe(self, informe: InformeEspecialista) -> None:
        """A finished report of the turn (its figures may answer later requests)."""
        with self._lock:
            self._informes.append(informe)

    def reservar_ids(self, ids: Iterable[str]) -> None:
        """Encargo ids the plan uses (sub-encargo ids never collide with them)."""
        with self._lock:
            self._ids_usados.update(ids)

    def respuestas_de(self, raiz: str) -> list[RegistroPeticion]:
        """The requests of one plan encargo's tree (the dispatcher merges their figures into its report)."""
        with self._lock:
            return [r for r in self._registros if r.raiz == raiz]

    def renumerar(self, funcion: Callable[[Cifra], Cifra]) -> None:
        """Apply the turn's figure numbering to everything recorded here."""

        def informe(i: InformeEspecialista) -> InformeEspecialista:
            return i.model_copy(update={"cifras": [funcion(c) for c in i.cifras]})

        def respuesta(r: RespuestaPeticion) -> RespuestaPeticion:
            return r.model_copy(update={"cifras": [funcion(c) for c in r.cifras]})

        with self._lock:
            self._subinformes = [informe(i) for i in self._subinformes]
            self._informes = [informe(i) for i in self._informes]
            self._hechas = {k: respuesta(r) for k, r in self._hechas.items()}
            self._registros = [
                RegistroPeticion(
                    peticion=r.peticion,
                    respuesta=respuesta(r.respuesta),
                    raiz=r.raiz,
                    encargo_solicitante=r.encargo_solicitante,
                    directa=r.directa,
                    subencargo=r.subencargo,
                    duracion_ms=r.duracion_ms,
                )
                for r in self._registros
            ]

    # ------------------------------------------------------------------ the request

    def _pedir(
        self, linea: LineaPizarra, solicitante: str, destinatario: str, dato: str, motivo: str
    ) -> RespuestaPeticion:
        inicio = time.monotonic()
        dato, motivo = _limpio(dato, MAX_DATO), _limpio(motivo, MAX_DATO)
        destino = str(destinatario or "").strip().lower()
        profundidad = linea.profundidad + 1
        clave = (destino, _normal(dato))
        yo = threading.get_ident()
        decidida: RespuestaPeticion | None = None
        vuelo: _EnVuelo | None = None
        evento: threading.Event | None = None
        sub_id: str | None = None
        with self._lock:
            if linea.token.cancelada:
                self.descartadas += 1
                return RespuestaPeticion(peticion_id="", estado="limite", texto="Tu encargo se ha cancelado.")
            if self._contador >= self.max_peticiones or profundidad > MAX_PROFUNDIDAD_PETICIONES:
                self.descartadas += 1
                return RespuestaPeticion(
                    peticion_id="",
                    estado="limite",
                    texto=f"No se pueden hacer más peticiones entre especialistas en esta consulta "
                    f"(máximo {self.max_peticiones}, hasta {self.max_profundidad} nivel(es)).",
                )
            self._contador += 1
            peticion = PeticionDato(
                id=f"p{self._contador}",
                solicitante=linea.clave,
                destinatario=destino if _CLAVE_RE.fullmatch(destino) else "desconocido",
                dato=dato or "(vacío)",
                motivo=motivo,
                profundidad=profundidad,
            )
            rechazo = self._rechazo(linea, solicitante, destino, dato, profundidad)
            if rechazo is not None:
                decidida = RespuestaPeticion(peticion_id=peticion.id, **rechazo)
            elif (hecha := self._hechas.get(clave)) is not None:
                decidida = self._reusar(hecha, peticion.id)
            elif (cubierta := self._cubierta(destino, dato, peticion.id)) is not None:
                self._hechas[clave] = cubierta
                decidida = cubierta
            else:
                vuelo = self._en_vuelo.get(clave)
                if vuelo is not None and not self._bloquearia(vuelo, yo):
                    evento = threading.Event()
                    vuelo.esperando.append(evento)
                    self._bloqueos[yo] = clave
                    linea.token.avisar(evento)
                else:
                    # Run it here; registered as in flight unless an identical one already is (and waiting for
                    # it would deadlock): then this run is an unregistered duplicate.
                    vuelo, sub_id = self._reservar(clave, yo)
                    if sub_id is None:
                        decidida = RespuestaPeticion(
                            peticion_id=peticion.id, estado="limite", texto="No quedan encargos libres."
                        )
            if decidida is not None:
                self._anotar(linea, peticion, decidida, inicio, None)
        if decidida is not None:
            self._emitir(linea, peticion, decidida.estado, reutilizada=decidida.reutilizada)
            return decidida
        self._emitir(linea, peticion, "start")
        if evento is not None:
            assert vuelo is not None
            evento.wait()
            with self._lock:
                self._bloqueos.pop(yo, None)
                linea.token.olvidar(evento)
                obtenida = vuelo.respuesta
                if obtenida is None and not linea.token.cancelada:
                    # The first run was abandoned (its encargo was cancelled): run it here.
                    vuelo, sub_id = self._reservar(clave, yo)
            if obtenida is not None:
                return self._cerrar_fuera(linea, peticion, self._reusar(obtenida, peticion.id), inicio)
            if sub_id is None:
                texto = "Tu encargo se ha cancelado." if linea.token.cancelada else "No quedan encargos libres."
                return self._cerrar_fuera(
                    linea, peticion, RespuestaPeticion(peticion_id=peticion.id, estado="limite", texto=texto), inicio
                )
        assert sub_id is not None
        return self._ejecutar_peticion(linea, peticion, clave, inicio, sub_id=sub_id, vuelo=vuelo)

    def _reservar(self, clave: tuple[str, str], yo: int) -> tuple[_EnVuelo | None, str | None]:
        """A sub-encargo id and, when none is in flight for ``clave``, the in-flight record. (Under the lock.)"""
        sub_id = self._sub_id()
        if sub_id is None:
            return None, None
        if clave in self._en_vuelo:
            return None, sub_id
        vuelo = _EnVuelo(dueno=yo)
        self._en_vuelo[clave] = vuelo
        return vuelo, sub_id

    def _rechazo(
        self, linea: LineaPizarra, solicitante: str, destino: str, dato: str, profundidad: int
    ) -> dict[str, Any] | None:
        if solicitante != linea.clave:
            return {"estado": "denegada", "texto": "Solo puedes pedir datos en tu propio nombre."}
        if not dato:
            return {"estado": "denegada", "texto": "Falta el dato que necesitas."}
        if destino == linea.clave:
            return {"estado": "denegada", "texto": "No puedes pedirte un dato a ti mismo: usa tus herramientas."}
        if destino not in self.especialistas:
            disponibles = ", ".join(k for k in self.especialistas if k not in linea.cadena) or "ninguno"
            return {
                "estado": "denegada",
                "texto": f"«{destino or '(vacío)'}» no es un especialista activo en esta consulta. Puedes pedir a: "
                f"{disponibles}.",
            }
        if destino in linea.cadena:
            return {
                "estado": "denegada",
                "texto": f"Esa petición cerraría un círculo ({' → '.join((*linea.cadena, destino))}): responde con "
                "lo que tienes y di qué falta.",
            }
        if profundidad > self.max_profundidad:
            return {
                "estado": "limite",
                "texto": "Esta petición ya viene de otra: no se puede encadenar otra más. Responde con lo que tienes.",
            }
        if self.estado.agotado:
            return {"estado": "limite", "texto": "Se ha agotado el presupuesto de modelo de esta pregunta."}
        return None

    def _bloquearia(self, vuelo: _EnVuelo, yo: int) -> bool:
        """Would waiting for ``vuelo`` close a cycle in the wait-for graph? (Called under the lock.)"""
        hilo = vuelo.dueno
        vistos: set[int] = set()
        while hilo not in vistos:
            if hilo == yo:
                return True
            vistos.add(hilo)
            espera = self._bloqueos.get(hilo)
            siguiente = self._en_vuelo.get(espera) if espera is not None else None
            if siguiente is None:
                return False
            hilo = siguiente.dueno
        return False

    def _cubierta(self, destino: str, dato: str, peticion_id: str) -> RespuestaPeticion | None:
        """A finished report of ``destino`` whose figure covers ``dato`` (unambiguously), as an answer."""
        buscadas = _tokens(dato)
        if not buscadas:
            return None
        anio = _anio(dato)
        mejores: list[tuple[float, InformeEspecialista, Cifra]] = []
        for informe in self._informes:
            if informe.especialista != destino or informe.estado != "ok":
                continue
            for cifra in informe.cifras:
                if cifra.especialista != destino or (anio and cifra.ejercicio and cifra.ejercicio != anio):
                    continue
                concepto = _tokens(cifra.concepto)
                comunes = sum(1 for t in buscadas if any(_casan(t, c) for c in concepto))
                cobertura = comunes / len(buscadas)
                if cobertura >= UMBRAL_COBERTURA and comunes >= min(2, len(buscadas)):
                    mejores.append((cobertura, informe, cifra))
        if not mejores:
            return None
        tope = max(c for c, _i, _f in mejores)
        elegidas = [(i, c) for cob, i, c in mejores if cob == tope]
        if len({(c.valor, c.unidad) for _i, c in elegidas}) != 1:
            return None  # ambiguous: two different figures answer it equally well
        informe, cifra = elegidas[0]
        nombre = str(getattr(self.especialistas.get(destino), "nombre", destino))
        valor = cifra.literal or formatear_valor(cifra.valor, cifra.unidad)
        fuente = f" [{cifra.fragment_id}]" if cifra.fragment_id else ""
        return RespuestaPeticion(
            peticion_id=peticion_id,
            estado="ok",
            texto=_limpio(
                f"Dato del informe que {nombre} ya ha hecho en esta consulta ({informe.encargo_id}): "
                f"{cifra.concepto}: {valor}{fuente}.",
                MAX_TEXTO_RESPUESTA,
            ),
            cifras=[cifra],
            fragment_ids=[cifra.fragment_id] if cifra.fragment_id else [],
            reutilizada=True,
        )

    def _reusar(self, respuesta: RespuestaPeticion, peticion_id: str) -> RespuestaPeticion:
        return respuesta.model_copy(update={"peticion_id": peticion_id, "reutilizada": True})

    def _sub_id(self) -> str | None:
        for n in range(99, -1, -1):
            candidato = f"e{n}"
            if candidato not in self._ids_usados:
                self._ids_usados.add(candidato)
                return candidato
        return None

    def _ejecutar_peticion(
        self,
        linea: LineaPizarra,
        peticion: PeticionDato,
        clave: tuple[str, str],
        inicio: float,
        *,
        sub_id: str,
        vuelo: _EnVuelo | None,
    ) -> RespuestaPeticion:
        destino = peticion.destinatario
        nombre = str(getattr(self.especialistas.get(linea.clave), "nombre", linea.clave))
        pregunta = f"Otro especialista del hogar ({nombre}) te pide, a través del asistente central, este dato: {peticion.dato}."
        if peticion.motivo:
            pregunta += f" Lo necesita para: {peticion.motivo}."
        pregunta += (
            " Responde solo con ese dato: la cifra con su [F#] y de qué cálculo sale. Si tus herramientas no lo dan, "
            "dilo; no lo estimes."
        )
        encargo = Encargo(id=sub_id, especialista=destino, pregunta=pregunta, motivo=f"petición {peticion.id}")
        hija = LineaPizarra(
            self,
            clave=destino,
            turno=linea.turno,
            cadena=(*linea.cadena, destino),
            profundidad=peticion.profundidad,
            token=linea.token,
            encargo_id=sub_id,
            raiz=linea.raiz,
        )
        contexto = ContextoEncargo(
            turno=linea.turno, encargo=encargo, pizarra=hija, previos=(), profundidad=peticion.profundidad
        )
        informe: InformeEspecialista | None = None
        try:
            informe = self._ejecutar(self.especialistas[destino], contexto)
        finally:
            respuesta = self._respuesta_de(peticion.id, informe) if informe is not None else None
            with self._lock:
                vigente = not linea.token.cancelada
                if vuelo is not None:
                    vuelo.respuesta = respuesta if vigente else None
                    vuelo.terminado = True
                    self._en_vuelo.pop(clave, None)
                    despertar, vuelo.esperando = vuelo.esperando, []
                else:
                    despertar = []
                if vigente and informe is not None and respuesta is not None:
                    self._subinformes.append(informe)
                    self._informes.append(informe)
                    if clave not in self._hechas or self._hechas[clave].estado != "ok":
                        self._hechas[clave] = respuesta
            for evento in despertar:
                evento.set()
        assert respuesta is not None  # informe is always set when no exception propagated
        return self._cerrar_fuera(linea, peticion, respuesta, inicio, subencargo=sub_id)

    def _respuesta_de(self, peticion_id: str, informe: InformeEspecialista) -> RespuestaPeticion:
        if informe.estado == "ok" and informe.texto.strip():
            texto = _limpio_largo(informe.texto)
            return RespuestaPeticion(
                peticion_id=peticion_id,
                estado="ok",
                texto=texto,
                cifras=list(informe.cifras),
                fragment_ids=list(informe.fragment_ids),
            )
        motivo = "; ".join(informe.avisos) or "no ha podido responder"
        return RespuestaPeticion(
            peticion_id=peticion_id,
            estado="sin_dato",
            texto=_limpio(f"{informe.nombre} no ha podido dar ese dato ({motivo}).", MAX_TEXTO_RESPUESTA),
        )

    # ------------------------------------------------------------------ audit

    def _cerrar_fuera(
        self,
        linea: LineaPizarra,
        peticion: PeticionDato,
        respuesta: RespuestaPeticion,
        inicio: float,
        subencargo: str | None = None,
    ) -> RespuestaPeticion:
        with self._lock:
            self._anotar(linea, peticion, respuesta, inicio, subencargo)
        self._emitir(linea, peticion, respuesta.estado, reutilizada=respuesta.reutilizada)
        return respuesta

    def _anotar(
        self,
        linea: LineaPizarra,
        peticion: PeticionDato,
        respuesta: RespuestaPeticion,
        inicio: float,
        subencargo: str | None,
    ) -> None:
        if linea.token.cancelada:
            return  # the encargo was given up: nothing it does afterwards is recorded
        linea._propias.append(peticion)
        self._registros.append(
            RegistroPeticion(
                peticion=peticion,
                respuesta=respuesta,
                raiz=linea.raiz,
                encargo_solicitante=linea.encargo_id,
                directa=linea.profundidad == 0,
                subencargo=subencargo,
                duracion_ms=max(0, int((time.monotonic() - inicio) * 1000)),
            )
        )

    def _emitir(self, linea: LineaPizarra, peticion: PeticionDato, estado: str, *, reutilizada: bool = False) -> None:
        # The line's emitter is the encargo's (serialised, quiet once cancelled). A callback failing must not
        # break the request: the dispatcher's emitter already swallows it; a bare one is guarded here.
        try:
            linea.turno.emit(
                EVENTO_PETICION,
                peticion=peticion.model_dump(mode="json"),
                estado=estado,
                reutilizada=reutilizada,
            )
        except Exception:  # pragma: no cover - the dispatcher's emitter never raises
            pass


def _limpio_largo(texto: str) -> str:
    limpio = neutralizar(texto.strip())
    return limpio if len(limpio) <= MAX_TEXTO_RESPUESTA else limpio[: MAX_TEXTO_RESPUESTA - 1] + "…"


def peticiones_de(registros: Sequence[RegistroPeticion]) -> list[PeticionDato]:
    return [r.peticion for r in registros]


__all__ = [
    "MAX_DATO",
    "UMBRAL_COBERTURA",
    "Ejecutor",
    "LineaPizarra",
    "Pizarra",
    "RegistroPeticion",
    "peticiones_de",
]
