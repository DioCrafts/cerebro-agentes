"""Embedding model factory — MLX (Apple Silicon native, bf16) only.

Both indexing (``asistente.conocimiento.processor.document_loader``) and querying
(``asistente.nucleo.agent.search``) build
their embedder here so the corpus index and the query path always share the
same vector space.

The runtime backend is **MLX** via the ``mlx-embeddings`` package: bf16
weights, no quantization (a hard project rule), and ~1.35× the throughput of
PyTorch+MPS on the Qwen3-Embedding-0.6B reference workload (measured). The
target model must be a locally converted MLX directory — convert with
``scripts/convert_to_mlx.py``.

This module deliberately drops every PyTorch / sentence-transformers /
langchain-huggingface code path that lived here before, so importing this
module on a non-Apple-Silicon machine will fail at the ``mlx_embeddings``
import. That's intentional: the rest of the stack (Docling, sqlite-vec) targets
the same platform, and an embedder mismatch would silently break retrieval.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
from typing import Any, Protocol

from asistente.plataforma import settings

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    """What the index (ingest and search) needs from an embedder: one vector per document, one per query."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class MLXEmbeddings:
    """Apple-Silicon-native embedder over MLX bf16 weights.

    The constructor loads the model and tokenizer once and reuses them. The
    query-time instruction prefix (e.g. ``"Instruct: ... Query:"``) is read
    from ``config_sentence_transformers.json`` in the model directory if
    present — Qwen3-Embedding ships one and skipping it costs 1-5% retrieval
    quality.
    """

    def __init__(
        self,
        *,
        model_path: str,
        batch_size: int = 32,
        max_seq_length: int = 512,
    ) -> None:
        # Lazy import keeps this module importable for type-checking and
        # tests on machines without the mlx wheel (Linux CI, x86 macOS).
        from mlx_embeddings import load

        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(
                f"MLX model directory not found at {model_path!r}. "
                f"Run `scripts/convert_to_mlx.py` to produce it, "
                f"or set EMBEDDING_MODEL to a valid converted-model path."
            )

        self._model, self._tokenizer = load(str(path))
        self._batch_size = batch_size
        self._max_seq_length = max_seq_length
        self._lock = threading.Lock()

        # Optional sentence-transformers-style prompts. Qwen3-Embedding's
        # config maps ``query`` → instruction prefix and ``document`` → "".
        self._query_prefix = ""
        prompts_path = path / "config_sentence_transformers.json"
        if prompts_path.exists():
            try:
                cfg = json.loads(prompts_path.read_text(encoding="utf-8"))
                self._query_prefix = (cfg.get("prompts") or {}).get("query", "")
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "Could not parse %s (%s); query-time instruction prefix disabled.",
                    prompts_path,
                    exc,
                )

        logger.info(
            "MLX embedder ready: model=%s  batch_size=%d  max_seq_length=%d  query_prefix=%r",
            model_path,
            batch_size,
            max_seq_length,
            self._query_prefix,
        )

    # ---------------- Embedder interface ----------------------------------- #

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # Documents go in raw — sentence-transformers convention for Qwen3
        # (the ``document`` prompt is the empty string in its shipped config).
        return self._encode(list(texts), prefix="")

    def embed_query(self, text: str) -> list[float]:
        return self._encode([text], prefix=self._query_prefix)[0]

    # ---------------- Internals -------------------------------------------- #

    def _encode(self, texts: list[str], *, prefix: str) -> list[list[float]]:
        if not texts:
            return []
        # The model is shared by the whole process (see ``create_embeddings``) and MLX is not safe to call from two
        # threads at once.
        with self._lock:
            return self._encode_sin_cerrojo(texts, prefix=prefix)

    def _encode_sin_cerrojo(self, texts: list[str], *, prefix: str) -> list[list[float]]:
        if prefix:
            texts = [f"{prefix} {t}" for t in texts]

        all_vecs: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            tokens = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self._max_seq_length,
                return_tensors="mlx",
            )
            output: Any = self._model(
                input_ids=tokens["input_ids"],
                attention_mask=tokens["attention_mask"],
            )
            # ``text_embeds`` is last-token-pooled + L2-normalized inside the
            # MLX impl, matching the HF sentence-transformers recipe.
            embeds = output.text_embeds
            # MLX bf16 → Python floats for sqlite-vec / numpy ingestion.
            all_vecs.extend(embeds.tolist())
        return all_vecs


def create_embeddings(model: str | None = None) -> Embedder:
    """Return the local MLX embedder used everywhere in the app.

    Args:
        model: Override for ``settings.EMBEDDING_MODEL`` — a path to a
            locally converted MLX model directory.
    """
    clave = (
        str(model or settings.EMBEDDING_MODEL),
        int(settings.EMBEDDING_BATCH_SIZE),
        int(settings.EMBEDDING_MAX_SEQ_LENGTH),
    )
    # ONE embedder per process and model: loading the weights twice doubles the memory, and two copies on one GPU
    # thrash it. The lock also makes two threads asking at once load it only once.
    with _CACHE_LOCK:
        embedder = _CACHE.get(clave)
        if embedder is None:
            embedder = MLXEmbeddings(model_path=clave[0], batch_size=clave[1], max_seq_length=clave[2])
            _CACHE[clave] = embedder
        return embedder


_CACHE: dict[tuple[str, int, int], MLXEmbeddings] = {}
_CACHE_LOCK = threading.Lock()
