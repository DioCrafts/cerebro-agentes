"""Benchmark reproducible de recuperadores del módulo fiscal: denso, híbrido (RRF) y pageindex.

Uso::

    python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal            # simulado, sin red
    python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal --real --indice data/indice --variantes denso,rrf
    # Compara modelos de embeddings sobre los mismos fragmentos (recalcula los vectores con cada uno):
    python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal --real --indice data/indice --variantes denso,rrf \
        --modelo-embeddings data/models/qwen3-emb-mlx-bf16 --modelo-embeddings data/models/f2llm-v2-0.6b-mlx-bf16

Datos (versionados en ``eval_data/recuperacion/``):

* ``corpus_fiscal.jsonl`` — chunks de artículos (LIRPF, LIVA, LIS, LGT, LISD)
  con la ruta de headings tal como la deja Docling (Título > Capítulo > Artículo).
* ``fiscal.jsonl`` — consultas con el artículo (o la página AEAT, por su hash) que debe
  citarse, o ``abstencion``. ``estilo`` agrupa: ``original`` (las 32 primeras),
  ``cuerpo`` (vocabulario del cuerpo del artículo, no de su título), ``aeat`` (la
  mejor fuente es una página de la AEAT), ``fuera_de_corpus`` (abstenciones ajenas
  a la fiscalidad) y ``casi`` (abstenciones fiscales: la respuesta está en una norma
  que el corpus no tiene).
* ``citas_indice.json`` — qué artículos y páginas AEAT hay en el índice del corpus
  (``--escribir-manifiesto``); un test comprueba que toda cita esperada existe.
  Las que llevan ``origen`` derivan de las citas que exigen los evals del
  módulo (``eval_data/modulos/fiscal.jsonl``): esos evals prueban calculadoras,
  no recuperación, así que aquí se convierten en preguntas de normativa.

Variantes: ``denso`` y ``rrf`` son lo que recupera la herramienta ``buscar_fuentes`` del
agente (:class:`~asistente.conocimiento.indice.buscador.SqliteCorpusSearcher` sobre un
``IndiceHibrido`` en memoria: primera etapa densa o fusión RRF de ``sqlite-vec`` y FTS5,
reranker con ``--real`` y saneado), con ``BM25_ENABLED`` apagado y encendido.
``pageindex`` navega el árbol de headings con un LLM. El buscador del agente no se
abstiene por sí solo (eso lo decide la puerta de grounding sobre lo que cita el
modelo), así que sus abstenciones salen a 0 %.

Con ``--indice`` se leen los fragmentos de ``indice.sqlite`` con sus vectores guardados:
el benchmark ordena con los mismos vectores que producción y solo calcula los de las
consultas.

Modo simulado (por defecto): embeddings deterministas por *hashing* de términos
y un selector léxico (:class:`SelectorLexico`) que hace de LLM y contabiliza los
tokens del prompt que habría enviado. Es reproducible bit a bit y sirve para
probar la fontanería y el efecto de la estructura; el veredicto sobre calidad
de razonamiento exige ``--real``.

Métricas: exactitud de la cita (top-1 y en top-k: norma + artículo),
abstenciones correctas, falsas abstenciones, latencia (medida y estimada con
LLM real) y coste por consulta (tokens × precio configurable). Además, para cada
buscador (``primera_etapa``): recall de la primera etapa a 1, 3, 10, 30 y 50;
cuántos fallos son de la primera etapa (la cita no llega a los candidatos del
reranker) y cuántos de la reordenación; y el AUROC con que la similitud densa y la
puntuación del reranker separan las consultas con respuesta de las abstenciones,
por estilo (``fuera_de_corpus`` frente a ``casi``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import time
from typing import TYPE_CHECKING, Any

import click

from asistente.conocimiento.fragmento import FragmentoRecuperado
from asistente.conocimiento.llm.arbol import (
    Consumo,
    Recuperador,
    SelectorLexico,
    SelectorLLM,
    envolver_recuperador,
    ruta_headings,
    tokens,
)

if TYPE_CHECKING:
    from asistente.conocimiento.indice.almacen import IndiceHibrido
    from asistente.conocimiento.indice.buscador import SqliteCorpusSearcher
    from asistente.conocimiento.llm.embeddings import Embedder
    from asistente.nucleo.agent.search import Reordenador

RAIZ = Path(__file__).resolve().parents[3]
CORPUS_POR_DEFECTO = RAIZ / "eval_data" / "recuperacion" / "corpus_fiscal.jsonl"
CONSULTAS_POR_DEFECTO = RAIZ / "eval_data" / "recuperacion" / "fiscal.jsonl"
_ARTICULO = re.compile(r"art[íi]culo\s+(\d+)", re.I)


# --------------------------------------------------------------------------
# Datos
# --------------------------------------------------------------------------


def cargar_corpus(ruta: Path) -> list[FragmentoRecuperado]:
    docs = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        d = json.loads(linea)
        headings = d["headings"]
        docs.append(
            FragmentoRecuperado(
                texto=f"{headings[-1]}. {d['texto']}",
                metadatos={
                    "chunk_id": d["chunk_id"],
                    "source_file": d["source_file"],
                    "norma": d["norma"],
                    "documento": d["documento"],
                    "title": " — ".join(headings),
                    "section": " — ".join(headings),
                    "headings": headings,
                },
            )
        )
    return docs


def cargar_consultas(ruta: Path) -> list[dict]:
    return [json.loads(x) for x in ruta.read_text(encoding="utf-8").splitlines() if x.strip()]


#: BOE consolidated texts of the norms the queries cite (``eval_data/recuperacion/fiscal.jsonl``).
NORMAS_BOE = {
    "BOE-A-2006-20764": ("LIRPF", "Ley 35/2006, del Impuesto sobre la Renta de las Personas Físicas"),
    "BOE-A-1992-28740": ("LIVA", "Ley 37/1992, del Impuesto sobre el Valor Añadido"),
    "BOE-A-2003-23186": ("LGT", "Ley 58/2003, General Tributaria"),
    "BOE-A-2014-12328": ("LIS", "Ley 27/2014, del Impuesto sobre Sociedades"),
    "BOE-A-1987-28141": ("LISD", "Ley 29/1987, del Impuesto sobre Sucesiones y Donaciones"),
    # The 20 % of the autónomos' quarterly payment (modelo 130) lives in the regulation, not in the law.
    "BOE-A-2007-6820": (
        "RIRPF",
        "Real Decreto 439/2007, Reglamento del Impuesto sobre la Renta de las Personas Físicas",
    ),
}


#: AEAT pages are stored by the scraper as ``<md5 of the URL>.html``; the BOE ones keep their BOE id.
_PAGINA_AEAT = re.compile(r"(?:^|/)([0-9a-f]{32})\.html$")


def pagina_aeat(doc: FragmentoRecuperado) -> str | None:
    """Hash of the AEAT page a chunk comes from, or ``None`` if it is not an AEAT page."""
    m = _PAGINA_AEAT.search(str((doc.metadatos or {}).get("source_file") or (doc.metadatos or {}).get("source") or ""))
    return m.group(1) if m else None


def _ruta_sqlite(ruta: Path) -> Path:
    """``ruta`` is the index file itself or the directory that holds ``indice.sqlite``."""
    from asistente.conocimiento.indice.ingesta import ruta_indice

    return ruta if ruta.is_file() else ruta_indice(ruta)


def leer_corpus_de_indice(ruta: Path, *, aeat: bool = True) -> list[tuple[FragmentoRecuperado, list[float] | None]]:
    """The chunks of the cited norms (and the AEAT pages) as the real processor indexed them, with their vectors.

    Reads the SQLite index (``indice.sqlite``). Text and heading path are kept as Docling left them; ``norma`` /
    ``documento`` are stamped from the BOE id in the file name so the citation can be scored. With ``aeat`` the
    AEAT pages compete too, as they do in production (``norma="AEAT"``; the page is scored by its hash). The
    stored vectors are kept, so the benchmark ranks with exactly what production ranks with.
    """
    from asistente.conocimiento.indice.almacen import IndiceHibrido

    pares = []
    with IndiceHibrido(_ruta_sqlite(ruta), solo_lectura=True) as indice:
        for f, vector in indice.recorrer(con_vectores=True):
            fuente = str(f.fuente or f.metadatos.get("source_file") or f.metadatos.get("source") or "")
            doc = FragmentoRecuperado(texto=f.texto, metadatos={**f.metadatos, "source_file": fuente}, id=f.chunk_id)
            boe = next((b for b in NORMAS_BOE if b in fuente), None)
            if boe is not None:
                norma, documento = NORMAS_BOE[boe]
            elif aeat and pagina_aeat(doc):
                norma, documento = "AEAT", None
            else:
                continue
            meta = {**doc.metadatos, "norma": norma}
            if documento:
                meta["documento"] = documento
            pares.append((doc.con(metadatos=meta), vector))
    return pares


def cargar_corpus_de_indice(ruta: Path, *, aeat: bool = True) -> list[FragmentoRecuperado]:
    """:func:`leer_corpus_de_indice` without the vectors."""
    return [doc for doc, _ in leer_corpus_de_indice(ruta, aeat=aeat)]


def manifiesto_de_corpus(docs: Sequence[FragmentoRecuperado]) -> dict:
    """Which citations exist in a corpus: article numbers per norm and AEAT pages (hash → title)."""
    normas: dict[str, set[str]] = {}
    aeat: dict[str, str] = {}
    for d in docs:
        h = pagina_aeat(d) if d.metadatos.get("norma") == "AEAT" else None
        if h:
            ruta = ruta_headings(d)
            aeat.setdefault(h, ruta[0] if ruta else "")
            continue
        _, articulo = cita_de(d)
        if articulo:
            normas.setdefault(str(d.metadatos.get("norma")), set()).add(articulo)
    return {
        "normas": {n: sorted(a, key=int) for n, a in sorted(normas.items())},
        "aeat": dict(sorted(aeat.items())),
    }


def cita_en_manifiesto(espera: dict, manifiesto: dict) -> bool:
    """Whether the expected citation (and every accepted alternative) exists in the manifest."""
    if "pagina" in espera:
        return espera["pagina"] in manifiesto["aeat"]
    opciones = [espera, *espera.get("alternativas", [])]
    return all(str(o["articulo"]) in manifiesto["normas"].get(o["norma"], []) for o in opciones)


def cita_de(doc: FragmentoRecuperado) -> tuple[str, str | None]:
    """(norma/fuente, nº de artículo) que citaría el grounding gate para ``doc``."""
    meta = doc.metadatos or {}
    ruta = ruta_headings(doc)
    articulo = None
    for h in reversed(ruta):
        m = _ARTICULO.search(h)
        if m:
            articulo = m.group(1)
            break
    fuente = " ".join(str(meta.get(k, "")) for k in ("norma", "documento", "source_file", "title"))
    return fuente, articulo


def es_cita_correcta(doc: FragmentoRecuperado, espera: dict) -> bool:
    """Whether ``doc`` is an acceptable source for the expected citation.

    * Law article: same norm and article number (``alternativas`` lists other articles that also answer).
    * AEAT page (``{"fuente": "AEAT", "pagina": <hash>}``): any chunk of that page counts. A page has no
      articles, so the unit the grounding gate would cite is the page itself.
    """
    if "pagina" in espera:
        return bool(pagina_aeat(doc) == espera["pagina"])
    fuente, articulo = cita_de(doc)
    norma = str((doc.metadatos or {}).get("norma") or "").upper()
    for o in (espera, *espera.get("alternativas", [])):
        # Same norm by equality when the chunk carries one: a substring test let "LIS" match "LISD".
        misma = norma == o["norma"].upper() if norma else o["norma"].upper() in fuente.upper()
        if articulo == str(o["articulo"]) and misma:
            return True
    return False


# --------------------------------------------------------------------------
# Los buscadores: el del agente (SqliteCorpusSearcher), denso o híbrido
# --------------------------------------------------------------------------

#: ``denso`` and ``rrf`` are the agent's searcher with ``BM25_ENABLED`` off and on; ``pageindex`` is the tree.
BUSCADORES = ("denso", "rrf")
VARIANTES = (*BUSCADORES, "pageindex")


class EmbeddingsHash:
    """Embeddings deterministas: bolsa de términos (stem de 6) por *hashing*."""

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def _uno(self, texto: str) -> list[float]:
        v = [0.0] * self.dim
        for t in tokens(texto):
            v[int(hashlib.md5(t.encode()).hexdigest(), 16) % self.dim] += 1.0
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._uno(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._uno(text)


class EmbeddingsConCache:
    """The embedder with its query vectors cached: every variant and the diagnosis embed the same queries."""

    def __init__(self, embeddings: Embedder) -> None:
        self._embeddings = embeddings
        self._cache: dict[str, list[float]] = {}

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embeddings.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        if text not in self._cache:
            self._cache[text] = self._embeddings.embed_query(text)
        return self._cache[text]


def construir_indice(
    corpus: Sequence[FragmentoRecuperado],
    embeddings: Embedder,
    vectores: Sequence[list[float] | None] | None = None,
) -> IndiceHibrido:
    """The corpus in an in-memory :class:`IndiceHibrido`: the stored vectors of the real index or, without them,
    the embedder's. Dense search (``sqlite-vec``) and FTS5 are the production code."""
    from asistente.conocimiento.indice.almacen import Fragmento, IndiceHibrido

    if vectores is None or any(v is None for v in vectores):
        vectores = embeddings.embed_documents([d.texto for d in corpus])
    fragmentos = [
        Fragmento(
            chunk_id=d.id or str(d.metadatos.get("chunk_id") or f"c{i}"), texto=d.texto, metadatos=dict(d.metadatos)
        )
        for i, d in enumerate(corpus)
    ]
    indice = IndiceHibrido(":memory:")
    indice.anadir(fragmentos, [list(v) for v in vectores if v is not None])
    return indice


class RecuperadorAgente:
    """A ``denso`` / ``rrf`` variant: exactly what the agent's ``buscar_fuentes`` tool retrieves.

    :class:`SqliteCorpusSearcher` with no filters: the ``primera_etapa`` first candidates (dense, or the RRF of
    dense and FTS5), reordered by the cross-encoder when there is one (``--real`` with ``RERANKER_ENABLED``), the
    first ``k`` sanitized. It never abstains on its own: in the agent, abstaining is the grounding gate's call.
    """

    def __init__(self, buscador: SqliteCorpusSearcher, k: int = 3) -> None:
        self.buscador = buscador
        self.k = k

    def invoke(self, consulta: str) -> list[FragmentoRecuperado]:
        from asistente.nucleo.agent.search import SearchFilters

        return list(self.buscador.search(consulta, k=self.k, filters=SearchFilters()))

    def primera_etapa(self, consulta: str, profundidad: int) -> list[FragmentoRecuperado]:
        return self.buscador.primera_etapa(consulta, profundidad)


def construir_buscador(
    indice: IndiceHibrido,
    embeddings: Embedder,
    *,
    k: int,
    hibrido: bool = False,
    primera_etapa: int = 30,
    reranker: Reordenador | None = None,
    peso_denso: float = 0.9,
    peso_lexico: float = 0.1,
) -> RecuperadorAgente:
    from asistente.conocimiento.indice.buscador import SqliteCorpusSearcher

    buscador = SqliteCorpusSearcher(
        indice,
        embeddings,
        reranker=reranker,
        first_stage_k=primera_etapa,
        hibrido=hibrido,
        peso_denso=peso_denso,
        peso_lexico=peso_lexico,
    )
    return RecuperadorAgente(buscador=buscador, k=k)


# --------------------------------------------------------------------------
# Primera etapa: dónde queda la cita correcta antes del reranker
# --------------------------------------------------------------------------

#: Profundidades a las que se mide el recall de la primera etapa. 30 es lo que ve el reranker del agente
#: (``RERANKER_FIRST_STAGE_K``); 50, el margen que tendría una primera etapa más honda.
PROFUNDIDADES = (1, 3, 10, 30, 50)


def rango_primera_etapa(
    recuperador: RecuperadorAgente, consulta: str, espera: dict, profundidad: int = 50
) -> int | None:
    """Posición (desde 1) del primer fragmento correcto en la primera etapa, o ``None`` si queda más abajo."""
    for posicion, doc in enumerate(recuperador.primera_etapa(consulta, profundidad), start=1):
        if es_cita_correcta(doc, espera):
            return posicion
    return None


def similitud_maxima(indice: IndiceHibrido, embeddings: Embedder, consulta: str) -> float:
    """Coseno del vecino denso más cercano (``sqlite-vec`` da la distancia coseno, 1 - coseno)."""
    aciertos = indice.buscar_denso(embeddings.embed_query(consulta), k=1)
    return round(1.0 - aciertos[0].puntuacion, 4) if aciertos else 0.0


def auroc(positivos: Sequence[float], negativos: Sequence[float]) -> float | None:
    """Probabilidad de que un positivo puntúe más que un negativo (empates cuentan medio); ``None`` sin datos.

    Aquí los positivos son las consultas con respuesta en el corpus y los negativos las que no la tienen:
    1,0 quiere decir que un umbral sobre la puntuación las separa del todo; 0,5, que no las distingue.
    """
    if not positivos or not negativos:
        return None
    ganados = sum((p > n) + 0.5 * (p == n) for p in positivos for n in negativos)
    return round(ganados / (len(positivos) * len(negativos)), 3)


def diagnosticar_primera_etapa(
    recuperador: RecuperadorAgente,
    indice: IndiceHibrido,
    embeddings: Embedder,
    consultas: Sequence[dict],
    filas_variante: Sequence[dict],
    *,
    primera_etapa: int,
    profundidad: int = 50,
) -> dict:
    """Recall de la primera etapa por profundidad, a quién atribuir cada fallo y si la puntuación separa las
    abstenciones.

    ``filas_variante`` son las filas de la misma variante, alineadas con ``consultas``. Un fallo (la cita no
    está entre las k que se devuelven) es de la primera etapa si la cita correcta no llega a los
    ``primera_etapa`` candidatos que ve el reranker, y de la reordenación si llega y aun así no sube a las k
    primeras.
    """
    filas: list[dict[str, Any]] = []
    for q, variante in zip(consultas, filas_variante, strict=True):
        fila: dict[str, Any] = {
            "id": q["id"],
            "tipo": q["tipo"],
            "estilo": q.get("estilo", "original"),
            "sim_max": similitud_maxima(indice, embeddings, q["consulta"]),
            "score_reranker": variante.get("score_reranker"),
        }
        if q["tipo"] == "cita":
            fila["rango"] = rango_primera_etapa(recuperador, q["consulta"], q["espera"], profundidad)
            if not variante["en_k"]:
                fuera = fila["rango"] is None or fila["rango"] > primera_etapa
                fila["fallo"] = "primera_etapa" if fuera else "reordenacion"
        filas.append(fila)

    citas = [f for f in filas if f["tipo"] == "cita"]

    def recall(grupo: Sequence[dict]) -> dict[str, float]:
        if not grupo:
            return {}
        return {
            f"@{p}": round(sum(f["rango"] is not None and f["rango"] <= p for f in grupo) / len(grupo), 3)
            for p in PROFUNDIDADES
            if p <= profundidad
        }

    separabilidad = {}
    for estilo in sorted({f["estilo"] for f in filas if f["tipo"] == "abstencion"}):
        negativos = [f for f in filas if f["tipo"] == "abstencion" and f["estilo"] == estilo]
        separabilidad[estilo] = {
            "n": len(negativos),
            "denso": auroc([f["sim_max"] for f in citas], [f["sim_max"] for f in negativos]),
            "reranker": auroc(
                [f["score_reranker"] for f in citas if f["score_reranker"] is not None],
                [f["score_reranker"] for f in negativos if f["score_reranker"] is not None],
            ),
        }
    fallos = [f["fallo"] for f in citas if "fallo" in f]
    return {
        "profundidad": profundidad,
        "primera_etapa": primera_etapa,
        "resumen": {
            "recall": recall(citas),
            "recall_por_estilo": {
                e: recall([f for f in citas if f["estilo"] == e]) for e in sorted({f["estilo"] for f in citas})
            },
            "fallos_primera_etapa": fallos.count("primera_etapa"),
            "fallos_reordenacion": fallos.count("reordenacion"),
            "separabilidad_abstencion": separabilidad,
        },
        "filas": filas,
    }


# --------------------------------------------------------------------------
# Ejecución
# --------------------------------------------------------------------------


@dataclass
class Precios:
    entrada_por_millon: float
    salida_por_millon: float
    latencia_llm_ms: float

    def coste(self, c: Consumo) -> float:
        return (c.tokens_entrada * self.entrada_por_millon + c.tokens_salida * self.salida_por_millon) / 1e6


def evaluar_variante(nombre: str, recuperador: Recuperador, consultas: Sequence[dict], precios: Precios) -> dict:
    filas = []
    for q in consultas:
        t0 = time.perf_counter()
        docs = recuperador.invoke(q["consulta"])
        ms = (time.perf_counter() - t0) * 1000
        consumo = getattr(recuperador, "ultimo_consumo", None) or Consumo()
        fila = {
            "id": q["id"],
            "tipo": q["tipo"],
            "estilo": q.get("estilo", "original"),
            "abstiene": not docs,
            "citas": [_etiqueta(d) for d in docs],
            "latencia_ms": round(ms, 2),
            "latencia_estimada_ms": round(ms + consumo.llamadas * precios.latencia_llm_ms, 1),
            "llamadas_llm": consumo.llamadas,
            "tokens": consumo.tokens_entrada + consumo.tokens_salida,
            "coste": precios.coste(consumo),
        }
        score = (docs[0].metadatos or {}).get("_rerank_score") if docs else None
        fila["score_reranker"] = round(float(score), 4) if isinstance(score, int | float) else None
        if q["tipo"] == "cita":
            fila["top1"] = bool(docs) and es_cita_correcta(docs[0], q["espera"])
            fila["en_k"] = any(es_cita_correcta(d, q["espera"]) for d in docs)
        filas.append(fila)
    return {"variante": nombre, "resumen": resumir(filas), "filas": filas}


def _etiqueta(doc: FragmentoRecuperado) -> str:
    h = pagina_aeat(doc)
    if h and doc.metadatos.get("norma") == "AEAT":
        return f"AEAT {h[:8]}"
    fuente, articulo = cita_de(doc)
    return f"{fuente.split()[0] if fuente.split() else '?'} art. {articulo}"


def wilson(aciertos: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 % Wilson score interval for a proportion (sane at 0 % / 100 % and small ``n``)."""
    if n == 0:
        return (0.0, 0.0)
    p = aciertos / n
    centro = (p + z * z / (2 * n)) / (1 + z * z / n)
    margen = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (round(max(0.0, centro - margen), 3), round(min(1.0, centro + margen), 3))


def resumir(filas: list[dict]) -> dict:
    citas = [f for f in filas if f["tipo"] == "cita"]
    abst = [f for f in filas if f["tipo"] == "abstencion"]
    lat = [f["latencia_estimada_ms"] for f in filas]

    def frac(xs: list[bool]) -> float:
        return round(sum(xs) / len(xs), 3) if xs else 0.0

    # "Correct behaviour": a citation query gets the right source among the k, an out-of-corpus one abstains.
    correctas = [f["en_k"] for f in citas] + [f["abstiene"] for f in abst]
    por_estilo = {}
    for estilo in sorted({f["estilo"] for f in citas}):
        grupo = [f for f in citas if f["estilo"] == estilo]
        por_estilo[estilo] = {
            "n": len(grupo),
            "top1": frac([f["top1"] for f in grupo]),
            "en_k": frac([f["en_k"] for f in grupo]),
        }
    return {
        "consultas": len(filas),
        "n_citas": len(citas),
        "n_abstenciones": len(abst),
        "cita_top1": frac([f["top1"] for f in citas]),
        "cita_top1_ic95": wilson(sum(f["top1"] for f in citas), len(citas)),
        "cita_en_k": frac([f["en_k"] for f in citas]),
        "cita_en_k_ic95": wilson(sum(f["en_k"] for f in citas), len(citas)),
        "abstencion_correcta": frac([f["abstiene"] for f in abst]),
        "abstencion_correcta_ic95": wilson(sum(f["abstiene"] for f in abst), len(abst)),
        "falsa_abstencion": frac([f["abstiene"] for f in citas]),
        "conducta_correcta": frac(correctas),
        "por_estilo": por_estilo,
        "latencia_medida_ms_media": round(statistics.mean(f["latencia_ms"] for f in filas), 2),
        "latencia_estimada_ms_media": round(statistics.mean(lat), 1),
        "latencia_estimada_ms_p95": round(sorted(lat)[max(0, math.ceil(0.95 * len(lat)) - 1)], 1),
        "llamadas_llm_por_consulta": round(statistics.mean(f["llamadas_llm"] for f in filas), 2),
        "tokens_por_consulta": round(statistics.mean(f["tokens"] for f in filas), 1),
        "coste_por_consulta": round(statistics.mean(f["coste"] for f in filas), 7),
    }


def ejecutar(
    *,
    variantes: Sequence[str] = VARIANTES,
    corpus_path: Path = CORPUS_POR_DEFECTO,
    consultas_path: Path = CONSULTAS_POR_DEFECTO,
    embeddings: Embedder | None = None,
    selector=None,
    k: int = 3,
    precios: Precios | None = None,
    indice: Path | None = None,
    aeat: bool = True,
    primera_etapa: int = 30,
    reranker: Reordenador | None = None,
    profundidad: int = 50,
    peso_denso: float = 0.9,
    peso_lexico: float = 0.1,
    recalcular_vectores: bool = False,
    modelo_embeddings: str | None = None,
) -> dict:
    """Run every variant over the same corpus and queries; the searchers carry their first-stage diagnosis.

    ``recalcular_vectores`` ignores the vectors stored in the index and embeds the corpus with ``embeddings``:
    that is how another embedding model is measured on the same chunks. ``modelo_embeddings`` only labels the
    result.
    """
    desconocidas = [v for v in variantes if v not in VARIANTES]
    if desconocidas:
        raise ValueError(f"Variantes desconocidas: {', '.join(desconocidas)} (usa {', '.join(VARIANTES)})")
    if indice is not None:
        pares = leer_corpus_de_indice(indice, aeat=aeat)
        corpus, vectores = [d for d, _ in pares], [v for _, v in pares]
    else:
        corpus, vectores = cargar_corpus(corpus_path), None
    if recalcular_vectores:
        vectores = None
    consultas = cargar_consultas(consultas_path)
    precios = precios or Precios(0.15, 0.60, 800.0)
    emb = EmbeddingsConCache(embeddings or EmbeddingsHash())
    inicio = time.perf_counter()
    almacen = construir_indice(corpus, emb, vectores)
    segundos_indice = time.perf_counter() - inicio
    resultados = []
    for v in variantes:
        if v in BUSCADORES:
            rec = construir_buscador(
                almacen,
                emb,
                k=k,
                hibrido=v == "rrf",
                primera_etapa=primera_etapa,
                reranker=reranker,
                peso_denso=peso_denso,
                peso_lexico=peso_lexico,
            )
            r = evaluar_variante(v, rec, consultas, precios)
            r["primera_etapa"] = diagnosticar_primera_etapa(
                rec, almacen, emb, consultas, r["filas"], primera_etapa=primera_etapa, profundidad=profundidad
            )
        else:
            base = construir_buscador(almacen, emb, k=k)
            r = evaluar_variante(
                v, envolver_recuperador(v, base, corpus, selector or SelectorLexico(), k=k), consultas, precios
            )
        resultados.append(r)
    return {
        "corpus": str(indice)
        if indice is not None
        else (str(corpus_path.relative_to(RAIZ)) if corpus_path.is_relative_to(RAIZ) else str(corpus_path)),
        "chunks": len(corpus),
        "chunks_aeat": sum(d.metadatos.get("norma") == "AEAT" for d in corpus),
        "modelo_embeddings": modelo_embeddings,
        "vectores_recalculados": vectores is None,
        # With recalculated vectors this is the time to embed the whole corpus: what a reindex would cost.
        "segundos_indice": round(segundos_indice, 1),
        "consultas": len(consultas),
        "k": k,
        "primera_etapa": primera_etapa,
        "reranker": reranker is not None,
        "pesos_rrf": {"denso": peso_denso, "lexico": peso_lexico},
        "precios_por_millon_tokens": {"entrada": precios.entrada_por_millon, "salida": precios.salida_por_millon},
        "latencia_llm_ms_supuesta": precios.latencia_llm_ms,
        "variantes": resultados,
    }


def _pct(x: float) -> str:
    return f"{x:.0%}"


def _ic(par: Sequence[float]) -> str:
    return f"{par[0]:.0%}–{par[1]:.0%}"


def tabla_markdown(resultado: dict) -> str:
    cab = (
        "| variante | cita top-1 | cita en top-k (IC 95 %) | abst. correctas (IC 95 %) | falsas abst. | "
        "conducta correcta | lat. est. media (ms) | p95 (ms) | llamadas LLM | tokens | coste/consulta |"
    )
    out = [cab, "|" + "---|" * 11]
    for v in resultado["variantes"]:
        r = v["resumen"]
        out.append(
            f"| {v['variante']} | {_pct(r['cita_top1'])} | {_pct(r['cita_en_k'])} ({_ic(r['cita_en_k_ic95'])}) | "
            f"{_pct(r['abstencion_correcta'])} ({_ic(r['abstencion_correcta_ic95'])}) | {_pct(r['falsa_abstencion'])} | "
            f"{_pct(r['conducta_correcta'])} | {r['latencia_estimada_ms_media']} | {r['latencia_estimada_ms_p95']} | "
            f"{r['llamadas_llm_por_consulta']} | {r['tokens_por_consulta']} | {r['coste_por_consulta']:.6f} |"
        )
    if resultado["variantes"]:
        r = resultado["variantes"][0]["resumen"]
        out.append(f"\nn = {r['n_citas']} citas, {r['n_abstenciones']} abstenciones")
        out.append(
            "\n| variante | " + " | ".join(f"{e} (n={x['n']}) top-1 / top-k" for e, x in r["por_estilo"].items()) + " |"
        )
        out.append("|" + "---|" * (1 + len(r["por_estilo"])))
        for v in resultado["variantes"]:
            pe = v["resumen"]["por_estilo"]
            out.append(
                f"| {v['variante']} | "
                + " | ".join(f"{_pct(x['top1'])} / {_pct(x['en_k'])}" for x in pe.values())
                + " |"
            )
    for v in resultado["variantes"]:
        if "primera_etapa" in v:
            out.extend(_tabla_primera_etapa(v["variante"], v["primera_etapa"]))
    return "\n".join(out)


def tabla_comparativa(resultados: Sequence[dict]) -> str:
    """One row per embedding model and searcher variant, to choose a model at a glance.

    The first-stage recall is the part the embedder decides alone; top-1 and top-k already include the reranker.
    """
    filas = [(r, v) for r in resultados for v in r["variantes"] if "primera_etapa" in v]
    if not filas:
        return ""
    cols = list(filas[0][1]["primera_etapa"]["resumen"]["recall"])
    estilos = list(filas[0][1]["resumen"]["por_estilo"])
    out = [
        "| modelo | variante | cita top-1 | cita en top-k (IC 95 %) | "
        + " | ".join(f"recall denso {c}" for c in cols)
        + " | fallos 1.ª etapa / reordenación | "
        + " | ".join(f"{e} top-1 / top-k" for e in estilos)
        + " | s indexar corpus |",
        "|" + "---|" * (6 + len(cols) + len(estilos)),
    ]
    for r, v in filas:
        res, diag = v["resumen"], v["primera_etapa"]["resumen"]
        nombre = Path(r.get("modelo_embeddings") or "vectores del índice").name
        out.append(
            f"| {nombre} | {v['variante']} | {_pct(res['cita_top1'])} | "
            f"{_pct(res['cita_en_k'])} ({_ic(res['cita_en_k_ic95'])}) | "
            + " | ".join(_pct(diag["recall"].get(c, 0.0)) for c in cols)
            + f" | {diag['fallos_primera_etapa']} / {diag['fallos_reordenacion']} | "
            + " | ".join(
                f"{_pct(x['top1'])} / {_pct(x['en_k'])}" if (x := res["por_estilo"].get(e)) else "—" for e in estilos
            )
            + f" | {r['segundos_indice'] if r.get('vectores_recalculados') else '—'} |"
        )
    return "\n".join(out)


def _auc(x: float | None) -> str:
    return "—" if x is None else f"{x:.2f}"


def _tabla_primera_etapa(variante: str, diag: dict) -> list[str]:
    r = diag["resumen"]
    cols = list(r["recall"])
    out = [
        f"\nPrimera etapa de `{variante}` (antes del reranker, que recibe {diag['primera_etapa']} candidatos):",
        "\n| estilo | " + " | ".join(f"recall {c}" for c in cols) + " |",
        "|" + "---|" * (1 + len(cols)),
        "| todas | " + " | ".join(_pct(r["recall"][c]) for c in cols) + " |",
    ]
    for estilo, rec in r["recall_por_estilo"].items():
        out.append(f"| {estilo} | " + " | ".join(_pct(rec.get(c, 0.0)) for c in cols) + " |")
    out.append(
        f"\nFallos de `{variante}`: {r['fallos_primera_etapa']} en la primera etapa (la cita no llega a los "
        f"candidatos), {r['fallos_reordenacion']} en la reordenación (llega y no sube al top-k)."
    )
    if r["separabilidad_abstencion"]:
        out.append("\n| abstenciones | n | AUROC sim. densa | AUROC reranker |")
        out.append("|---|---|---|---|")
        for estilo, x in r["separabilidad_abstencion"].items():
            out.append(f"| {estilo} | {x['n']} | {_auc(x['denso'])} | {_auc(x['reranker'])} |")
        out.append(
            "\nAUROC: 1 = un umbral separa las consultas con respuesta de las que no la tienen; 0,5 = no las distingue."
        )
    return out


class _ClaudeTexto:
    """``.invoke(prompt) -> str`` over the Anthropic SDK (``ANTHROPIC_MODEL``), for :class:`SelectorLLM`."""

    def __init__(self) -> None:
        import anthropic

        from asistente.plataforma.settings import ANTHROPIC_MODEL

        self._cliente = anthropic.Anthropic()
        self._modelo = ANTHROPIC_MODEL

    def invoke(self, prompt: str) -> str:
        # Choosing node ids is a light task: low effort. Opus 5.5 always thinks, and the thinking counts towards
        # max_tokens, so the old 1024 could leave no room for the answer (an empty choice reads as "nothing found").
        r = self._cliente.messages.create(
            model=self._modelo,
            max_tokens=8000,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in r.content if b.type == "text")


class _OpenAITexto:
    """``.invoke(prompt) -> str`` over the OpenAI SDK (``OPENAI_AGENT_MODEL``), for :class:`SelectorLLM`."""

    def __init__(self) -> None:
        import openai

        from asistente.plataforma.settings import OPENAI_AGENT_MODEL
        from asistente.plataforma.utils import get_openai_api_key

        self._cliente = openai.OpenAI(api_key=get_openai_api_key())
        self._modelo = OPENAI_AGENT_MODEL

    def invoke(self, prompt: str) -> str:
        r = self._cliente.chat.completions.create(
            model=self._modelo, temperature=0, messages=[{"role": "user", "content": prompt}]
        )
        return r.choices[0].message.content or ""


@click.command()
@click.option("--variantes", default=",".join(VARIANTES), show_default=True, help="Lista separada por comas.")
@click.option("--corpus", "corpus_path", type=click.Path(path_type=Path), default=CORPUS_POR_DEFECTO)
@click.option("--consultas", "consultas_path", type=click.Path(path_type=Path), default=CONSULTAS_POR_DEFECTO)
@click.option(
    "--real",
    is_flag=True,
    help="Embeddings del proyecto, reranker si RERANKER_ENABLED y LLM real como selector de pageindex.",
)
@click.option(
    "--proveedor",
    type=click.Choice(["anthropic", "openai"]),
    default="anthropic",
    show_default=True,
    help="LLM selector con --real (ANTHROPIC_MODEL u OPENAI_AGENT_MODEL).",
)
@click.option(
    "--indice",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help="Evalúa sobre los fragmentos reales del índice SQLite del procesador (el directorio o indice.sqlite).",
)
@click.option("--k", default=3, show_default=True)
@click.option(
    "--aeat/--sin-aeat",
    default=True,
    show_default=True,
    help="Con --indice, incluye las páginas de la AEAT como competidoras.",
)
@click.option(
    "--primera-etapa",
    default=None,
    type=int,
    help="Candidatos densos antes del reranker (por defecto, RERANKER_FIRST_STAGE_K, como el agente).",
)
@click.option(
    "--escribir-manifiesto",
    type=click.Path(path_type=Path),
    default=None,
    help="Con --indice: escribe qué artículos y páginas AEAT existen y termina.",
)
@click.option(
    "--profundidad",
    default=50,
    show_default=True,
    help="Hasta qué posición del ranking denso se busca la cita correcta (recall de la primera etapa).",
)
@click.option("--peso-denso", type=float, default=None, help="Peso del ranking denso en RRF (BM25_DENSE_WEIGHT).")
@click.option("--peso-lexico", type=float, default=None, help="Peso del ranking FTS5 en RRF (BM25_BM25_WEIGHT).")
@click.option("--precio-entrada", default=0.15, show_default=True, help="Precio por 1M tokens de entrada.")
@click.option("--precio-salida", default=0.60, show_default=True, help="Precio por 1M tokens de salida.")
@click.option(
    "--latencia-llm-ms", default=800.0, show_default=True, help="Latencia supuesta por llamada LLM (simulado)."
)
@click.option(
    "--modelo-embeddings",
    "modelos_embeddings",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    multiple=True,
    help=(
        "Con --real: directorio de un modelo MLX de embeddings con el que recalcular los vectores del corpus. "
        "Repetible: mide cada modelo sobre los mismos fragmentos, con el mismo reranker, y termina con una tabla "
        "comparativa."
    ),
)
@click.option("--salida", type=click.Path(path_type=Path), default=None, help="Guarda el JSON completo.")
def cli(
    variantes: str,
    corpus_path: Path,
    consultas_path: Path,
    real: bool,
    proveedor: str,
    indice: Path | None,
    k: int,
    aeat: bool,
    primera_etapa: int | None,
    escribir_manifiesto: Path | None,
    profundidad: int,
    peso_denso: float | None,
    peso_lexico: float | None,
    precio_entrada: float,
    precio_salida: float,
    latencia_llm_ms: float,
    modelos_embeddings: tuple[Path, ...],
    salida: Path | None,
) -> None:
    from asistente.plataforma import settings

    if modelos_embeddings and not real:
        raise click.UsageError("--modelo-embeddings necesita --real")

    if escribir_manifiesto is not None:
        if indice is None:
            raise click.UsageError("--escribir-manifiesto necesita --indice")
        manifiesto = manifiesto_de_corpus(cargar_corpus_de_indice(indice, aeat=True))
        escribir_manifiesto.write_text(json.dumps(manifiesto, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        click.echo(f"{sum(map(len, manifiesto['normas'].values()))} artículos, {len(manifiesto['aeat'])} páginas AEAT")
        return
    if primera_etapa is None:
        primera_etapa = settings.RERANKER_FIRST_STAGE_K
    lista = [v.strip() for v in variantes.split(",") if v.strip()]
    embeddings = None
    selector = None
    reranker = None
    if real:
        from asistente.conocimiento.llm.embeddings import create_embeddings

        # With --modelo-embeddings each model brings its own embedder: do not load the default one too.
        embeddings = None if modelos_embeddings else create_embeddings()
        if settings.RERANKER_ENABLED:
            from asistente.conocimiento.llm.reranker import desde_ajustes

            reranker = desde_ajustes()
        if "pageindex" in lista:
            # Only the tree needs the LLM: the searchers run with the local models and no API key.
            selector = SelectorLLM(_ClaudeTexto() if proveedor == "anthropic" else _OpenAITexto())
        latencia_llm_ms = 0.0  # con LLM real la latencia ya va medida
    comun: dict[str, Any] = {
        "variantes": lista,
        "corpus_path": corpus_path,
        "consultas_path": consultas_path,
        "selector": selector,
        "k": k,
        "precios": Precios(precio_entrada, precio_salida, latencia_llm_ms),
        "indice": indice,
        "aeat": aeat,
        "primera_etapa": primera_etapa,
        "reranker": reranker,
        "profundidad": profundidad,
        "peso_denso": settings.BM25_DENSE_WEIGHT if peso_denso is None else peso_denso,
        "peso_lexico": settings.BM25_BM25_WEIGHT if peso_lexico is None else peso_lexico,
    }
    modo = "real" if real else "simulado"
    if not modelos_embeddings:
        resultado = ejecutar(embeddings=embeddings, **comun)
        resultado["modo"] = modo
        if salida:
            salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
        click.echo(tabla_markdown(resultado))
        return

    from asistente.conocimiento.llm.embeddings import create_embeddings

    resultados = []
    for modelo in modelos_embeddings:
        click.echo(f"Midiendo {modelo.name}: vectores del corpus y {len(lista)} variante(s)…", err=True)
        r = ejecutar(
            embeddings=create_embeddings(str(modelo)),
            recalcular_vectores=True,
            modelo_embeddings=str(modelo),
            **comun,
        )
        r["modo"] = modo
        resultados.append(r)
        click.echo(f"\n## {modelo.name}\n\n{tabla_markdown(r)}")
    if salida:
        salida.write_text(
            json.dumps({"modo": modo, "modelos": resultados}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    click.echo(f"\n## Comparativa\n\n{tabla_comparativa(resultados)}")


if __name__ == "__main__":
    cli()
