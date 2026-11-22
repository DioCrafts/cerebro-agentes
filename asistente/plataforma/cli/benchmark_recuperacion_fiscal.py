"""Benchmark reproducible de recuperadores del módulo fiscal: faiss vs pageindex vs híbrido.

Uso::

    python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal            # simulado, sin red
    python -m asistente.plataforma.cli.benchmark_recuperacion_fiscal --real     # embeddings + LLM reales

Datos (versionados en ``eval_data/recuperacion/``):

* ``corpus_fiscal.jsonl`` — chunks de artículos (LIRPF, LIVA, LIS, LGT, LISD)
  con la ruta de headings tal como la deja Docling (Título > Capítulo > Artículo).
* ``fiscal.jsonl`` — consultas con el artículo que debe citarse o ``abstencion``.
  Las que llevan ``origen`` derivan de las citas que exigen los evals del
  módulo (``eval_data/modulos/fiscal.jsonl``): esos evals prueban calculadoras,
  no recuperación, así que aquí se convierten en preguntas de normativa.

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
    fuente, articulo = cita_de(doc)
    return articulo == str(espera["articulo"]) and espera["norma"].upper() in fuente.upper()


# --------------------------------------------------------------------------
# Recuperador base simulado (denso + BM25 con RRF, como BM25_ENABLED en prod)
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


class RecuperadorDensoBM25(BaseRetriever):
    """FAISS (umbral de similitud, como el ``EmbeddingsFilter``) + BM25 fusionados por RRF.

    Si ningún chunk denso supera el umbral devuelve ``[]``: es la abstención del
    pipeline actual (el filtro deja el contexto vacío y el gate se abstiene).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    faiss: object
    bm25: object
    corpus: list
    k: int = 3
    umbral: float = 0.25

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        densos = self.faiss.similarity_search_with_relevance_scores(query, k=20)  # type: ignore[attr-defined]
        densos = [(d, s) for d, s in densos if s >= self.umbral]
        if not densos:
            return []
        rrf: dict[str, float] = {}
        por_id: dict[str, Document] = {}
        for rango, (d, _s) in enumerate(densos):
            cid = d.metadata["chunk_id"]
            por_id[cid] = d
            rrf[cid] = rrf.get(cid, 0.0) + 0.6 / (60 + rango)
        puntuaciones = self.bm25.get_scores(sorted(tokens(query)))  # type: ignore[attr-defined]
        orden = sorted(range(len(self.corpus)), key=lambda i: -puntuaciones[i])[:20]
        for rango, i in enumerate(orden):
            d = self.corpus[i]
            cid = d.metadata["chunk_id"]
            por_id[cid] = d
            rrf[cid] = rrf.get(cid, 0.0) + 0.4 / (60 + rango)
        mejores = sorted(rrf, key=lambda c: -rrf[c])[: self.k]
        return [por_id[c] for c in mejores]


def construir_base(corpus: list[Document], embeddings: Embeddings, *, k: int, umbral: float) -> BaseRetriever:
    from langchain_community.vectorstores import FAISS
    from rank_bm25 import BM25Okapi

    faiss = FAISS.from_documents(corpus, embeddings, relevance_score_fn=lambda d: 1.0 - d / 2.0)
    bm25 = BM25Okapi([sorted(tokens(d.page_content)) for d in corpus])
    return RecuperadorDensoBM25(faiss=faiss, bm25=bm25, corpus=corpus, k=k, umbral=umbral)


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


def evaluar_variante(
    nombre: str, recuperador: BaseRetriever, consultas: Sequence[dict], precios: Precios
) -> dict:
    filas = []
    for q in consultas:
        t0 = time.perf_counter()
        docs = recuperador.invoke(q["consulta"])
        ms = (time.perf_counter() - t0) * 1000
        consumo = getattr(recuperador, "ultimo_consumo", None) or Consumo()
        fila = {
            "id": q["id"],
            "tipo": q["tipo"],
            "abstiene": not docs,
            "citas": [f"{cita_de(d)[0].split()[0]} art. {cita_de(d)[1]}" for d in docs],
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


def resumir(filas: list[dict]) -> dict:
    citas = [f for f in filas if f["tipo"] == "cita"]
    abst = [f for f in filas if f["tipo"] == "abstencion"]
    lat = [f["latencia_estimada_ms"] for f in filas]

    def frac(xs: list[bool]) -> float:
        return round(sum(xs) / len(xs), 3) if xs else 0.0

    return {
        "consultas": len(filas),
        "cita_top1": frac([f["top1"] for f in citas]),
        "cita_en_k": frac([f["en_k"] for f in citas]),
        "abstencion_correcta": frac([f["abstiene"] for f in abst]),
        "falsa_abstencion": frac([f["abstiene"] for f in citas]),
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
    umbral: float = 0.25,
    precios: Precios | None = None,
) -> dict:
    corpus = cargar_corpus(corpus_path)
    consultas = cargar_consultas(consultas_path)
    precios = precios or Precios(0.15, 0.60, 800.0)
    base = construir_base(corpus, embeddings or EmbeddingsHash(), k=k, umbral=umbral)
    selector = selector or SelectorLexico()
    resultados = [
        evaluar_variante(v, envolver_recuperador(v, base, corpus, selector, k=k), consultas, precios)
        for v in variantes
    ]
    return {
        "corpus": str(corpus_path.relative_to(RAIZ)) if corpus_path.is_relative_to(RAIZ) else str(corpus_path),
        "consultas": len(consultas),
        "k": k,
        "precios_por_millon_tokens": {"entrada": precios.entrada_por_millon, "salida": precios.salida_por_millon},
        "latencia_llm_ms_supuesta": precios.latencia_llm_ms,
        "variantes": resultados,
    }


def tabla_markdown(resultado: dict) -> str:
    cab = "| variante | cita top-1 | cita en top-k | abst. correctas | falsas abst. | lat. est. media (ms) | p95 (ms) | llamadas LLM | tokens | coste/consulta |"
    out = [cab, "|" + "---|" * 10]
    for v in resultado["variantes"]:
        r = v["resumen"]
        out.append(
            f"| {v['variante']} | {r['cita_top1']:.0%} | {r['cita_en_k']:.0%} | {r['abstencion_correcta']:.0%} | "
            f"{r['falsa_abstencion']:.0%} | {r['latencia_estimada_ms_media']} | {r['latencia_estimada_ms_p95']} | "
            f"{r['llamadas_llm_por_consulta']} | {r['tokens_por_consulta']} | {r['coste_por_consulta']:.6f} |"
        )
    return "\n".join(out)


@click.command()
@click.option("--variantes", default=",".join(RECUPERADORES), help="Lista separada por comas.")
@click.option("--corpus", "corpus_path", type=click.Path(path_type=Path), default=CORPUS_POR_DEFECTO)
@click.option("--consultas", "consultas_path", type=click.Path(path_type=Path), default=CONSULTAS_POR_DEFECTO)
@click.option("--real", is_flag=True, help="Embeddings del proyecto + LLM real (OPENAI_MODEL) como selector.")
@click.option("--k", default=3, show_default=True)
@click.option("--umbral", default=0.25, show_default=True, help="Umbral de similitud densa (abstención).")
@click.option("--precio-entrada", default=0.15, show_default=True, help="Precio por 1M tokens de entrada.")
@click.option("--precio-salida", default=0.60, show_default=True, help="Precio por 1M tokens de salida.")
@click.option("--latencia-llm-ms", default=800.0, show_default=True, help="Latencia supuesta por llamada LLM (simulado).")
@click.option("--salida", type=click.Path(path_type=Path), default=None, help="Guarda el JSON completo.")
def cli(
    variantes: str,
    corpus_path: Path,
    consultas_path: Path,
    real: bool,
    k: int,
    umbral: float,
    precio_entrada: float,
    precio_salida: float,
    latencia_llm_ms: float,
    salida: Path | None,
) -> None:
    embeddings = None
    selector = None
    if real:
        from langchain_openai import ChatOpenAI

        from asistente.conocimiento.llm.embeddings import create_embeddings
        from asistente.plataforma.settings import OPENAI_MODEL
        from asistente.plataforma.utils import get_openai_api_key

        embeddings = create_embeddings()
        selector = SelectorLLM(ChatOpenAI(model=OPENAI_MODEL, temperature=0, api_key=get_openai_api_key()))
        latencia_llm_ms = 0.0  # con LLM real la latencia ya va medida
    resultado = ejecutar(
        variantes=[v.strip() for v in variantes.split(",") if v.strip()],
        corpus_path=corpus_path,
        consultas_path=consultas_path,
        embeddings=embeddings,
        selector=selector,
        k=k,
        umbral=umbral,
        precios=Precios(precio_entrada, precio_salida, latencia_llm_ms),
    )
    resultado["modo"] = "real" if real else "simulado"
    if salida:
        salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    click.echo(tabla_markdown(resultado))


if __name__ == "__main__":
    cli()
