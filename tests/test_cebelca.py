import asyncio

import pytest

from cebelca_mcp.client import Cebelca, LiveBackend, invoice_totals, validate_items


@pytest.fixture
def demo(tmp_path):
    return Cebelca(mode="demo", journal_path=str(tmp_path / "j.json"))


def test_totals_split_by_vat_rate():
    t = invoice_totals([{"title": "a", "qty": 2, "price": 100, "vat": 22},
                        {"title": "b", "qty": 1, "price": 50, "vat": 9.5, "discount": 10}])
    assert t["net"] == 245.0
    assert t["vat_by_rate"] == {"22%": 44.0, "9.5%": 4.28}
    assert t["gross"] == 293.28


def test_validation_catches_bad_lines():
    errs = validate_items([{"title": "", "price": -1, "vat": 20}])
    assert len(errs) == 3


def test_draft_needs_confirmation(demo):
    before = len(demo.journal.data["invoices"])
    r = demo.create_invoice_draft(1, [{"title": "Svetovanje", "qty": 3, "price": 60, "vat": 22}])
    assert "preview" in r and len(demo.journal.data["invoices"]) == before
    r = demo.create_invoice_draft(1, [{"title": "Svetovanje", "qty": 3, "price": 60, "vat": 22}], confirm=True)
    assert r["status"] == "draft" and r["totals"]["gross"] == 219.6


def test_finalize_and_pay_flow(demo):
    inv = demo.create_invoice_draft(2, [{"title": "Logo", "price": 100, "vat": 22}], confirm=True)["invoice_id"]
    assert demo.finalize_invoice(inv, "26-0099", confirm=True)["status"] == "issued"
    assert demo.finalize_invoice(inv, "26-0099", confirm=True)["ok"] is False  # cannot issue twice
    demo.add_payment(inv, 122, confirm=True)
    row = next(i for i in demo.list_invoices() if i["id"] == inv)
    assert row["status"] == "paid" and row["open_amount"] == 0


def test_overdue_detection(demo):
    assert [i["title"] for i in demo.list_invoices("overdue")] == ["26-0041"]


def test_partner_dedup_by_vat(demo):
    r = demo.find_or_create_partner("Kavarna LIPA", vat_id="SI12345678")
    assert r["created"] is False and r["id"] == 1


def test_first_id_parses_nested_responses():
    assert LiveBackend.first_id(["ok", [{"id": 42}]]) == 42
    assert LiveBackend.first_id([[{"id": 7}]]) == 7


def test_mcp_server_lists_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("CEBELCA_JOURNAL", str(tmp_path / "s.json"))
    from cebelca_mcp import server
    tools = asyncio.run(server.server.list_tools())
    names = {t.name for t in tools}
    assert {"list_invoices", "create_invoice_draft", "finalize_invoice", "calculate_totals"} <= names
