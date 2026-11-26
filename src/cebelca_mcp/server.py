"""MCP server that lets AI assistants (Claude, ChatGPT, Copilot...) work with Čebelca BIZ.

Run:  cebelca-mcp                      (stdio, for Claude Desktop / Claude Code)
      cebelca-mcp --http --port 8000   (streamable HTTP, for remote clients)

Safety model
- Every tool that writes to Čebelca first returns a preview. The model must show it
  to the user and call again with confirm=true.
- CEBELCA_READ_ONLY=true removes all write tools.
- Default mode is "demo" (fictional data); "test" uses test.cebelca.biz.
"""
from __future__ import annotations

import argparse
import functools
import os
from typing import Annotated

from pydantic import BaseModel, Field

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .client import Cebelca, CebelcaError, invoice_totals

READ_ONLY = os.getenv("CEBELCA_READ_ONLY", "false").lower() == "true"

server = MCPServer(
    name="cebelca",
    title="Čebelca BIZ",
    instructions=(
        "Tools for the Slovenian invoicing service Čebelca BIZ. Amounts are in EUR, VAT rates are "
        "Slovenian (22, 9.5, 5 or 0). Before any tool call with confirm=true, show the user the preview "
        "returned by the same tool without confirm and wait for their approval. Never invent partner or "
        "invoice IDs: look them up first."
    ),
)
_api: Cebelca | None = None


def api() -> Cebelca:
    global _api
    if _api is None:
        _api = Cebelca()
    return _api


class InvoiceLine(BaseModel):
    title: str = Field(description="What is being invoiced, e.g. 'Spletna stran, izdelava'")
    qty: float = Field(1, description="Quantity")
    unit: str = Field("kos", description="Unit of measure, e.g. kos, ura, mesec")
    price: float = Field(description="Unit price without VAT, EUR")
    vat: float = Field(22, description="VAT rate in percent: 22, 9.5, 5 or 0")
    discount: float = Field(0, description="Discount in percent")


READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)


def _safe(fn):
    """Turn API errors into readable tool results instead of protocol errors."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except CebelcaError as e:
            return {"ok": False, "errors": [str(e)]}
    return wrapper


@server.tool(annotations=READ)
@_safe
def check_connection() -> dict:
    """Check that the Čebelca API token works and show the current mode (demo, test or live)."""
    return api().check_connection()


@server.tool(annotations=READ)
@_safe
def search_partners(query: Annotated[str, Field(description="Part of the name or VAT ID; empty for all")] = "") -> list[dict]:
    """Search customers (partners) known to this server."""
    return api().list_partners(query)


@server.tool(annotations=READ)
@_safe
def list_invoices(
    status: Annotated[str, Field(description="all, draft, issued, overdue or paid")] = "all",
    partner_id: Annotated[int | None, Field(description="Only invoices for this partner")] = None,
) -> list[dict]:
    """List invoices with status, total and open amount. Use status='overdue' for unpaid invoices past due date."""
    return api().list_invoices(status, partner_id)


@server.tool(annotations=READ)
def calculate_totals(items: list[InvoiceLine]) -> dict:
    """Calculate net, VAT (per rate) and gross totals for invoice lines without creating anything."""
    return invoice_totals([i.model_dump() for i in items])


if not READ_ONLY:
    @server.tool(annotations=WRITE)
    @_safe
    def find_or_create_partner(
        name: str,
        street: str = "",
        postal: str = "",
        city: str = "",
        vat_id: Annotated[str, Field(description="Slovenian VAT ID, e.g. SI12345678")] = "",
    ) -> dict:
        """Find a customer by VAT ID or name, or create it if it does not exist. Returns the partner id."""
        return api().find_or_create_partner(name, street, postal, city, vat_id)

    @server.tool(annotations=WRITE)
    @_safe
    def create_invoice_draft(
        partner_id: int,
        items: list[InvoiceLine],
        date_sent: Annotated[str | None, Field(description="Issue date YYYY-MM-DD, default today")] = None,
        due_days: Annotated[int, Field(description="Days until payment is due")] = 15,
        confirm: Annotated[bool, Field(description="false = preview only; true = create in Čebelca")] = False,
    ) -> dict:
        """Prepare an invoice draft. Without confirm it only returns a preview with totals."""
        return api().create_invoice_draft(partner_id, [i.model_dump() for i in items], date_sent, due_days, confirm)

    @server.tool(annotations=WRITE)
    @_safe
    def add_payment(invoice_id: int, amount: float, date_of: str | None = None, confirm: bool = False) -> dict:
        """Record a (partial) payment on an invoice. Without confirm it only returns a preview."""
        return api().add_payment(invoice_id, amount, date_of, confirm=confirm)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    @_safe
    def finalize_invoice(
        invoice_id: int,
        number: Annotated[str, Field(description="Invoice number, e.g. 26-0044")],
        confirm: bool = False,
    ) -> dict:
        """Issue a draft invoice (non-fiscal, e.g. paid by bank transfer). This cannot be undone."""
        return api().finalize_invoice(invoice_id, number, confirm)

    @server.tool(annotations=WRITE)
    @_safe
    def download_invoice_pdf(invoice_id: int, folder: str = "~/Downloads", lang: str = "si") -> dict:
        """Save the invoice PDF generated by Čebelca to a local folder (test or live mode)."""
        return api().download_pdf(invoice_id, folder, lang)


@server.prompt()
def monthly_overview() -> str:
    """Summarise this month's invoicing and list overdue invoices."""
    return ("Use list_invoices to summarise invoicing for the current month: total invoiced, total paid, "
            "open amount. Then list overdue invoices with partner, days overdue and amount, and draft a short, "
            "polite Slovenian payment reminder for the largest one.")


def main() -> None:
    p = argparse.ArgumentParser(description="Čebelca BIZ MCP server")
    p.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
