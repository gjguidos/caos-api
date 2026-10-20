class PrecomputedEmbeddingFunction:
    def __init__(self, embeddings: list[list[float]]):
        self._embeddings = embeddings
        self._index = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        result = self._embeddings[self._index : self._index + len(texts)]
        self._index += len(texts)
        return result

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
