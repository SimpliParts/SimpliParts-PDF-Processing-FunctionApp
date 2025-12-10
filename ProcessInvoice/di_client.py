import os
import requests
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobClient


def _blob_client_from_env(url: str) -> Optional[BlobClient]:
    """
    Build a BlobClient using connection string when the URL is private (no SAS).
    """
    parsed = urlparse(url)
    if not parsed.netloc.endswith(".blob.core.windows.net"):
        return None
    path_parts = parsed.path.lstrip("/").split("/", 1)
    if len(path_parts) != 2:
        return None
    container, blob_name = path_parts

    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if conn_str:
        return BlobClient.from_connection_string(conn_str, container_name=container, blob_name=blob_name)
    return None


def download_pdf(url: str) -> bytes:
    """
    Download a PDF via SAS/public URL if possible; otherwise use account credentials from env.
    """
    # Try direct HTTP first (covers SAS/public)
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        return resp.content
    except Exception:
        # Fallback to SDK using account credentials
        client = _blob_client_from_env(url)
        if not client:
            raise
        downloader = client.download_blob()
        return downloader.readall()


def analyze_with_di(pdf_bytes: bytes) -> Dict[str, Any]:
    endpoint = os.environ["AZURE_FORMRECOGNIZER_ENDPOINT"]
    key = os.environ["AZURE_FORMRECOGNIZER_KEY"]
    client = DocumentAnalysisClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    poller = client.begin_analyze_document("prebuilt-read", pdf_bytes)
    result = poller.result()
    return result.to_dict()


def summarize_di(di_payload: Dict[str, Any]) -> Dict[str, Any]:
    documents = di_payload.get("documents") or []
    pages = di_payload.get("pages") or []
    return {
        "documents": len(documents),
        "pages": len(pages),
        "model_id": di_payload.get("modelId"),
    }

