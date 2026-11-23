"""Benchmark reproducible de recuperadores del módulo fiscal: faiss vs pageindex.

Uso::

    python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal            # simulado, sin red
    python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal --real     # embeddings + LLM reales

Datos (versionados en ``eval_data/recuperacion/``):

* ``corpus_fiscal.jsonl`` — chunks de artículos (LIRPF, LIVA, LIS, LGT, LISD)
  con la ruta de headings tal como la deja Docling (Título > Capítulo > Artículo).
* ``fiscal.jsonl`` — consultas con el artículo (o la página AEAT, por su hash) que debe
  citarse, o ``abstencion``. ``estilo`` agrupa: ``original`` (las 32 primeras),
  ``cuerpo`` (vocabulario del cuerpo del artículo, no de su título), ``aeat`` (la
  mejor fuente es una página de la AEAT) y ``fuera_de_corpus`` (abstenciones).
* ``citas_indice.json`` — qué artículos y páginas AEAT hay en ``data/faiss``
  (``--escribir-manifiesto``); un test comprueba que toda cita esperada existe.
  Las que llevan ``origen`` derivan de las citas que exigen los evals del
  módulo (``eval_data/modulos/fiscal.jsonl``): esos evals prueban calculadoras,
  no recuperación, así que aquí se convierten en preguntas de normativa.

Variantes: ``faiss`` es lo que recupera la herramienta ``search_corpus`` del agente
(:class:`~asistente.nucleo.agent.search.FaissCorpusSearcher`: vecinos densos, reranker
con ``--real`` y saneado); ``pageindex`` navega el árbol de headings con un LLM.
El buscador del agente no se abstiene por sí solo (eso lo decide la puerta de
grounding sobre lo que cita el modelo), así que sus abstenciones salen a 0 %.

Modo simulado (por defecto): embeddings deterministas por *hashing* de términos
y un selector léxico (:class:`SelectorLexico`) que hace de LLM y contabiliza los
tokens del prompt que habría enviado. Es reproducible bit a bit y sirve para
probar la fontanería y el efecto de la estructura; el veredicto sobre calidad
de razonamiento exige ``--real``.

Métricas: exactitud de la cita (top-1 y en top-k: norma + artículo),
abstenciones correctas, falsas abstenciones, latencia (medida y estimada con
LLM real) y coste por consulta (tokens × precio configurable).
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

import click
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from asistente.conocimiento.llm.arbol import (
    RECUPERADORES,
    Consumo,
    SelectorLexico,
    SelectorLLM,
    envolver_recuperador,
    ruta_headings,
    tokens,
)

RAIZ = Path(__file__).resolve().parents[3]
CORPUS_POR_DEFECTO = RAIZ / "eval_data" / "recuperacion" / "corpus_fiscal.jsonl"
CONSULTAS_POR_DEFECTO = RAIZ / "eval_data" / "recuperacion" / "fiscal.jsonl"
_ARTICULO = re.compile(r"art[íi]culo\s+(\d+)", re.I)


# --------------------------------------------------------------------------
# Datos
# --------------------------------------------------------------------------


def cargar_corpus(ruta: Path) -> list[Document]:
    docs = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        d = json.loads(linea)
        headings = d["headings"]
        docs.append(
            Document(
                page_content=f"{headings[-1]}. {d['texto']}",
                metadata={
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


def pagina_aeat(doc: Document) -> str | None:
    """Hash of the AEAT page a chunk comes from, or ``None`` if it is not an AEAT page."""
    m = _PAGINA_AEAT.search(str((doc.metadata or {}).get("source_file") or (doc.metadata or {}).get("source") or ""))
    return m.group(1) if m else None


def cargar_corpus_de_indice(ruta: Path, *, aeat: bool = True) -> list[Document]:
    """The chunks of the cited norms (and the AEAT pages) as the real processor indexed them.

    Reads ``index.pkl`` of a FAISS index. Text and heading path are kept as Docling left them; ``norma`` /
    ``documento`` are stamped from the BOE id in the file name so the citation can be scored. With ``aeat``
    the AEAT pages compete too, as they do in production (``norma="AEAT"``; the page is scored by its hash).
    Vectors are recomputed by the benchmark's embedder.
    """
    import pickle  # noqa: S403 - a local index this project built (same trust as FAISS_TRUSTED_INDEX)

    docstore, _ = pickle.loads((ruta / "index.pkl").read_bytes())  # noqa: S301
    docs = []
    for d in docstore._dict.values():
        fuente = str(d.metadata.get("source_file") or d.metadata.get("source") or "")
        boe = next((b for b in NORMAS_BOE if b in fuente), None)
        if boe is not None:
            norma, documento = NORMAS_BOE[boe]
        elif aeat and pagina_aeat(d):
            norma, documento = "AEAT", None
        else:
            continue
        meta = {**d.metadata, "norma": norma}
        if documento:
            meta["documento"] = documento
        docs.append(Document(page_content=d.page_content, metadata=meta))
    return docs


def manifiesto_de_corpus(docs: Sequence[Document]) -> dict:
    """Which citations exist in a corpus: article numbers per norm and AEAT pages (hash → title)."""
    normas: dict[str, set[str]] = {}
    aeat: dict[str, str] = {}
    for d in docs:
        h = pagina_aeat(d) if d.metadata.get("norma") == "AEAT" else None
        if h:
            ruta = ruta_headings(d)
            aeat.setdefault(h, ruta[0] if ruta else "")
            continue
        _, articulo = cita_de(d)
        if articulo:
            normas.setdefault(str(d.metadata.get("norma")), set()).add(articulo)
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


def cita_de(doc: Document) -> tuple[str, str | None]:
    """(norma/fuente, nº de artículo) que citaría el grounding gate para ``doc``."""
    meta = doc.metadata or {}
    ruta = ruta_headings(doc)
    articulo = None
    for h in reversed(ruta):
        m = _ARTICULO.search(h)
        if m:
            articulo = m.group(1)
            break
    fuente = " ".join(str(meta.get(k, "")) for k in ("norma", "documento", "source_file", "title"))
    return fuente, articulo


def es_cita_correcta(doc: Document, espera: dict) -> bool:
    """Whether ``doc`` is an acceptable source for the expected citation.

    * Law article: same norm and article number (``alternativas`` lists other articles that also answer).
    * AEAT page (``{"fuente": "AEAT", "pagina": <hash>}``): any chunk of that page counts. A page has no
      articles, so the unit the grounding gate would cite is the page itself.
    """
    if "pagina" in espera:
        return bool(pagina_aeat(doc) == espera["pagina"])
    fuente, articulo = cita_de(doc)
    norma = str((doc.metadata or {}).get("norma") or "").upper()
    for o in (espera, *espera.get("alternativas", [])):
        # Same norm by equality when the chunk carries one: a substring test let "LIS" match "LISD".
        misma = norma == o["norma"].upper() if norma else o["norma"].upper() in fuente.upper()
        if articulo == str(o["articulo"]) and misma:
            return True
    return False


# --------------------------------------------------------------------------
# Recuperador base: el buscador del agente (FaissCorpusSearcher)
# --------------------------------------------------------------------------


class EmbeddingsHash(Embeddings):
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


class VectoresEnMemoria:
    """The part of the FAISS vector store that :class:`FaissCorpusSearcher` uses, over a numpy matrix.

    Exact cosine search (FAISS' flat index is exact too), so the ranking matches the real store without building
    one: ``similarity_search(query, k=…, filter=…, fetch_k=…)`` with the filter applied after the ``fetch_k``
    nearest neighbours, as FAISS does.
    """

    def __init__(self, corpus: Sequence[Document], embeddings: Embeddings) -> None:
        import numpy as np

        self.corpus = list(corpus)
        self.embeddings = embeddings
        matriz = np.asarray(embeddings.embed_documents([d.page_content for d in self.corpus]), dtype=np.float32)
        matriz /= np.clip(np.linalg.norm(matriz, axis=1, keepdims=True), 1e-12, None)
        self.matriz = matriz
        self._cache: dict[str, object] = {}

    def similitudes(self, query: str):
        import numpy as np

        if query not in self._cache:
            q = np.asarray(self.embeddings.embed_query(query), dtype=np.float32)
            q /= np.linalg.norm(q) or 1.0
            self._cache[query] = self.matriz @ q
        return self._cache[query]

    def similarity_search(self, query: str, k: int = 4, filter=None, fetch_k: int = 20, **_: object) -> list[Document]:
        import numpy as np

        orden = [int(i) for i in np.argsort(-self.similitudes(query), kind="stable")]
        if filter is None:
            return [self.corpus[i] for i in orden[:k]]
        vecinos = [self.corpus[i] for i in orden[:fetch_k]]
        return [d for d in vecinos if filter(d.metadata)][:k]


class RecuperadorAgente(BaseRetriever):
    """The ``faiss`` variant: exactly what the agent's ``search_corpus`` tool retrieves.

    :class:`FaissCorpusSearcher` with no filters: the ``primera_etapa`` nearest chunks, reordered by the
    cross-encoder when there is one (``--real`` with ``RERANKER_ENABLED``), the first ``k`` sanitized. It never
    abstains on its own: in the agent, abstaining is the grounding gate's call over what the model cites.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    buscador: object
    k: int = 3

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        from asistente.nucleo.agent.search import SearchFilters

        return list(self.buscador.search(query, k=self.k, filters=SearchFilters()))  # type: ignore[attr-defined]


def construir_base(
    corpus: list[Document],
    embeddings: Embeddings,
    *,
    k: int,
    primera_etapa: int = 30,
    reranker: object | None = None,
) -> RecuperadorAgente:
    from asistente.nucleo.agent.search import FaissCorpusSearcher

    buscador = FaissCorpusSearcher(
        VectoresEnMemoria(corpus, embeddings), reranker=reranker, first_stage_k=primera_etapa
    )
    return RecuperadorAgente(buscador=buscador, k=k)


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


def evaluar_variante(nombre: str, recuperador: BaseRetriever, consultas: Sequence[dict], precios: Precios) -> dict:
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
        if q["tipo"] == "cita":
            fila["top1"] = bool(docs) and es_cita_correcta(docs[0], q["espera"])
            fila["en_k"] = any(es_cita_correcta(d, q["espera"]) for d in docs)
        filas.append(fila)
    return {"variante": nombre, "resumen": resumir(filas), "filas": filas}


def _etiqueta(doc: Document) -> str:
    h = pagina_aeat(doc)
    if h and doc.metadata.get("norma") == "AEAT":
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
    variantes: Sequence[str] = RECUPERADORES,
    corpus_path: Path = CORPUS_POR_DEFECTO,
    consultas_path: Path = CONSULTAS_POR_DEFECTO,
    embeddings: Embeddings | None = None,
    selector=None,
    k: int = 3,
    precios: Precios | None = None,
    indice: Path | None = None,
    aeat: bool = True,
    primera_etapa: int = 30,
    reranker: object | None = None,
) -> dict:
    """Run every variant over the same corpus and queries."""
    corpus = cargar_corpus_de_indice(indice, aeat=aeat) if indice is not None else cargar_corpus(corpus_path)
    consultas = cargar_consultas(consultas_path)
    precios = precios or Precios(0.15, 0.60, 800.0)
    base = construir_base(corpus, embeddings or EmbeddingsHash(), k=k, primera_etapa=primera_etapa, reranker=reranker)
    selector = selector or SelectorLexico()
    resultados = [
        evaluar_variante(v, envolver_recuperador(v, base, corpus, selector, k=k), consultas, precios) for v in variantes
    ]
    return {
        "corpus": str(indice)
        if indice is not None
        else (str(corpus_path.relative_to(RAIZ)) if corpus_path.is_relative_to(RAIZ) else str(corpus_path)),
        "chunks": len(corpus),
        "chunks_aeat": sum(d.metadata.get("norma") == "AEAT" for d in corpus),
        "consultas": len(consultas),
        "k": k,
        "primera_etapa": primera_etapa,
        "reranker": reranker is not None,
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
    return "\n".join(out)


class _ClaudeTexto:
    """``.invoke(prompt) -> str`` over the Anthropic SDK (``ANTHROPIC_MODEL``), for :class:`SelectorLLM`."""

    def __init__(self) -> None:
        import anthropic

        from asistente.plataforma.settings import ANTHROPIC_MODEL

        self._cliente = anthropic.Anthropic()
        self._modelo = ANTHROPIC_MODEL

    def invoke(self, prompt: str) -> str:
        r = self._cliente.messages.create(
            model=self._modelo, max_tokens=1024, messages=[{"role": "user", "content": prompt}]
        )
        return "".join(getattr(b, "text", "") for b in r.content)


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
@click.option("--variantes", default=",".join(RECUPERADORES), help="Lista separada por comas.")
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
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    default=None,
    help="Evalúa sobre los chunks reales de un índice FAISS del procesador (las normas citadas).",
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
@click.option("--precio-entrada", default=0.15, show_default=True, help="Precio por 1M tokens de entrada.")
@click.option("--precio-salida", default=0.60, show_default=True, help="Precio por 1M tokens de salida.")
@click.option(
    "--latencia-llm-ms", default=800.0, show_default=True, help="Latencia supuesta por llamada LLM (simulado)."
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
    precio_entrada: float,
    precio_salida: float,
    latencia_llm_ms: float,
    salida: Path | None,
) -> None:
    from asistente.plataforma import settings

    if escribir_manifiesto is not None:
        if indice is None:
            raise click.UsageError("--escribir-manifiesto necesita --indice")
        manifiesto = manifiesto_de_corpus(cargar_corpus_de_indice(indice, aeat=True))
        escribir_manifiesto.write_text(json.dumps(manifiesto, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        click.echo(f"{sum(map(len, manifiesto['normas'].values()))} artículos, {len(manifiesto['aeat'])} páginas AEAT")
        return
    if primera_etapa is None:
        primera_etapa = settings.RERANKER_FIRST_STAGE_K
    embeddings = None
    selector = None
    reranker = None
    if real:
        from asistente.conocimiento.llm.embeddings import create_embeddings

        embeddings = create_embeddings()
        if settings.RERANKER_ENABLED:
            from asistente.conocimiento.llm.reranker import MLXReranker

            reranker = MLXReranker(model_path=settings.RERANKER_MODEL, top_k=settings.RERANKER_TOP_K)
        selector = SelectorLLM(_ClaudeTexto() if proveedor == "anthropic" else _OpenAITexto())
        latencia_llm_ms = 0.0  # con LLM real la latencia ya va medida
    resultado = ejecutar(
        variantes=[v.strip() for v in variantes.split(",") if v.strip()],
        corpus_path=corpus_path,
        consultas_path=consultas_path,
        embeddings=embeddings,
        selector=selector,
        k=k,
        precios=Precios(precio_entrada, precio_salida, latencia_llm_ms),
        indice=indice,
        aeat=aeat,
        primera_etapa=primera_etapa,
        reranker=reranker,
    )
    resultado["modo"] = "real" if real else "simulado"
    if salida:
        salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    click.echo(tabla_markdown(resultado))


if __name__ == "__main__":
    cli()
