import logging
import os
from typing import Any, Dict, List, Optional

from azure.core.credentials import AzureKeyCredential
from openai import AzureOpenAI


def _maybe_embedding_client() -> Optional[AzureOpenAI]:
    endpoint = os.environ.get("AZURE_OPENAI_EMBEDDING_ENDPOINT")
    key = os.environ.get("AZURE_OPENAI_EMBEDDING_KEY")
    api_version = os.environ.get("AZURE_OPENAI_EMBEDDING_API_VERSION", "2024-12-01-preview")
    if not endpoint or not key:
        return None
    return AzureOpenAI(
        credential=AzureKeyCredential(key),
        azure_endpoint=endpoint,
        api_version=api_version,
    )


def generate_embedding(text: str) -> Optional[List[float]]:
    client = _maybe_embedding_client()
    deployment = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME")
    model = os.environ.get("AZURE_OPENAI_EMBEDDING_MODEL_NAME", deployment)
    if not client or not deployment or not text:
        return None
    try:
        resp = client.embeddings.create(
            model=deployment,
            input=text[:8000],  # guard input length
        )
        return resp.data[0].embedding  # type: ignore[attr-defined]
    except Exception as exc:
        logging.warning("Embedding generation failed: %s", exc)
        return None


def embedding_text(line: Dict[str, Any]) -> str:
    parts = [
        str(line.get("part_number") or ""),
        str(line.get("brand") or ""),
        str(line.get("description") or ""),
        " ".join(line.get("categories") or []),
    ]
    return " | ".join([p for p in parts if p])

