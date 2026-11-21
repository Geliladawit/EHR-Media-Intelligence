"""Sentence-transformer embedding wrapper (all-MiniLM-L6-v2, 384-dim)."""

from __future__ import annotations

import numpy as np

from ehr_media import config

EMBEDDING_DIM = 384

# Prefer a vendored local copy (models/all-MiniLM-L6-v2) so the app works
# offline; falls back to the HuggingFace hub name.
_LOCAL_MODEL_DIR = config.ROOT / "models" / "all-MiniLM-L6-v2"
_MODEL_NAME = str(_LOCAL_MODEL_DIR) if _LOCAL_MODEL_DIR.exists() \
    else "sentence-transformers/all-MiniLM-L6-v2"

_model = None


def get_model():
    """Lazy-load the embedding model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed documents; returns L2-normalized float32 matrix."""
    if not texts:
        return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    vecs = get_model().encode(texts, normalize_embeddings=True,
                              convert_to_numpy=True, show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)


def embed_query(text: str) -> np.ndarray:
    """Embed a single query; returns shape (1, dim) normalized."""
    return embed_texts([text])
