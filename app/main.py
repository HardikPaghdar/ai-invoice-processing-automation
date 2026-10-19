from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="AI Invoice Processing Automation", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
processed: list[dict[str, Any]] = []

class InvoiceInput(BaseModel):
    text: str = Field(min_length=10, max_length=30000)

def amount(value: str) -> float:
    return round(float(value.replace(",", "").strip()), 2)

def field(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.I | re.M)
    return match.group(1).strip() if match else None

def parse_invoice(text: str) -> dict[str, Any]:
    invoice_number = field(r"^Invoice Number:\s*(.+)$", text)
    supplier = field(r"^Supplier:\s*(.+)$", text)
    invoice_date = field(r"^Invoice Date:\s*(.+)$", text)
    due_date = field(r"^Due Date:\s*(.+)$", text)
    items = []
    for line in re.findall(r"^Item:\s*(.+)$", text, re.I | re.M):
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3:
            continue
        try:
            quantity = float(parts[1])
            unit_price = amount(parts[2])
            items.append({"description": parts[0], "quantity": quantity, "unit_price": unit_price,
                          "line_total": round(quantity * unit_price, 2)})
        except ValueError:
            continue
    values = {}
    for key in ("Subtotal", "Tax", "Total"):
        raw = field(rf"^{key}:\s*([0-9,]+(?:\.[0-9]{{1,2}})?)$", text)
        try:
            values[key.lower()] = amount(raw) if raw is not None else None
        except ValueError:
            values[key.lower()] = None
    return {"invoice_number": invoice_number, "supplier": supplier, "invoice_date": invoice_date,
            "due_date": due_date, "items": items, **values}

def process_invoice(text: str) -> dict[str, Any]:
    parsed = parse_invoice(text)
    errors = []
    required = ["invoice_number", "supplier", "invoice_date", "subtotal", "tax", "total"]
    for key in required:
        if parsed.get(key) in (None, ""):
            errors.append(f"Missing required field: {key}")
    line_sum = round(sum(item["line_total"] for item in parsed["items"]), 2)
    if not parsed["items"]:
        errors.append("No valid line items found.")
    if parsed["subtotal"] is not None and abs(line_sum - parsed["subtotal"]) > 0.01:
        errors.append(f"Line items sum to {line_sum:.2f}, but subtotal is {parsed['subtotal']:.2f}.")
    if parsed["subtotal"] is not None and parsed["tax"] is not None and parsed["total"] is not None:
        expected = round(parsed["subtotal"] + parsed["tax"], 2)
        if abs(expected - parsed["total"]) > 0.01:
            errors.append(f"Subtotal plus tax is {expected:.2f}, but total is {parsed['total']:.2f}.")
    duplicate = any(
        record["parsed"].get("invoice_number", "").casefold() == (parsed.get("invoice_number") or "").casefold()
        and record["parsed"].get("supplier", "").casefold() == (parsed.get("supplier") or "").casefold()
        for record in processed
        if parsed.get("invoice_number") and parsed.get("supplier")
    )
    if duplicate:
        errors.append("Potential duplicate: supplier and invoice number match a prior record.")
    status = "needs_review" if errors else "ready_for_approval"
    record = {"id": str(uuid.uuid4()), "received_at": datetime.now(timezone.utc).isoformat(),
              "parsed": parsed, "checks": {"line_items_sum": line_sum, "duplicate": duplicate, "errors": errors},
              "status": status, "payment_executed": False}
    processed.append(record)
    return record

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")

@app.get("/health")
def health():
    return {"status": "ok", "processed_count": len(processed), "review_count": sum(x["status"] == "needs_review" for x in processed)}

@app.get("/invoices")
def invoices():
    return {"items": [{"id": x["id"], "invoice_number": x["parsed"]["invoice_number"], "supplier": x["parsed"]["supplier"],
                       "status": x["status"], "duplicate": x["checks"]["duplicate"]} for x in processed]}

@app.get("/review-queue")
def review_queue():
    return {"items": [x for x in processed if x["status"] == "needs_review"]}

@app.post("/invoices/process")
def invoice_process(request: InvoiceInput):
    return process_invoice(request.text)

@app.post("/invoices/reset-demo")
def reset_demo():
    # Demo-only helper; never expose this unauthenticated endpoint in production.
    processed.clear()
    return {"status": "cleared"}
