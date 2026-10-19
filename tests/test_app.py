from fastapi.testclient import TestClient
from app.main import app, processed, process_invoice

client = TestClient(app)
VALID = """Invoice Number: INV-1001
Supplier: Northwind Office Supplies
Invoice Date: 2026-01-10
Due Date: 2026-02-09
Item: Printer paper | 2 | 15.00
Item: Shipping | 1 | 5.00
Subtotal: 35.00
Tax: 2.80
Total: 37.80"""

def setup_function():
    processed.clear()

def test_parse_and_validate_invoice():
    record = process_invoice(VALID)
    assert record["parsed"]["invoice_number"] == "INV-1001"
    assert record["status"] == "ready_for_approval"
    assert record["checks"]["errors"] == []
    assert record["payment_executed"] is False

def test_duplicate_supplier_and_invoice_is_flagged():
    process_invoice(VALID)
    second = process_invoice(VALID)
    assert second["checks"]["duplicate"] is True
    assert second["status"] == "needs_review"

def test_mismatched_total_goes_to_review():
    record = process_invoice(VALID.replace("Total: 37.80", "Total: 999.00"))
    assert record["status"] == "needs_review"
    assert any("Subtotal plus tax" in error for error in record["checks"]["errors"])

def test_endpoint_rejects_short_text():
    assert client.post("/invoices/process", json={"text": "short"}).status_code == 422

def test_review_queue_contains_exceptions():
    client.post("/invoices/process", json={"text": VALID.replace("Total: 37.80", "Total: 999.00")})
    assert len(client.get("/review-queue").json()["items"]) == 1
