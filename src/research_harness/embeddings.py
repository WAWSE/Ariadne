"""Embedding providers used by the persisted sqlite-vec index.

``hash`` is intentionally explicit and deterministic.  It is useful for
offline tests only and is not a semantic model.  FastEmbed is loaded lazily so
opening a project never downloads a model; callers must explicitly build an
index before a model is loaded.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_PROVIDER = "fastembed"
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_DIMENSION = 384
HASH_MODEL = "sha256-feature-hash-v1"


class EmbeddingError(ValueError):
    """A provider is unavailable or has incompatible identity."""


@dataclass(frozen=True)
class ProviderIdentity:
    provider: str
    model: str
    dimension: int


def _hash_vector(text: str, dimension: int = DEFAULT_DIMENSION) -> list[float]:
    """Signed feature hashing with deterministic token and bigram features."""
    vals = [0.0] * dimension
    tokens = re.findall(r"\w+", text.casefold(), flags=re.UNICODE)
    features: list[str] = tokens[:]
    features.extend(f"{a}\x00{b}" for a, b in zip(tokens, tokens[1:]))
    for feature in features:
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:8], "little") % dimension
        sign = 1.0 if digest[8] & 1 else -1.0
        vals[idx] += sign
    norm = math.sqrt(sum(v * v for v in vals))
    return [v / norm for v in vals] if norm else vals


class Embedder:
    identity: ProviderIdentity

    def embed(self, texts: Iterable[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


class HashEmbedder(Embedder):
    def __init__(self, dimension: int = DEFAULT_DIMENSION):
        if not isinstance(dimension, int) or dimension <= 0:
            raise EmbeddingError("hash embedding dimension must be a positive integer")
        self.identity = ProviderIdentity("hash", HASH_MODEL, int(dimension))

    def embed(self, texts: Iterable[str]) -> list[list[float]]:
        return [_hash_vector(str(text), self.identity.dimension) for text in texts]


class FastEmbedder(Embedder):
    def __init__(self, model: str = DEFAULT_MODEL, *, allow_download: bool = True,
                 cache_dir: Path | str | None = None):
        self.model = model
        self._model = None
        self.allow_download = allow_download
        self.cache_dir = str(Path(cache_dir)) if cache_dir is not None else None
        # bge-small-en-v1.5 is 384-dimensional.  Other configured models may
        # report their dimension once loaded; persisted metadata is authoritative.
        self.identity = ProviderIdentity("fastembed", model, DEFAULT_DIMENSION)
    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from fastembed import TextEmbedding
            kwargs = {"model_name": self.model, "cache_dir": self.cache_dir}
            if not self.allow_download:
                # FastEmbed 0.8 forwards this to its model downloader.  Do
                # not fall back to a constructor without this flag: that
                # fallback would turn an ordinary query into a network call.
                self._model = TextEmbedding(**kwargs, local_files_only=True)
            else:
                self._model = TextEmbedding(**kwargs)
        except Exception as exc:  # provider errors are user-visible, never fallback
            raise EmbeddingError(f"fastembed model {self.model!r} unavailable: {exc}") from exc
        return self._model

    def _vectors(self, values: Iterable[object]) -> list[list[float]]:
        try:
            output = [list(map(float, v)) for v in values]
        except Exception as exc:
            raise EmbeddingError(f"fastembed embedding failed: {exc}") from exc
        if output:
            dim = len(output[0])
            if dim <= 0 or any(len(v) != dim for v in output):
                raise EmbeddingError("fastembed returned invalid vector dimensions")
            if any(not math.isfinite(value) for vector in output for value in vector):
                raise EmbeddingError("fastembed returned non-finite vector values")
            self.identity = ProviderIdentity("fastembed", self.model, dim)
        return output

    def embed(self, texts: Iterable[str]) -> list[list[float]]:
        """Embed indexed documents with FastEmbed's passage specialization."""
        model = self._load()
        try:
            return self._vectors(model.passage_embed(list(texts)))
        except AttributeError as exc:
            raise EmbeddingError("fastembed provider lacks passage_embed") from exc

    def embed_one(self, text: str) -> list[float]:
        """Embed a query with FastEmbed's query specialization."""
        model = self._load()
        try:
            vectors = self._vectors(model.query_embed(text))
        except AttributeError as exc:
            raise EmbeddingError("fastembed provider lacks query_embed") from exc
        if len(vectors) != 1:
            raise EmbeddingError("fastembed returned an invalid query vector count")
        return vectors[0]


def make_embedder(provider: str, model: str | None = None, *, allow_download: bool = True,
                  dimension: int | None = None, cache_dir: Path | str | None = None) -> Embedder:
    provider = provider.lower()
    if provider == "hash":
        if model not in (None, HASH_MODEL):
            raise EmbeddingError(f"hash provider only supports model {HASH_MODEL!r}")
        return HashEmbedder(dimension or DEFAULT_DIMENSION)
    if provider == "fastembed":
        return FastEmbedder(model or DEFAULT_MODEL, allow_download=allow_download, cache_dir=cache_dir)
    raise EmbeddingError(f"unsupported embedding provider {provider!r}")


def record_text(record: dict) -> str:
    """Stable searchable text; source data remains untrusted content."""
    import json
    return " ".join([
        str(record.get("title", "")), str(record.get("summary", "")),
        " ".join(map(str, record.get("tags", []))),
        json.dumps(record.get("data", {}), ensure_ascii=False, sort_keys=True),
    ])
