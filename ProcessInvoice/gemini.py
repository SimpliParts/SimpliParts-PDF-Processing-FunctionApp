import base64
import json
import os
from typing import Any, Dict

import google.generativeai as genai

from .categories import CATEGORIES


def coerce_json(text: str) -> Dict[str, Any]:
    """
    Best-effort JSON parsing from LLM output, handling fenced code blocks.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return json.loads(cleaned)


def ensure_gemini():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    genai.configure(api_key=api_key)


def gemini_model() -> genai.GenerativeModel:
    model_name = os.environ.get("GEMINI_MODEL_NAME", "gemini-2.5-pro")
    return genai.GenerativeModel(model_name)


def gemini_extract_from_di(di_payload: Dict[str, Any]) -> Dict[str, Any]:
    model = gemini_model()
    prompt = """
You are an expert at auto parts invoice extraction. Input is Azure Document Intelligence JSON (tables + key-values + text).
Return ONLY JSON with this schema:
{
  "header": {
    "vendor_name": ...,
    "invoice_number": ...,
    "invoice_date": ...,
    "po_number": ...,
    "customer_account": ...,
    "store_branch": ...,
    "salesperson": ...,
    "payment_terms": ...,
    "currency": ...
  },
  "totals": {
    "subtotal": ...,
    "tax": ...,
    "tax_rate": ...,
    "shipping": ...,
    "core_charges": ...,
    "discounts": ...,
    "fees": ...,
    "grand_total": ...,
    "amount_paid": ...,
    "balance_due": ...
  },
  "line_items": [
    {
      "line_number": ...,
      "part_number": ...,
      "description": ...,
      "brand": ...,
      "quantity": ...,
      "unit_price": ...,
      "line_discount": ...,
      "core_charge": ...,
      "line_total": ...,
      "taxability": ...,
      "tax_rate": ...,
      "uom": ...,
      "categories": [],   // array of values from the provided categories list; allow multiple
      "is_core": ...      // boolean if this line has a core charge nature
    }
  ]
}
Rules: normalize numbers, dates, currency; leave missing fields null; avoid hallucinating; include only content supported by the input.
Pick zero or more categories from this list (no free-form): """ + ", ".join(CATEGORIES) + """
"""
    content = json.dumps(di_payload)
    resp = model.generate_content([prompt, content], request_options={"timeout": 120})
    return coerce_json(resp.text)


def gemini_extract_from_pdf(pdf_bytes: bytes) -> Dict[str, Any]:
    model = gemini_model()
    prompt = """
You are an expert at auto parts invoice extraction. Input is the raw PDF. Extract the same schema as before.
Be conservative: if uncertain, return null and note uncertainty in a "warnings" array at the top level.
Return ONLY JSON following the same schema.
Pick zero or more categories from this list (no free-form): """ + ", ".join(CATEGORIES) + """
"""
    inline_pdf = {
        "mime_type": "application/pdf",
        "data": base64.b64encode(pdf_bytes).decode("utf-8"),
    }
    resp = model.generate_content([prompt, inline_pdf], request_options={"timeout": 180})
    return coerce_json(resp.text)


def gemini_reconcile(pass_a: Dict[str, Any], pass_b: Dict[str, Any], di_payload: Dict[str, Any]) -> Dict[str, Any]:
    model = gemini_model()
    prompt = """
You will reconcile two invoice extraction payloads.
Inputs:
- pass_a: from Azure DI JSON via Gemini
- pass_b: from raw PDF via Gemini
- di_totals: key-value/totals from DI to anchor math

Tasks:
- Choose the most supported value per field using DI totals and internal consistency (line sums vs totals).
- Recompute line totals and compare to provided totals; flag mismatches.
- Return JSON:
{
  "data": {header/totals/line_items schema},
  "warnings": [...],
  "confidence": "high|medium|low",
  "fields_needing_review": [path strings]
}
Return ONLY JSON.
Ensure line_items keep categories (multi-select from provided list) and is_core.
"""
    payload = {
        "pass_a": pass_a,
        "pass_b": pass_b,
        "di_totals": di_payload.get("documents") or di_payload,
    }
    resp = model.generate_content([prompt, json.dumps(payload)], request_options={"timeout": 180})
    return coerce_json(resp.text)

