from functools import lru_cache
from langchain_openai import OpenAIEmbeddings
from app.config import get_settings

@lru_cache(maxsize=1)
def get_embeddings() -> OpenAIEmbeddings:
    """Singleton Gemini embedding model."""
    settings = get_settings()

    if not settings.jina_api_key:
        raise RuntimeError("GEMINI_API_KEY is empty - set it in the project-root .env file")

    return OpenAIEmbeddings(
        model=settings.jina_model,  # gemini-embedding-2-preview
        base_url=settings.jina_base_url,
        api_key=settings.jina_api_key,
        dimensions=settings.embedding_dim,
        check_embedding_ctx_length=False,
        request_timeout=120,
        max_retries=2,
    )