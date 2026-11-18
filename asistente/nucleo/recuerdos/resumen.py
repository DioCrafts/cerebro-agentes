"""Summarising a conversation: the model proposes, a deterministic gate decides what is remembered.

The model (purpose ``memoria:resumen``, a small tier) reads one finished conversation and proposes a title, a
short summary and candidate items (``dato`` / ``preferencia`` / ``contexto``), each with ``cita``: the user's
literal words. THE GATE (:func:`puerta`) keeps a candidate only if:

1. its quote is in one of THIS conversation's USER messages — in the user's own words, not in something they
   pasted (``privacidad.quitar_pegado``) — with the same normalisation and minimum length as the quote of
   ``registrar_decision`` (``agent/procedencia.cita_valida``). What only the assistant said, or a document, an
   e-mail or a tool result, never becomes «something you told me»;
2. every figure in its text is one the user wrote (``procedencia.numeros_del_usuario``): the memory never
   carries a number the assistant computed;
3. it carries no credential (dropped), and its identifiers are masked;
4. it is not special-category data (health, religion…), unless the user asked to remember it in that very
   message — then it is kept and flagged ``sensible``;
5. it is not injection bait.

The summary and the title are free text: injection bait is neutralised, identifiers masked, and every sentence
with a secret, special-category data or a figure the user did not write is dropped.

:func:`aplicar` then merges the accepted items into the owner's memory: the same normalised ``clave`` with the
same content is a duplicate (one more source), with a different content the newer words supersede the older
item (kept as history, ``sustituido_por``); the user's correction wins over older words; a message the user
made the memory forget is never learnt from again; the number of active items is capped.

Batch mode (:func:`enviar_en_lote` / :func:`recoger_lotes`): the requests go through the provider's batch API
(cheaper); only ids and message counts are stored while they wait, never text, and a result is applied only if
the conversation is still there, unchanged, and the user has not paused the memory.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import logging
import re
from typing import Any

from asistente.nucleo.agent.procedencia import cita_valida, numeros_del_usuario
from asistente.nucleo.memoria.modelo import normalizar_texto
from asistente.nucleo.recuerdos import privacidad, prompts
from asistente.nucleo.recuerdos.almacen import (
    TIPOS_GUARDADOS,
    AlmacenRecuerdos,
    Conversacion,
    FilaLote,
    Fuente,
    Mensaje,
    Recuerdo,
    Titular,
    en_utc,
    fecha_local,
    nuevo_id,
)
from asistente.nucleo.recuerdos.recuperacion import Incrustador, analizar

logger = logging.getLogger(__name__)

MAX_TEXTO = 300
MAX_CITA = 300
MAX_RESUMEN = 600
MAX_TITULO = 80
MAX_CARACTERES_PROMPT = 40_000
MAX_EXISTENTES = 60
SIMILITUD_DUPLICADO = 0.8
# A batch that cannot even be queried for this long is given up (its conversations go back to the queue).
ABANDONO_LOTE = timedelta(hours=48)

_NUMERO = re.compile(r"(?<![\w])\d+(?:[.,   ]\d+)*(?:\s*(?:k|mil|millon(?:es)?)\b)?", re.IGNORECASE)
_NUMEROS_EN_LETRA = frozenset(
    "dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis diecisiete "
    "dieciocho diecinueve veinte".split()
)
_FRASE = re.compile(r"(?<=[.!?…])\s+")
_CLAVE = re.compile(r"[^a-z0-9.]+")


@dataclass(frozen=True)
class EntradaResumen:
    """What the summariser reads for one conversation of one owner."""

    titular: Titular
    conversacion: Conversacion
    mensajes: Sequence[Mensaje]
    existentes: Sequence[Recuerdo] = ()

    @property
    def cubiertos(self) -> int:
        """How many messages this summary covers (all of them, up to the last retained one)."""
        return max((m.n for m in self.mensajes), default=self.conversacion.mensajes)

    @property
    def del_usuario(self) -> list[Mensaje]:
        return [m for m in self.mensajes if m.rol == "user"]


@dataclass(frozen=True)
class Aceptado:
    tipo: str
    clave: str
    texto: str
    cita: str
    n: int
    momento: datetime
    sensible: bool = False

    @property
    def fecha(self) -> date:
        return fecha_local(self.momento)


@dataclass(frozen=True)
class Rechazo:
    motivo: str  # sin_cita | cifra_ajena | credencial | inyeccion | categoria_especial:<c> | invalido | repetido
    clave: str = ""


@dataclass
class ResultadoPuerta:
    titulo: str
    resumen: str | None
    aceptados: list[Aceptado] = field(default_factory=list)
    rechazos: list[Rechazo] = field(default_factory=list)


def preparar(store: AlmacenRecuerdos, titular: Titular, conversacion: Conversacion) -> EntradaResumen:
    return EntradaResumen(
        titular=titular,
        conversacion=conversacion,
        mensajes=store.mensajes(titular, conversacion.id),
        existentes=store.recuerdos(titular)[:MAX_EXISTENTES],
    )


def peticion(entrada: EntradaResumen) -> tuple[str, str]:
    """(system, user) of the summariser for ``entrada``: the newest messages that fit, as data."""
    elegidos: list[tuple[int, str, str]] = []
    total = 0
    for m in reversed(entrada.mensajes):
        texto = privacidad.neutralizar(m.texto)
        total += len(texto)
        if elegidos and total > MAX_CARACTERES_PROMPT:
            break
        elegidos.append((m.n, m.rol, texto))
    elegidos.reverse()
    primero = elegidos[0][0] if elegidos else 0
    previo = entrada.conversacion.resumen if primero > 1 else None
    return prompts.prompt_resumen(), prompts.mensaje_usuario(
        elegidos,
        resumen_previo=previo,
        existentes=[(r.clave, r.texto) for r in entrada.existentes],
    )


# --------------------------------------------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------------------------------------------


def normalizar_clave(clave: str, texto: str) -> str:
    """``hipoteca.banco`` style: lower-case ASCII, dots and underscores, at most 60 characters."""
    plano = normalizar_texto(clave.replace(".", " punto ")).replace(" punto ", ".")
    plano = _CLAVE.sub("_", plano.replace(" ", "_")).strip("._")
    plano = re.sub(r"_+", "_", re.sub(r"\.+", ".", plano))[:60].strip("._")
    if plano:
        return plano
    return "_".join(analizar(texto)[:3]) or "general"


def _cifras_ajenas(texto: str, numeros_usuario: set[Decimal]) -> list[str]:
    """The figures of ``texto`` the user never wrote (in any of their spellings)."""
    ajenas: list[str] = []
    for m in _NUMERO.finditer(texto):
        if not numeros_del_usuario([m.group(0)]) & numeros_usuario:
            ajenas.append(m.group(0))
    for palabra in normalizar_texto(texto).split():
        if palabra in _NUMEROS_EN_LETRA and not numeros_del_usuario([palabra]) & numeros_usuario:
            ajenas.append(palabra)
    return ajenas


def _mensaje_de_la_cita(cita: str, entrada: EntradaResumen) -> Mensaje | None:
    """The (latest) user message whose OWN words contain ``cita``."""
    for mensaje in reversed(entrada.del_usuario):
        if cita_valida(cita, [privacidad.quitar_pegado(mensaje.texto)]):
            return mensaje
    return None


def _limpia_cita(cita: str) -> str:
    return " ".join(cita.strip().strip("«»\"“”'").split())


def puerta_candidato(
    candidato: dict[str, Any], entrada: EntradaResumen, numeros_usuario: set[Decimal]
) -> Aceptado | Rechazo:
    """One candidate through the gate (see the module docstring for the WHY of each rule)."""
    tipo = str(candidato.get("tipo") or "").strip().lower()
    texto = " ".join(str(candidato.get("texto") or "").split())
    cita = _limpia_cita(str(candidato.get("cita") or ""))
    clave = normalizar_clave(str(candidato.get("clave") or ""), texto)
    if tipo not in TIPOS_GUARDADOS or not texto or not cita or len(texto) > MAX_TEXTO or len(cita) > MAX_CITA:
        return Rechazo("invalido", clave)
    if privacidad.es_inyeccion(texto) or privacidad.es_inyeccion(cita):
        return Rechazo("inyeccion", clave)
    if privacidad.tiene_credencial(texto) or privacidad.tiene_credencial(cita):
        return Rechazo("credencial", clave)
    mensaje = _mensaje_de_la_cita(cita, entrada)
    if mensaje is None:
        return Rechazo("sin_cita", clave)
    if _cifras_ajenas(texto, numeros_usuario):
        return Rechazo("cifra_ajena", clave)
    sensible = False
    categoria = privacidad.categoria_especial(f"{texto} {cita}")
    if categoria is not None:
        if not privacidad.pidio_recordar(cita, [privacidad.quitar_pegado(mensaje.texto)]):
            return Rechazo(f"categoria_especial:{categoria}", clave)
        sensible = True
    return Aceptado(
        tipo=tipo,
        clave=clave,
        texto=privacidad.limpiar(texto),
        cita=privacidad.limpiar(cita),
        n=mensaje.n,
        momento=en_utc(mensaje.fecha),
        sensible=sensible,
    )


def _texto_libre(texto: str, numeros_usuario: set[Decimal], maximo: int) -> str:
    """A summary or a title, made safe: neutralised, masked, and without any sentence that carries a secret,
    special-category data or a figure the user did not write."""
    limpio = " ".join(privacidad.limpiar(privacidad.neutralizar(texto)).split())
    frases = [
        f
        for f in _FRASE.split(limpio)
        if f
        and privacidad.SECRETO not in f
        and privacidad.TAPA_INYECCION not in f
        and privacidad.categoria_especial(f) is None
        and not _cifras_ajenas(f, numeros_usuario)
    ]
    salida = " ".join(frases).strip()
    if len(salida) > maximo:
        salida = salida[: maximo - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return salida


def puerta(datos: dict[str, Any] | None, entrada: EntradaResumen) -> ResultadoPuerta:
    """The model's output through the gate. Never raises: an unusable output keeps the provisional title."""
    datos = datos if isinstance(datos, dict) else {}
    propias = privacidad.palabras_propias(m.texto for m in entrada.del_usuario)
    numeros = numeros_del_usuario(propias)
    titulo = _texto_libre(str(datos.get("titulo") or ""), numeros, MAX_TITULO).rstrip(".") or (
        entrada.conversacion.titulo
    )
    resumen = _texto_libre(str(datos.get("resumen") or ""), numeros, MAX_RESUMEN) or None
    resultado = ResultadoPuerta(titulo=titulo, resumen=resumen)
    vistos: set[tuple[str, str]] = set()
    claves: set[str] = set()
    candidatos = datos.get("recuerdos")
    for candidato in (candidatos if isinstance(candidatos, list) else [])[: prompts.MAX_CANDIDATOS]:
        if not isinstance(candidato, dict):
            resultado.rechazos.append(Rechazo("invalido"))
            continue
        veredicto = puerta_candidato(candidato, entrada, numeros)
        if isinstance(veredicto, Rechazo):
            resultado.rechazos.append(veredicto)
            continue
        firma = (normalizar_texto(veredicto.texto), normalizar_texto(veredicto.cita))
        if firma in vistos or veredicto.clave in claves:
            resultado.rechazos.append(Rechazo("repetido", veredicto.clave))
            continue
        vistos.add(firma)
        claves.add(veredicto.clave)
        resultado.aceptados.append(veredicto)
    return resultado


def resumir(proveedor: Any, entrada: EntradaResumen) -> ResultadoPuerta:
    """One synchronous model call, then the gate (the call's errors propagate to the caller)."""
    system, user = peticion(entrada)
    datos = proveedor.complete_json(
        system=system, user=user, schema=prompts.ESQUEMA_RESUMEN, schema_name=prompts.NOMBRE_ESQUEMA
    )
    return puerta(datos, entrada)


# --------------------------------------------------------------------------------------------------------------
# Merging into the memory
# --------------------------------------------------------------------------------------------------------------


def parecidos(a: str, b: str) -> bool:
    """Same content in other words (term Jaccard ≥ 0.8, or one normalised text inside the other)."""
    na, nb = normalizar_texto(a), normalizar_texto(b)
    if na == nb or (na and nb and (na in nb or nb in na)):
        return True
    ta, tb = set(analizar(a)), set(analizar(b))
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= SIMILITUD_DUPLICADO


def _vectores(incrustador: Incrustador | None, textos: dict[str, str]) -> dict[str, list[float]]:
    if incrustador is None or not textos:
        return {}
    try:
        ids = list(textos)
        return dict(zip(ids, incrustador.embed_documents([textos[i] for i in ids]), strict=True))
    except Exception:
        logger.warning("Could not embed %d memory items; lexical ranking only.", len(textos), exc_info=True)
        return {}


def aplicar(
    store: AlmacenRecuerdos,
    entrada: EntradaResumen,
    resultado: ResultadoPuerta,
    *,
    momento: datetime,
    max_recuerdos: int,
    incrustador: Incrustador | None = None,
) -> Counter[str]:
    """Store the summary and merge the accepted items into the owner's memory. Returns what happened."""
    titular = entrada.titular
    conversacion = entrada.conversacion.id
    informe: Counter[str] = Counter()
    activos = store.recuerdos(titular)
    conocidas = {
        (f.conversacion_id, f.n, normalizar_texto(f.cita))
        for r in store.recuerdos(titular, estados=("activo", "sustituido"))
        for f in r.fuentes
    }
    bloqueados = store.bloqueados(titular)
    cambios: dict[str, Recuerdo] = {}

    for a in resultado.aceptados:
        if (conversacion, a.n) in bloqueados:
            informe["olvidado_por_el_usuario"] += 1
            continue
        if (conversacion, a.n, normalizar_texto(a.cita)) in conocidas:
            informe["ya_sabido"] += 1
            continue
        fuente = Fuente(conversacion_id=conversacion, fecha=a.fecha, cita=a.cita, n=a.n)
        mismo = next((r for r in activos if r.clave == a.clave), None)
        if mismo is None:
            mismo = next((r for r in activos if r.tipo == a.tipo and parecidos(r.texto, a.texto)), None)
        if mismo is not None and parecidos(mismo.texto, a.texto):
            # A duplicate: one more source; the newest one is the origin shown.
            origen = fuente if (fuente.fecha, fuente.n) >= (mismo.origen.fecha, mismo.origen.n) else mismo.origen
            fusionado = mismo.model_copy(
                update={
                    "fuentes": [*mismo.fuentes, fuente],
                    "origen": origen,
                    "actualizado": momento,
                    "sensible": mismo.sensible or a.sensible,
                }
            )
            activos = [fusionado if r.id == mismo.id else r for r in activos]
            cambios[fusionado.id] = fusionado
            informe["fusionado"] += 1
            continue
        if mismo is not None:
            # Contradiction: the newer words supersede; the user's correction counts from when they made it.
            if mismo.corregido and en_utc(a.momento) <= en_utc(mismo.actualizado):
                informe["corregido_mas_reciente"] += 1
                continue
            if a.fecha < mismo.origen.fecha:
                informe["mas_antiguo"] += 1
                continue
        nuevo = Recuerdo(
            id=nuevo_id(),
            tipo=a.tipo,  # type: ignore[arg-type]
            clave=a.clave,
            texto=a.texto,
            origen=fuente,
            fuentes=[fuente],
            creado=momento,
            actualizado=momento,
            sensible=a.sensible,
        )
        if mismo is not None:
            viejo = mismo.model_copy(
                update={"estado": "sustituido", "sustituido_por": nuevo.id, "actualizado": momento}
            )
            cambios[viejo.id] = viejo
            activos = [r for r in activos if r.id != mismo.id]
            informe["sustituido"] += 1
        else:
            informe["nuevo"] += 1
        activos.append(nuevo)
        cambios[nuevo.id] = nuevo

    vectores = _vectores(incrustador, {i: r.texto for i, r in cambios.items() if r.estado == "activo"})
    if cambios:
        store.guardar(titular, *cambios.values(), vectores=vectores)
    resumen_vector = _vectores(incrustador, {"c": f"{resultado.titulo}. {resultado.resumen or ''}"}).get("c")
    store.guardar_resumen(
        titular,
        conversacion,
        titulo=resultado.titulo,
        resumen=resultado.resumen,
        cita=cita_de_conversacion(entrada),
        cubiertos=entrada.cubiertos,
        momento=momento,
        vector=resumen_vector,
    )
    informe["recortado"] += recortar(store, titular, max_recuerdos)
    for rechazo in resultado.rechazos:
        informe[f"rechazo:{rechazo.motivo.split(':')[0]}"] += 1
    return informe


def cita_de_conversacion(entrada: EntradaResumen) -> str | None:
    """The user's words a summary rests on: their first retained question that is usable as a quote (own words,
    no injection bait, no special-category data), cut at a word boundary. None keeps the stored one."""
    if entrada.conversacion.cita:
        return None
    for mensaje in entrada.del_usuario:
        propias = " ".join(privacidad.quitar_pegado(mensaje.texto).split())
        if privacidad.SECRETO in propias:
            propias = propias.split(privacidad.SECRETO, 1)[0].rstrip(" ,;:") + " …"
        if len(propias) < 10 or privacidad.es_inyeccion(propias) or privacidad.categoria_especial(propias) is not None:
            continue
        if len(propias) > MAX_CITA:
            propias = propias[: MAX_CITA - 1].rsplit(" ", 1)[0] + "…"
        return propias
    return None


def recortar(store: AlmacenRecuerdos, titular: Titular, max_recuerdos: int) -> int:
    """Keep at most ``max_recuerdos`` active items (context first, then the least recently used; the user's
    corrections last) and as many superseded ones (oldest first)."""
    maximo = max(1, max_recuerdos)
    activos = store.recuerdos(titular)
    sobran = len(activos) - maximo
    victimas: list[str] = []
    if sobran > 0:
        orden = sorted(
            activos,
            key=lambda r: (r.corregido, r.tipo != "contexto", en_utc(r.ultimo_uso or r.actualizado), r.actualizado),
        )
        victimas.extend(r.id for r in orden[:sobran])
    historia = store.recuerdos(titular, estados=("sustituido",))
    if len(historia) > maximo:
        victimas.extend(r.id for r in sorted(historia, key=lambda r: en_utc(r.actualizado))[: len(historia) - maximo])
    return store.eliminar(titular, victimas) if victimas else 0


# --------------------------------------------------------------------------------------------------------------
# Batch mode
# --------------------------------------------------------------------------------------------------------------


def nativo_de(proveedor: Any) -> Any:
    """The provider that serves the purpose (after routing): the one whose batch API is used."""
    return getattr(proveedor, "wrapped", proveedor)


def admite_lotes(proveedor: Any) -> bool:
    from asistente.nucleo.agent.providers.lotes import admite_lotes as _admite

    return bool(_admite(proveedor))


def _custom_id(entrada: EntradaResumen, momento: datetime) -> str:
    base = f"{entrada.titular.case_id}|{entrada.titular.user_id}|{entrada.conversacion.id}|{entrada.cubiertos}|"
    return "mem_" + hashlib.sha256((base + momento.isoformat()).encode()).hexdigest()[:40]


def enviar_en_lote(
    store: AlmacenRecuerdos, proveedor: Any, entradas: Sequence[EntradaResumen], *, momento: datetime
) -> str | None:
    """Submit the summaries of ``entradas`` as ONE batch. Returns its id (None when there is nothing to send)."""
    if not entradas:
        return None
    nativo = nativo_de(proveedor)
    peticiones: list[tuple[str, dict[str, Any]]] = []
    filas: list[FilaLote] = []
    for entrada in entradas:
        system, user = peticion(entrada)
        params = nativo.peticion_json(
            system=system, user=user, schema=prompts.ESQUEMA_RESUMEN, schema_name=prompts.NOMBRE_ESQUEMA
        )
        custom_id = _custom_id(entrada, momento)
        peticiones.append((custom_id, params))
        filas.append(
            FilaLote(
                custom_id=custom_id,
                lote_id="",
                proveedor=str(getattr(nativo, "name", "")),
                modelo=str(getattr(nativo, "model", "")),
                titular=entrada.titular,
                conversacion_id=entrada.conversacion.id,
                mensajes=entrada.cubiertos,
                enviado_el=momento,
            )
        )
    lote_id = str(nativo.crear_lote(peticiones))
    store.guardar_lote([replace(f, lote_id=lote_id) for f in filas])
    return lote_id


def recoger_lotes(
    store: AlmacenRecuerdos,
    proveedor: Any,
    *,
    momento: datetime,
    procesar: Callable[[FilaLote, dict[str, Any]], bool],
    registrar_uso: Callable[[FilaLote, Any], None] | None = None,
    max_intentos: int = 3,
) -> Counter[str]:
    """Collect every finished batch: each result goes to ``procesar(fila, datos)`` (which checks the
    conversation is still there and unchanged, then gates and applies it; False puts it back in the queue)."""
    informe: Counter[str] = Counter()
    nativo = nativo_de(proveedor)
    for lote_id, filas in store.lotes_en_curso().items():
        try:
            estado = nativo.estado_lote(lote_id)
        except Exception:
            logger.warning("Could not query memory batch %s.", lote_id, exc_info=True)
            if momento - min(f.enviado_el for f in filas) > ABANDONO_LOTE:
                for fila in filas:
                    store.quitar_de_lote(fila, reabrir=True)
                informe["lote_abandonado"] += 1
            continue
        if not estado.terminado:
            informe["lote_en_proceso"] += 1
            continue
        try:
            resultados = {r.custom_id: r for r in nativo.resultados_lote(lote_id)}
        except Exception:
            logger.warning("Could not read the results of memory batch %s.", lote_id, exc_info=True)
            continue
        for fila in filas:
            resultado = resultados.get(fila.custom_id)
            if resultado is not None and resultado.usage is not None and registrar_uso is not None:
                registrar_uso(fila, resultado.usage)
            if resultado is None or resultado.error is not None or resultado.datos is None:
                store.quitar_de_lote(fila, reabrir=True)
                if resultado is None or not resultado.reintentable:
                    store.fallo_de_resumen(fila.titular, fila.conversacion_id, max_intentos=max_intentos)
                informe["resultado_con_error"] += 1
                continue
            try:
                aplicado = procesar(fila, resultado.datos)
            except Exception:
                logger.exception("Could not apply the summary of a memory batch result (%s).", fila.custom_id)
                aplicado = False
            store.quitar_de_lote(fila, reabrir=not aplicado)
            informe["aplicado" if aplicado else "descartado"] += 1
    return informe


__all__ = [
    "Aceptado",
    "EntradaResumen",
    "Rechazo",
    "ResultadoPuerta",
    "admite_lotes",
    "aplicar",
    "cita_de_conversacion",
    "enviar_en_lote",
    "nativo_de",
    "normalizar_clave",
    "parecidos",
    "peticion",
    "preparar",
    "puerta",
    "puerta_candidato",
    "recoger_lotes",
    "recortar",
    "resumir",
]
