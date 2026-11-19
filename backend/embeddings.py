"""Multilingual sentence embeddings with a dependency-free fallback.

Primary:  sentence-transformers `paraphrase-multilingual-MiniLM-L12-v2` (50+ languages,
          including Albanian and Serbian; 384-dim; runs on CPU at roughly 1-2k sentences/s).
Fallback: TF-IDF over character 3–5-grams (scikit-learn). Catches copy-paste with
          synonym swaps reasonably well but not true paraphrase or cross-language matches.

The engine reads `embedder.kind` to pick thresholds suited to each backend.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading

import numpy as np

log = logging.getLogger("htccip.embeddings")

# Cosine thresholds per backend (calibrate on your own labelled data before relying on them).
THRESHOLDS = {
    "sbert": {"near_dup": 0.82, "narrative": 0.60},
    "tfidf": {"near_dup": 0.55, "narrative": 0.30},
}


class Embedder:
    def __init__(self, model_name: str | None = None, allow_fallback: bool = True):
        self.model_name = model_name or os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        self._model = None
        self._lock = threading.Lock()
        self._cache: dict[str, np.ndarray] = {}
        self.kind = "tfidf"
        if os.getenv("EMBED_BACKEND", "auto") != "tfidf":
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore

                self._model = SentenceTransformer(self.model_name)
                self.kind = "sbert"
                log.info("Loaded sentence-transformers model %s", self.model_name)
            except Exception as e:  # pragma: no cover - depends on environment
                if not allow_fallback:
                    raise
                log.warning("sentence-transformers unavailable (%s); using TF-IDF char n-gram fallback", e)

    @property
    def thresholds(self) -> dict:
        return THRESHOLDS[self.kind]

    def encode(self, texts: list[str]) -> np.ndarray:
        """Return L2-normalised vectors, one row per text."""
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        if self.kind == "sbert":
            keys = [hashlib.sha1(t.encode("utf-8")).hexdigest() for t in texts]
            missing = [(k, t) for k, t in zip(keys, texts) if k not in self._cache]
            if missing:
                with self._lock:
                    vecs = self._model.encode([t for _, t in missing], batch_size=64, normalize_embeddings=True, show_progress_bar=False)
                for (k, _), v in zip(missing, vecs):
                    self._cache[k] = np.asarray(v, dtype=np.float32)
            return np.vstack([self._cache[k] for k in keys])
        # TF-IDF fallback: fitted per call on the batch (so vocabulary matches the batch)
        from sklearn.feature_extraction.text import TfidfVectorizer

        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1, sublinear_tf=True, lowercase=True)
        m = vec.fit_transform(texts).astype(np.float32)
        m = m.toarray()
        n = np.linalg.norm(m, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return m / n
