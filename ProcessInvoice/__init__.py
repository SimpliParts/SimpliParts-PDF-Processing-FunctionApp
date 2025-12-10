import json
import logging
import os
import re
from typing import Dict, Any, List, Optional

import azure.functions as func
import requests

from .di_client import analyze_with_di, download_pdf, summarize_di
from .embeddings import embedding_text, generate_embedding
from .gemini import (
    ensure_gemini,
    gemini_extract_from_di,
    gemini_extract_from_pdf,
    gemini_reconcile,
)


def _clean_part_number(part_number: Optional[str]) -> Optional[str]:
    if not part_number:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", part_number).upper()
    return cleaned or None


def _primary_category(categories: Optional[List[str]]) -> Optional[str]:
    if not categories:
        return None
    return categories[0]


def _line_code_from_brand(brand: Optional[str]) -> Optional[str]:
    if not brand:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", brand).upper()
    return cleaned[:3] or None


def _build_db_ready(source_blob_url: str, final_data: Dict[str, Any], shop_id: Optional[str]) -> Dict[str, Any]:
    header = final_data.get("header") or {}
    totals = final_data.get("totals") or {}
    line_items = final_data.get("line_items") or []

    ro_number = header.get("invoice_number") or header.get("po_number")
    repair_order = {
        "shop_id": shop_id,
        "ro_number": ro_number,
        "vendor_name": header.get("vendor_name"),
        "invoice_date": header.get("invoice_date"),
        "total_spend": totals.get("grand_total"),
        "status": "processing",
        "file_path": source_blob_url,
        "vehicle_year": None,
        "vehicle_make": None,
        "vehicle_model": None,
        "vehicle_vin": None,
    }

    mapped_lines = []
    for line in line_items:
        mapped_lines.append(
            {
                "ro_id": None,  # to be set after inserting repair_order
                "shop_id": shop_id,
                "part_number": line.get("part_number"),
                "clean_part_number": _clean_part_number(line.get("part_number")),
                "line_code": _line_code_from_brand(line.get("brand")),
                "description": line.get("description"),
                "quantity": line.get("quantity"),
                "unit_cost": line.get("unit_price"),
                "is_core": line.get("is_core"),
                "category": _primary_category(line.get("categories")),
                "embedding": line.get("embedding"),
                # carry extras if the client wants to persist them separately
                "core_charge": line.get("core_charge"),
                "line_discount": line.get("line_discount"),
                "line_total": line.get("line_total"),
                "tax_rate": line.get("tax_rate"),
                "taxability": line.get("taxability"),
                "uom": line.get("uom"),
                "brand": line.get("brand"),
            }
        )

    return {
        "repair_order": repair_order,
        "line_items": mapped_lines,
    }


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("ProcessInvoice triggered")

    # Optional static header auth (set EXPECT_HEADER_NAME and EXPECT_HEADER_VALUE env vars)
    expect_header_name = os.environ.get("EXPECT_HEADER_NAME")
    expect_header_value = os.environ.get("EXPECT_HEADER_VALUE")
    if expect_header_name and expect_header_value:
        actual_value = req.headers.get(expect_header_name)
        if actual_value != expect_header_value:
            return func.HttpResponse("Unauthorized", status_code=401)

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON body", status_code=400)

    blob_url = body.get("blob_url")
    if not blob_url:
        return func.HttpResponse("blob_url is required", status_code=400)

    try:
        pdf_bytes = download_pdf(blob_url)
        di_payload = analyze_with_di(pdf_bytes)
        ensure_gemini()
        pass_a = gemini_extract_from_di(di_payload)
        pass_b = gemini_extract_from_pdf(pdf_bytes)
        final_payload = gemini_reconcile(pass_a, pass_b, di_payload)

        # Optional: generate embeddings for each final line item if Azure OpenAI embedding config is present
        line_items = (final_payload.get("data") or {}).get("line_items") or []
        embeddings_generated = 0
        for line in line_items:
            emb = generate_embedding(embedding_text(line))
            if emb:
                line["embedding"] = emb
                embeddings_generated += 1

        db_ready = _build_db_ready(blob_url, final_payload.get("data") or {}, body.get("shop_id"))

        response = {
            "final": final_payload,
            "pass_a": pass_a,
            "pass_b": pass_b,
            "di_summary": summarize_di(di_payload),
            "source": {
                "blob_url": blob_url,
                "invoice_hint": body.get("invoice_id") or body.get("po_number"),
                "vendor_hint": body.get("vendor_hint"),
                "shop_id": body.get("shop_id"),
            },
            "embedding": {
                "enabled": embeddings_generated > 0,
                "count": embeddings_generated,
                "deployment": os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME"),
                "model": os.environ.get("AZURE_OPENAI_EMBEDDING_MODEL_NAME"),
            },
            "db_ready": db_ready,
        }
        return func.HttpResponse(json.dumps(response), status_code=200, mimetype="application/json")
    except requests.HTTPError as http_err:
        logging.exception("Download failed")
        return func.HttpResponse(f"Failed to download PDF: {http_err}", status_code=400)
    except Exception as ex:
        logging.exception("Processing error")
        return func.HttpResponse(f"Processing failed: {ex}", status_code=500)

