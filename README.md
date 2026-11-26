# Čebelca MCP server

[![tests](https://github.com/egorperp/cebelca-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/egorperp/cebelca-mcp/actions/workflows/ci.yml)

An [MCP](https://modelcontextprotocol.io) server for **Čebelca BIZ**, the Slovenian invoicing service. It lets AI assistants (Claude, ChatGPT, Copilot, Cursor, n8n AI agents...) look up customers, prepare invoices, record payments and download PDFs, with a person approving every write.

**Live playground:** https://yegor-ai.workers.dev/cebelca/ (demo data)

## What the assistant can do

| Tool | What it does | Writes? |
|---|---|---|
| `check_connection` | Checks the API token and shows the mode | no |
| `search_partners` | Finds customers by name or VAT ID | no |
| `list_invoices` | Lists invoices with status (draft, issued, overdue, paid) and open amount | no |
| `calculate_totals` | Net, VAT per rate and gross for invoice lines | no |
| `find_or_create_partner` | Finds or creates a customer (`partner/assure`) | yes |
| `create_invoice_draft` | Invoice header + lines (`invoice-sent`, `invoice-sent-b`) | yes, after preview |
| `add_payment` | Records a payment (`invoice-sent-p`) | yes, after preview |
| `finalize_invoice` | Issues a non-fiscal invoice (`finalize-invoice-2015`) | yes, after preview |
| `download_invoice_pdf` | Saves the PDF Čebelca generates | local file |

Plus a prompt, `monthly_overview`: totals for the month, overdue invoices and a drafted payment reminder.

Example requests:
- "Which invoices are overdue and how much is open?"
- "Invoice Kavarna Lipa for 4 hours of consulting at 50 EUR, due in 15 days."
- "Kavarna Lipa paid 146.40 today, record it."

## Safety by design

- **Preview first.** Every write tool returns a preview (with VAT totals) unless `confirm=true`. The server instructions tell the model to show the preview and wait for the user's approval.
- **Validation.** Slovenian VAT rates only (22, 9.5, 5, 0), no negative prices, discount 0 to 100 %.
- **Modes.** `demo` (default, fictional data, no network), `test` (test.cebelca.biz), `live`.
- **Read-only switch.** `CEBELCA_READ_ONLY=true` removes all write tools.
- **No secrets in prompts.** The API token stays in the server's environment; the model never sees it.
- **Tool annotations** mark read-only and destructive tools so clients can ask for confirmation.

Fiscal (cash) invoices with FURS are intentionally out of scope.

## Setup

```bash
pip install git+https://github.com/egorperp/cebelca-mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "cebelca": {
      "command": "cebelca-mcp",
      "env": { "CEBELCA_MODE": "demo" }
    }
  }
}
```

For your own account: in Čebelca go to *Nastavitve > Dostop > API*, copy the key, and set `CEBELCA_MODE=test` (or `live`) and `CEBELCA_API_TOKEN=...`.

Remote (streamable HTTP, e.g. on Render or Fly.io with the included Dockerfile):

```bash
cebelca-mcp --http --host 0.0.0.0 --port 8000
```

Put it behind authentication before exposing a live token.

## How it works

```
AI assistant ──MCP──> server.py (tools, previews, validation)
                         │
                         └─> client.py ── demo journal (JSON)
                                       └─ Čebelca API: POST /API?_r=<resource>&_m=<method>
                                          basic auth <token>:x, dates as dd.mm.yyyy
```

The Čebelca API has no general "list" methods, so the server keeps a local journal of the partners and invoices it creates. `list_invoices` answers from that journal.

## Development

```bash
pip install -e ".[dev]"
pytest -q
npx @modelcontextprotocol/inspector cebelca-mcp   # try the tools in a browser
```

## Credits

API calls follow Čebelca's public examples ([InvoiceFox-API-bash](https://github.com/InvoiceFox/InvoiceFox-API-bash)). Not affiliated with Čebelca BIZ.

MIT License
