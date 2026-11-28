"""El buscador del corpus que usa el agente, sobre ``IndiceHibrido`` (un fichero SQLite).

Dos etapas. La primera trae ``first_stage_k`` candidatos: con ``BM25_ENABLED`` es híbrida, una fusión RRF del
ranking denso (``sqlite-vec``) y del léxico (FTS5) con los pesos ``BM25_DENSE_WEIGHT`` / ``BM25_BM25_WEIGHT``;
sin él, solo densa. La segunda, si hay reranker, los reordena y se queda con los ``k`` primeros. El texto
recuperado se sanea antes de devolverlo, porque es entrada no fiable para el modelo.

Entre las dos etapas va la vigencia. Los artículos indexados por bloques del BOE llevan desde cuándo rige su
texto y, si ya no rige, hasta cuándo. Sin fecha, la búsqueda devuelve solo lo que está en vigor hoy. Con
``filters.vigente_en`` devuelve la ley tal como estaba ese día: el fragmento de un artículo reformado después se
cambia por la versión que regía entonces, leída de la tabla de preceptos del índice, y el de un artículo que aún
no existía se descarta.

El buscador da también la lectura por identificador que usan las herramientas del agente: un artículo entero en
una fecha (``leer_precepto``), el índice de una norma (``indice_de_norma``) y las normas disponibles (``normas``).
"""

from __future__ import annotations

import logging
from pathlib import Path
import threading
from typing import Any

from asistente.conocimiento.fragmento import FragmentoRecuperado
from asistente.conocimiento.indice.almacen import Acierto, IndiceHibrido, NormaIndexada, Precepto

logger = logging.getLogger(__name__)

# Con filtros, el filtro de metadatos va después del KNN: hace falta una reserva honda.
PROFUNDIDAD_CON_FILTRO = 500
# Cuánto texto de una versión histórica se devuelve como fragmento de búsqueda (el artículo entero se lee con
# ``leer_precepto``).
MAX_CARACTERES_HISTORICO = 2400
# Cuántos fragmentos del mismo artículo caben entre los resultados de una búsqueda. Un artículo largo tiene muchos
# fragmentos parecidos y, sin tope, se disputan los primeros puestos entre ellos; con uno, cada puesto es un
# artículo distinto y el resto del artículo se lee con ``leer_articulo``. 0: sin tope.
MAX_FRAGMENTOS_POR_ARTICULO = 1
# Cuántos candidatos de cada ranking (denso y léxico) entran en la fusión RRF, como mínimo.
PROFUNDIDAD_FUSION = 60


class SqliteCorpusSearcher:
    def __init__(
        self,
        indice: IndiceHibrido,
        embeddings: Any,
        *,
        reranker: Any | None = None,
        first_stage_k: int = 30,
        hibrido: bool = False,
        peso_denso: float = 1.0,
        peso_lexico: float = 1.0,
        max_por_articulo: int = MAX_FRAGMENTOS_POR_ARTICULO,
    ) -> None:
        self._max_por_articulo = max_por_articulo
        self._indice = indice
        self._embeddings = embeddings
        self._reranker = reranker
        self._first_stage_k = first_stage_k
        self._hibrido = hibrido
        self._peso_denso = peso_denso
        self._peso_lexico = peso_lexico
        # UN cerrojo para todos los que llaman (el agente, los hilos del investigador, el consejo): el embedder y
        # el reranker no admiten dos hilos a la vez, y el buscador es compartido por todo el proceso.
        self._cerrojo = threading.Lock()

    @property
    def hibrido(self) -> bool:
        return self._hibrido

    @classmethod
    def from_settings(cls) -> Any:
        """El buscador del índice configurado (``INDICE_SQLITE_PATH``), o ``SinCorpus`` si no existe."""
        from asistente.conocimiento.llm.embeddings import create_embeddings
        from asistente.nucleo.agent.search import SinCorpus
        from asistente.plataforma import settings

        ruta = Path(settings.INDICE_SQLITE_PATH)
        if not ruta.exists():
            # Sin índice no se cae el cerebro entero: las búsquedas no encuentran nada (la puerta de grounding se
            # abstiene en las preguntas de normativa) y el resto de especialistas sigue disponible.
            logger.warning("No hay índice del corpus en %s: se busca sin corpus.", ruta)
            return SinCorpus()
        reranker = None
        if settings.RERANKER_ENABLED:
            from asistente.conocimiento.llm.reranker import desde_ajustes

            reranker = desde_ajustes()
        return cls(
            IndiceHibrido(ruta),
            create_embeddings(),
            reranker=reranker,
            first_stage_k=settings.RERANKER_FIRST_STAGE_K,
            hibrido=settings.BM25_ENABLED,
            peso_denso=settings.BM25_DENSE_WEIGHT,
            peso_lexico=settings.BM25_BM25_WEIGHT,
        )

    def _recuperar(self, query: str, profundidad: int) -> list[Acierto]:
        vector = self._embeddings.embed_query(query)
        if self._hibrido:
            return self._indice.buscar_hibrido(
                query,
                vector,
                k=profundidad,
                # La fusión necesita listas hondas aunque se pidan pocos resultados: con las 3 o 6 primeras de
                # cada ranking, un fragmento que es 2.º en uno y 8.º en el otro se pierde.
                profundidad=max(profundidad, PROFUNDIDAD_FUSION),
                peso_denso=self._peso_denso,
                peso_lexico=self._peso_lexico,
            )
        return self._indice.buscar_denso(vector, k=profundidad)

    def _candidatos(
        self, query: str, cupo: int, profundidad: int, fecha: str | None, predicado: Any | None
    ) -> list[FragmentoRecuperado]:
        """Los ``cupo`` primeros candidatos de la primera etapa, en vigor y sin repetir artículo.

        Trae ``profundidad`` y, solo si la vigencia o el tope por artículo descartaron alguno y falta cupo, vuelve
        a pedir cuatro veces más: una búsqueda sobre fragmentos que no vienen por artículos no cambia.
        """
        for hondura in (profundidad, profundidad * 4):
            crudos = [
                _fragmento(a)
                for a in self._recuperar(query, hondura)
                if predicado is None or predicado(a.fragmento.metadatos)
            ]
            docs = self._sin_repetir_articulo(self._en_vigor(crudos, fecha))
            if len(docs) >= cupo or len(docs) == len(crudos) or len(crudos) < hondura:
                break
        return docs[:cupo]

    def primera_etapa(self, query: str, profundidad: int) -> list[FragmentoRecuperado]:
        """Los ``profundidad`` primeros candidatos de la primera etapa, sin reranker ni filtros (diagnóstico)."""
        with self._cerrojo:
            return self._candidatos(query, profundidad, profundidad, None, None)

    def search(self, query: str, *, k: int, filters: Any) -> list[FragmentoRecuperado]:
        from asistente.conocimiento.llm.security import sanitize_context_documents
        from asistente.nucleo.agent.search import build_filter_predicate

        first_stage_k = max(k, self._first_stage_k) if self._reranker is not None else k
        profundidad = first_stage_k
        predicado = None
        fecha = getattr(filters, "vigente_en", None)
        if not filters.is_empty():
            predicado = build_filter_predicate(filters)
            profundidad = max(first_stage_k * 20, PROFUNDIDAD_CON_FILTRO)
        with self._cerrojo:
            docs = self._candidatos(query, first_stage_k, profundidad, fecha, predicado)
            if self._reranker is not None and docs:
                docs = list(self._reranker.reordenar(docs, query))
        # El texto recuperado es entrada no fiable para el modelo: se limpia el señuelo de inyección.
        return list(sanitize_context_documents(list(docs[:k])))

    def _sin_repetir_articulo(self, docs: list[FragmentoRecuperado]) -> list[FragmentoRecuperado]:
        """``docs`` en su orden, con a lo sumo ``max_por_articulo`` fragmentos de cada artículo (los mejor
        colocados). Lo que no viene por artículos (doctrina, páginas de la AEAT) pasa sin tope."""
        if not self._max_por_articulo:
            return docs
        cuenta: dict[tuple[str, str], int] = {}
        resultado = []
        for doc in docs:
            meta = doc.metadatos or {}
            norma_id, bloque_id = meta.get("norma_id"), meta.get("bloque_id")
            if norma_id and bloque_id:
                clave = (str(norma_id), str(bloque_id))
                cuenta[clave] = cuenta.get(clave, 0) + 1
                if cuenta[clave] > self._max_por_articulo:
                    continue
            resultado.append(doc)
        return resultado

    # ------------------------------------------------------------------ vigencia

    def _en_vigor(self, docs: list[FragmentoRecuperado], fecha: str | None) -> list[FragmentoRecuperado]:
        """``docs`` tal como rige la ley hoy (``fecha`` None) o como regía en ``fecha`` (ISO), en el mismo orden."""
        resultado: list[FragmentoRecuperado] = []
        vistos: set[str] = set()
        for doc in docs:
            meta = doc.metadatos or {}
            desde = meta.get("vigente_desde")
            if not desde:  # no viene por bloques: no tiene versiones que elegir
                resultado.append(doc)
                continue
            hasta = meta.get("vigente_hasta")
            if fecha is None:
                if not hasta:
                    resultado.append(doc)
                continue
            if desde <= fecha and (not hasta or fecha < hasta):
                resultado.append(doc)
                continue
            historico = self._version_en(meta, fecha)
            if historico is not None and historico.id not in vistos:
                vistos.add(str(historico.id))
                resultado.append(historico)
        return resultado

    def _version_en(self, meta: dict[str, Any], fecha: str) -> FragmentoRecuperado | None:
        norma_id, bloque_id = meta.get("norma_id"), meta.get("bloque_id")
        if not norma_id or not bloque_id:
            return None
        precepto = self._indice.precepto(str(norma_id), bloque_id=str(bloque_id), fecha=fecha)
        if precepto is None or precepto.sin_contenido:
            return None
        return fragmento_de_precepto(precepto, base=meta, maximo=MAX_CARACTERES_HISTORICO)

    # ---------------------------------------------------- lectura por identificador

    def normas(self) -> list[NormaIndexada]:
        return self._indice.normas()

    def resolver_norma(self, referencia: str) -> str | None:
        return self._indice.resolver_norma(referencia)

    def leer_precepto(
        self,
        norma: str,
        *,
        articulo: str | None = None,
        disposicion: str | None = None,
        fecha: str | None = None,
    ) -> Precepto | None:
        """Un artículo («68», «7 bis») o una disposición («Disposición adicional cuarta») de una norma, vigente
        o en ``fecha``. ``None`` si la norma no está por artículos en el índice o no tiene ese precepto."""
        norma_id = self._indice.resolver_norma(norma)
        if norma_id is None:
            return None
        if articulo is not None:
            return self._indice.precepto(norma_id, numero=articulo, fecha=fecha)
        if disposicion is not None:
            return self._indice.precepto(norma_id, titulo=disposicion, fecha=fecha)
        return None

    def versiones_de(
        self, norma: str, *, articulo: str | None = None, disposicion: str | None = None
    ) -> list[Precepto]:
        norma_id = self._indice.resolver_norma(norma)
        if norma_id is None:
            return []
        if articulo is not None:
            return self._indice.versiones(norma_id, numero=articulo)
        return self._indice.versiones(norma_id, titulo=disposicion) if disposicion is not None else []

    def indice_de_norma(self, norma: str) -> list[Precepto]:
        norma_id = self._indice.resolver_norma(norma)
        return [] if norma_id is None else self._indice.indice_norma(norma_id)


def fragmento_de_precepto(
    precepto: Precepto, *, base: dict[str, Any] | None = None, maximo: int | None = None
) -> FragmentoRecuperado:
    """Una versión de un precepto como fragmento citable: cabecera con su sitio en la ley y su texto.

    ``base`` son los metadatos de un fragmento del mismo artículo (se heredan fuente y título); ``maximo`` recorta
    el texto y lo dice, para que el modelo sepa que hay más y lo pida entero.
    """
    nombre = precepto.sigla or precepto.norma_titulo.split(",")[0].strip() or precepto.norma_id
    cabecera = " · ".join([nombre, *precepto.jerarquia, precepto.rotulo.rstrip(".")])
    texto = precepto.texto
    recortado = maximo is not None and len(texto) > maximo
    if recortado:
        texto = texto[:maximo].rsplit(" ", 1)[0] + " […]"
    metadatos = {
        **{k: v for k, v in (base or {}).items() if k in ("source_file", "source_authority", "title", "norma")},
        "chunk_id": f"{precepto.norma_id}:{precepto.bloque_id}:{precepto.vigente_desde}:entero",
        "source_url": f"https://www.boe.es/buscar/act.php?id={precepto.norma_id}#{precepto.bloque_id}",
        "source_authority": "BOE",
        "section": precepto.rotulo,
        "dl_meta": {"headings": [*precepto.jerarquia, precepto.rotulo]},
        "norma_id": precepto.norma_id,
        "bloque_id": precepto.bloque_id,
        "vigente_desde": precepto.vigente_desde,
    }
    metadatos.setdefault("title", precepto.norma_titulo or nombre)
    if precepto.sigla:
        metadatos.setdefault("norma", precepto.sigla)
    if precepto.fuente:
        metadatos.setdefault("source_file", precepto.fuente)
    if precepto.numero:
        metadatos["articulo"] = precepto.numero
    if precepto.vigente_hasta:
        metadatos["vigente_hasta"] = precepto.vigente_hasta
    if recortado:
        metadatos["recortado"] = True
    return FragmentoRecuperado(texto=f"{cabecera}\n{texto}", metadatos=metadatos, id=metadatos["chunk_id"])


def _fragmento(acierto: Acierto) -> FragmentoRecuperado:
    f = acierto.fragmento
    return FragmentoRecuperado(texto=f.texto, metadatos=f.metadatos, id=f.chunk_id)
