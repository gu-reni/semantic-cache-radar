from pathlib import Path

from sentence_transformers import SentenceTransformer

from gateway.app.config import Settings


class EmbeddingService:
    """Generate normalized multilingual embeddings from the local model."""

    def __init__(self, settings: Settings) -> None:
        model_path = Path(settings.embedding_model_path)
        self._model = SentenceTransformer(
            str(model_path),
            local_files_only=True,
        )

    def encode_query(self, text: str) -> list[float]:
        vector = self._model.encode(
            f"query: {text}",
            normalize_embeddings=True,
        )
        return vector.tolist()

    def encode_passage(self, text: str) -> list[float]:
        vector = self._model.encode(
            f"passage: {text}",
            normalize_embeddings=True,
        )
        return vector.tolist()
