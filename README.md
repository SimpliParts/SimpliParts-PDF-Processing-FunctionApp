# PDF Processing Function App

Azure Functions (Python) endpoint that ingests an invoice PDF from blob storage, runs Azure Document Intelligence for text extraction, performs two Gemini passes (DI JSON and raw PDF), reconciles the results, optionally generates embeddings, and returns a DB-ready payload.

## What it does
- Downloads the PDF from a SAS/public URL; if private and no SAS, falls back to `AZURE_STORAGE_CONNECTION_STRING`.
- Runs Document Intelligence `prebuilt-read`.
- Runs two Gemini passes and reconciles to a single invoice payload.
- Optionally generates Azure OpenAI embeddings per line item.
- Returns structured JSON plus a DB-ready shape for repair orders and line items.

## Prerequisites
- Python 3.11+, Azure Functions Core Tools.
- Azure resources: Form Recognizer (Document Intelligence) endpoint/key; optional Storage account connection string for private blobs.
- Google Gemini API key (Generative AI).
- Optional Azure OpenAI embedding deployment (for line-item vectors).

## Configuration (env vars)
- `AZURE_FORMRECOGNIZER_ENDPOINT`, `AZURE_FORMRECOGNIZER_KEY`
- `GEMINI_API_KEY`, optional `GEMINI_MODEL_NAME` (default `gemini-2.5-pro`)
- Embeddings (optional): `AZURE_OPENAI_EMBEDDING_ENDPOINT`, `AZURE_OPENAI_EMBEDDING_KEY`, `AZURE_OPENAI_EMBEDDING_API_VERSION` (default `2024-12-01-preview`), `AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME`, `AZURE_OPENAI_EMBEDDING_MODEL_NAME`
- Private blob fallback (optional): `AZURE_STORAGE_CONNECTION_STRING`
- Optional static header auth: `EXPECT_HEADER_NAME`, `EXPECT_HEADER_VALUE`

Do **not** commit secrets. Use `local.settings.json` for local dev only; configure App Settings in Azure for production.

## Local development
```bash
cd PDFProcessingFunctionApp
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
func start
```

The function key is required by default (`authLevel: function`). For testing only, you can temporarily set `authLevel` to `anonymous` in `ProcessInvoice/function.json`.

## Endpoint
- Method/route: `POST /api/process-invoice`
- Auth: Function key header (`x-functions-key`) unless you change `authLevel`. If `EXPECT_HEADER_*` is set, the request must include that header/value.

### Request body
```json
{
  "blob_url": "https://<storage>/container/file.pdf?...",
  "shop_id": "optional string",
  "invoice_id": "optional hint",
  "po_number": "optional hint",
  "vendor_hint": "optional"
}
```
`blob_url` is required. The function downloads the file; no base64 upload is needed.

### Response (fields of interest)
- `final`: reconciled invoice payload with `data.header`, `data.totals`, `data.line_items`, plus `warnings` and `fields_needing_review`.
- `pass_a` / `pass_b`: intermediate Gemini results (DI-based vs PDF-based).
- `di_summary`: basic DI metadata.
- `source`: echoes `blob_url` and optional hints.
- `embedding`: `{ enabled, count, deployment, model }`.
- `db_ready`: `{ repair_order, line_items[] }` mapped for downstream storage (line items include cleaned part numbers, derived line codes, and embeddings when configured).

Example (truncated):
```json
{
  "final": { "data": { "header": {...}, "totals": {...}, "line_items": [...] } },
  "db_ready": { "repair_order": {...}, "line_items": [...] },
  "embedding": { "enabled": true, "count": 5 }
}
```

## Deployment (Azure)
1. Create a Python Function App (Consumption/Premium) with FUNCTIONS_WORKER_RUNTIME=python.
2. Set all required App Settings (keys above) and your Function key policy.
3. Deploy:
   ```bash
   func azure functionapp publish <your-func-app-name>
   ```
4. Test:
   ```bash
   curl -X POST "https://<app>.azurewebsites.net/api/process-invoice?code=<function-key>" \
     -H "Content-Type: application/json" \
     -d '{"blob_url": "..."}'
   ```

## Notes and troubleshooting
- 401: missing/incorrect function key or `EXPECT_HEADER_*`.
- 400: invalid JSON or `blob_url` missing/unreachable.
- 500: upstream DI/Gemini errors; check Application Insights logs.
- Embeddings run only when all embedding env vars are present; otherwise they are skipped gracefully.

