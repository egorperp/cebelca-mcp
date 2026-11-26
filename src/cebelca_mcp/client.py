"""Čebelca BIZ API client with a demo backend.

The Čebelca API is form-encoded POST to
    https://www.cebelca.biz/API?_r=<resource>&_m=<method>
with HTTP basic auth (API token as user name, "x" as password).
Test server: https://test.cebelca.biz

The public API is write-oriented: it has no general "list" methods. To still let
an assistant answer "what did we invoice this month?", the client keeps a local
journal (JSON file) of every record it creates.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

BASE_URLS = {"test": "https://test.cebelca.biz", "live": "https://www.cebelca.biz"}


class CebelcaError(RuntimeError):
    pass


# ---------- pure helpers ----------

def line_totals(item: dict) -> dict:
    qty = float(item.get("qty", 1))
    price = float(item["price"])
    discount = float(item.get("discount", 0))
    vat = float(item.get("vat", 22))
    net = round(qty * price * (1 - discount / 100), 2)
    tax = round(net * vat / 100, 2)
    return {"net": net, "vat_amount": tax, "gross": round(net + tax, 2)}


def invoice_totals(items: list[dict]) -> dict:
    lines = [line_totals(i) for i in items]
    by_rate: dict[str, float] = {}
    for item, t in zip(items, lines):
        rate = f"{float(item.get('vat', 22)):g}%"
        by_rate[rate] = round(by_rate.get(rate, 0) + t["vat_amount"], 2)
    net = round(sum(t["net"] for t in lines), 2)
    vat = round(sum(t["vat_amount"] for t in lines), 2)
    return {"net": net, "vat": vat, "gross": round(net + vat, 2), "vat_by_rate": by_rate}


def validate_items(items: list[dict]) -> list[str]:
    errors = []
    if not items:
        errors.append("At least one invoice line is needed.")
    for n, i in enumerate(items, 1):
        if not str(i.get("title", "")).strip():
            errors.append(f"Line {n}: title is missing.")
        try:
            if float(i["price"]) < 0:
                errors.append(f"Line {n}: price cannot be negative.")
        except (KeyError, TypeError, ValueError):
            errors.append(f"Line {n}: price must be a number.")
        if float(i.get("vat", 22)) not in (0, 5, 9.5, 22):
            errors.append(f"Line {n}: VAT {i.get('vat')} is not a Slovenian rate (0, 5, 9.5, 22).")
        if not 0 <= float(i.get("discount", 0)) <= 100:
            errors.append(f"Line {n}: discount must be between 0 and 100.")
    return errors


# ---------- journal ----------

@dataclass
class Journal:
    path: Path
    data: dict = field(default_factory=lambda: {"partners": [], "invoices": []})

    @classmethod
    def load(cls, path: Path) -> "Journal":
        j = cls(path)
        if path.exists():
            j.data = json.loads(path.read_text(encoding="utf-8"))
        return j

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------- backends ----------

class LiveBackend:
    """Talks to the real (or test) Čebelca server."""

    def __init__(self, token: str, base_url: str, timeout: float = 30):
        if not token:
            raise CebelcaError("CEBELCA_API_TOKEN is not set.")
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(auth=(token, "x"), timeout=timeout)

    def call(self, resource: str, method: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        r = self.http.post(f"{self.base}/API", params={"_r": resource, "_m": method}, data=clean)
        if r.status_code == 401:
            raise CebelcaError("Čebelca refused the API token (401).")
        if r.status_code >= 400:
            raise CebelcaError(f"Čebelca returned {r.status_code}: {r.text[:400]}")
        try:
            return r.json()
        except ValueError:
            # some methods answer with Python-like literals, e.g. ['ok',[{'id':1}]]
            return json.loads(r.text.replace("'", '"'))

    @staticmethod
    def first_id(resp: Any) -> int:
        """Find the first {'id': n} in a nested response."""
        stack = [resp]
        while stack:
            x = stack.pop(0)
            if isinstance(x, dict) and "id" in x:
                return int(x["id"])
            if isinstance(x, list):
                stack.extend(x)
        raise CebelcaError(f"No id in Čebelca response: {str(resp)[:200]}")

    def pdf(self, invoice_id: int, title: str, lang: str) -> bytes:
        r = self.http.get(f"{self.base}/API-pdf", params={
            "id": invoice_id, "res": "invoice-sent", "format": "PDF", "doctitle": "Račun št.",
            "lang": lang, "disposition": "inline", "preview": 0})
        if r.status_code >= 400 or not r.content.startswith(b"%PDF"):
            raise CebelcaError(f"PDF download failed ({r.status_code}).")
        return r.content


class Cebelca:
    """High-level operations used by the MCP tools (and mirrored in the web playground)."""

    def __init__(self, mode: str | None = None, token: str | None = None, journal_path: str | None = None):
        self.mode = (mode or os.getenv("CEBELCA_MODE", "demo")).lower()
        if self.mode not in ("demo", "test", "live"):
            raise CebelcaError("CEBELCA_MODE must be demo, test or live.")
        self.journal = Journal.load(Path(journal_path or os.getenv(
            "CEBELCA_JOURNAL", Path.home() / ".cebelca-mcp" / f"journal-{self.mode}.json")))
        self.live = None
        if self.mode in ("test", "live"):
            self.live = LiveBackend(token or os.getenv("CEBELCA_API_TOKEN", ""), BASE_URLS[self.mode])
        elif not self.journal.data["partners"]:
            self._seed_demo()

    # -- demo data --
    def _seed_demo(self) -> None:
        today = date.today()
        self.journal.data = {
            "partners": [
                {"id": 1, "name": "Kavarna Lipa d.o.o.", "street": "Glavni trg 5", "postal": "2000", "city": "Maribor", "vat_id": "SI12345678"},
                {"id": 2, "name": "Studio Vrba s.p.", "street": "Partizanska 12", "postal": "1000", "city": "Ljubljana", "vat_id": ""},
                {"id": 3, "name": "Zelena Energija d.o.o.", "street": "Industrijska 3", "postal": "3000", "city": "Celje", "vat_id": "SI87654321"},
            ],
            "invoices": [
                {"id": 101, "title": "26-0041", "partner_id": 1, "date_sent": str(today - timedelta(days=40)),
                 "date_to_pay": str(today - timedelta(days=10)), "status": "issued", "paid": 0,
                 "items": [{"title": "Spletna stran, vzdrževanje", "qty": 1, "unit": "mesec", "price": 120, "vat": 22}]},
                {"id": 102, "title": "26-0042", "partner_id": 3, "date_sent": str(today - timedelta(days=12)),
                 "date_to_pay": str(today + timedelta(days=18)), "status": "issued", "paid": 0,
                 "items": [{"title": "AI chatbot, postavitev", "qty": 1, "unit": "kos", "price": 900, "vat": 22},
                           {"title": "Svetovanje", "qty": 4, "unit": "ura", "price": 50, "vat": 22}]},
                {"id": 103, "title": "26-0043", "partner_id": 2, "date_sent": str(today - timedelta(days=5)),
                 "date_to_pay": str(today + timedelta(days=10)), "status": "issued", "paid": 30.5,
                 "items": [{"title": "Fotografiranje izdelkov", "qty": 10, "unit": "kos", "price": 5, "vat": 22}]},
            ],
        }
        self.journal.save()

    def _next_id(self, kind: str, start: int) -> int:
        return max([r["id"] for r in self.journal.data[kind]] + [start]) + 1

    # -- read --
    def check_connection(self) -> dict:
        if self.live:
            self.live.call("partner", "assure")  # empty call only validates the token
            return {"ok": True, "mode": self.mode, "message": "API token accepted."}
        return {"ok": True, "mode": "demo", "message": "Demo mode: fictional data, nothing leaves this computer."}

    def list_partners(self, query: str = "") -> list[dict]:
        q = query.lower().strip()
        return [p for p in self.journal.data["partners"] if q in p["name"].lower() or q in p.get("vat_id", "").lower()]

    def list_invoices(self, status: str = "all", partner_id: int | None = None) -> list[dict]:
        today = str(date.today())
        out = []
        for inv in self.journal.data["invoices"]:
            t = invoice_totals(inv["items"])
            open_amount = round(t["gross"] - inv.get("paid", 0), 2)
            state = inv["status"]
            if state == "issued" and open_amount <= 0:
                state = "paid"
            elif state == "issued" and inv["date_to_pay"] < today:
                state = "overdue"
            row = {**{k: inv[k] for k in ("id", "title", "partner_id", "date_sent", "date_to_pay")},
                   "partner": self._partner_name(inv["partner_id"]), "status": state,
                   "gross": t["gross"], "open_amount": max(open_amount, 0)}
            if (status in ("all", state)) and (partner_id is None or inv["partner_id"] == partner_id):
                out.append(row)
        return out

    def _partner_name(self, pid: int) -> str:
        return next((p["name"] for p in self.journal.data["partners"] if p["id"] == pid), f"partner #{pid}")

    # -- write --
    def find_or_create_partner(self, name: str, street: str = "", postal: str = "", city: str = "", vat_id: str = "") -> dict:
        existing = next((p for p in self.journal.data["partners"]
                         if (vat_id and p.get("vat_id") == vat_id) or p["name"].lower() == name.lower()), None)
        if self.live:
            pid = self.live.first_id(self.live.call("partner", "assure", name=name, street=street,
                                                    postal=postal, city=city, vatid=vat_id or None))
        elif existing:
            return {**existing, "created": False}
        else:
            pid = self._next_id("partners", 0)
        rec = {"id": pid, "name": name, "street": street, "postal": postal, "city": city, "vat_id": vat_id}
        if not existing:
            self.journal.data["partners"].append(rec)
            self.journal.save()
        return {**rec, "created": existing is None}

    def create_invoice_draft(self, partner_id: int, items: list[dict], date_sent: str | None = None,
                             due_days: int = 15, confirm: bool = False) -> dict:
        errors = validate_items(items)
        if errors:
            return {"ok": False, "errors": errors}
        sent = date.fromisoformat(date_sent) if date_sent else date.today()
        header = {"partner_id": partner_id, "partner": self._partner_name(partner_id),
                  "date_sent": str(sent), "date_to_pay": str(sent + timedelta(days=due_days))}
        preview = {**header, "items": items, "totals": invoice_totals(items)}
        if not confirm:
            return {"ok": True, "preview": preview,
                    "next_step": "Show this preview to the user. Call again with confirm=true only after they approve."}

        if self.live:
            inv_id = self.live.first_id(self.live.call(
                "invoice-sent", "insert-into", date_sent=sent.strftime("%d.%m.%Y"),
                date_to_pay=(sent + timedelta(days=due_days)).strftime("%d.%m.%Y"),
                date_served=sent.strftime("%d.%m.%Y"), id_partner=partner_id))
            for i in items:
                self.live.call("invoice-sent-b", "insert-into", title=i["title"], qty=i.get("qty", 1),
                               mu=i.get("unit", "kos"), price=i["price"], vat=i.get("vat", 22),
                               discount=i.get("discount", 0), id_invoice_sent=inv_id)
        else:
            inv_id = self._next_id("invoices", 100)
        self.journal.data["invoices"].append({"id": inv_id, "title": "", "status": "draft", "paid": 0,
                                              **{k: header[k] for k in ("partner_id", "date_sent", "date_to_pay")},
                                              "items": items})
        self.journal.save()
        return {"ok": True, "invoice_id": inv_id, "status": "draft", **preview}

    def add_payment(self, invoice_id: int, amount: float, date_of: str | None = None,
                    payment_method_id: int = 1, confirm: bool = False) -> dict:
        inv = self._invoice(invoice_id)
        if amount <= 0:
            return {"ok": False, "errors": ["Amount must be positive."]}
        if not confirm:
            return {"ok": True, "preview": {"invoice": inv.get("title") or invoice_id, "amount": amount},
                    "next_step": "Confirm with the user, then call again with confirm=true."}
        d = date.fromisoformat(date_of) if date_of else date.today()
        if self.live:
            self.live.call("invoice-sent-p", "insert-into", date_of=d.strftime("%d.%m.%Y"), amount=amount,
                           id_payment_method=payment_method_id, id_invoice_sent=invoice_id)
        inv["paid"] = round(inv.get("paid", 0) + amount, 2)
        self.journal.save()
        return {"ok": True, "invoice_id": invoice_id, "paid_total": inv["paid"]}

    def finalize_invoice(self, invoice_id: int, number: str, confirm: bool = False) -> dict:
        """Issue a draft as a non-fiscal invoice (e.g. bank transfer). Fiscal cash invoices are out of scope."""
        inv = self._invoice(invoice_id)
        if inv["status"] != "draft":
            return {"ok": False, "errors": [f"Invoice {invoice_id} is already {inv['status']}."]}
        if not confirm:
            return {"ok": True, "preview": {"invoice_id": invoice_id, "number": number,
                                            "totals": invoice_totals(inv["items"])},
                    "next_step": "Issuing cannot be undone. Confirm with the user, then call with confirm=true."}
        if self.live:
            self.live.call("invoice-sent", "finalize-invoice-2015", id=invoice_id, title=number, doctype=0)
        inv.update(status="issued", title=number)
        self.journal.save()
        return {"ok": True, "invoice_id": invoice_id, "number": number, "status": "issued"}

    def download_pdf(self, invoice_id: int, folder: str, lang: str = "si") -> dict:
        inv = self._invoice(invoice_id)
        if not self.live:
            return {"ok": False, "errors": ["PDFs are generated by Čebelca, so they need test or live mode."]}
        data = self.live.pdf(invoice_id, inv.get("title", ""), lang)
        out = Path(folder).expanduser() / f"racun-{inv.get('title') or invoice_id}.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return {"ok": True, "path": str(out), "bytes": len(data)}

    def _invoice(self, invoice_id: int) -> dict:
        inv = next((i for i in self.journal.data["invoices"] if i["id"] == invoice_id), None)
        if not inv:
            raise CebelcaError(f"Invoice {invoice_id} is not in this server's journal.")
        return inv
