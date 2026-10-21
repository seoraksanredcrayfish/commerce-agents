# supabase-ledger

A `MerchantBackend` over one tenant of a Supabase operations ledger that a Cafe24 sync and
a Zigzag sync feed, with the store's operations workbench as its only write path. Every
query is bound to the tenant id the deployment supplies, so one ledger can serve several
stores.

| Module | Contents |
|---|---|
| `tenant.py` | `TenantConfig` and `tenant_from_env` |
| `rows.py` | `ProductRow`, `DailyOrders`: the ledger records read |
| `reader.py` | `LedgerReader`, the read port; `InMemoryLedgerReader` |
| `postgres.py` | `PostgresLedgerReader` with its SQL; `readonly_fetch` on asyncpg |
| `workbench.py` | `WorkbenchCli`, the write port: the workbench CLI's `stock.set` |
| `catalog.py` | Rows to listings: a family per Cafe24 product number, a variant per barcode |
| `periods.py` | `last_7_days`, `last_30_days`, `last_90_days`, `start/end` to date windows |
| `merchant.py` | `LedgerMerchantBackend` |

## What it reads

| Ledger | Backend |
|---|---|
| `public.products` | listings, variants, low-stock alerts, pricing context (cost, margin) |
| `ops.channel_order_mirror` | daily orders and sales per channel: snapshot and `query_metrics` |

Reads are bounded: a search reads up to a limit of whole families, a listing reads its own
family, and low-stock alerts read a capped page with a separate count. A family's id is
`c24:<cafe24_product_no>`, the workbench product key; a variant's, or an unlinked row's,
is its barcode. The comma-separated option string maps onto `LEDGER_OPTION_LABELS` in
order. A zero selling price is marked `price_status: unset`, and margin is then None.

Reported as unknown, never as zero: traffic and conversion, campaign performance, and
sales on a channel whose mirror has no order amounts (the snapshot's `note` names it).
Order issues are not derived yet.

## What it writes

A restock of one variant or plain listing per change, and nothing else. The backend never
writes a table and holds no stock of its own:

1. `stage_inventory_action` records the proposal with the stock read then, for the preview.
2. The host approves it (`MerchantAgentConfig.require_host_approval`, on by default); the
   assistant does not approve its own changes.
3. `apply_change` reads the stock again and runs the workbench's
   `stock.set --barcode <barcode> --count <stock + quantity> --yes`, the same function as
   the workbench screen. A refusal, such as stock that moved under the fix, raises
   `ChangeNotApplicable` and the change stays staged.

Pauses, prices, content, promotions, and campaigns have no workbench command yet and raise
`ChangeNotApplicable`. Without a `WorkbenchCli` the backend is read-only.

## Run

```bash
pip install -e "./integrations/supabase-ledger[postgres]"
export LEDGER_TENANT_ID=<tenant id>             # required
export LEDGER_OPTION_LABELS=color,size          # optional
export LEDGER_LOW_STOCK_THRESHOLD=0             # optional; 0 turns low-stock alerts off
export LEDGER_ZERO_STOCK_SELLABLE=true          # optional; zero stock stays on sale
export LEDGER_DSN=postgresql://<read-only role>@<host>/postgres
export WORKBENCH_ROOT=<workbench checkout>      # optional; enables restocks
export WORKBENCH_PACKAGE=<its app's pnpm package>
```

```python
from ledger_backend.merchant import LedgerMerchantBackend
from ledger_backend.postgres import PostgresLedgerReader, readonly_fetch
from ledger_backend.tenant import tenant_from_env
from ledger_backend.workbench import WorkbenchCli

tenant = tenant_from_env()
backend = LedgerMerchantBackend(
    PostgresLedgerReader(readonly_fetch(os.environ["LEDGER_DSN"]), timezone="Asia/Seoul"),
    tenant,
    MerchantAgentConfig(enable_listing_edits=False, enable_pricing=False, enable_campaigns=False),
    workbench=WorkbenchCli(
        os.environ["WORKBENCH_ROOT"], os.environ["WORKBENCH_PACKAGE"], tenant.tenant_id
    ),
)
```

`readonly_fetch` runs each statement in a read-only transaction; give it a role that can
select only the two tables. The workbench CLI reads its own database credentials from its
checkout's environment and the tenant from `SUPABASE_TENANT_ID`, which `WorkbenchCli` sets.

## Deploy the console on a host

`deploy/install.sh` installs the merchant console under one directory (default
`/opt/commerce-agent`) and changes nothing outside it: no user, service, cron, or system
package. It needs git and Python 3.11+ and stops without them.

```bash
curl -fsSL https://raw.githubusercontent.com/<owner>/commerce-agents/<ref>/integrations/supabase-ledger/deploy/install.sh \
  | sudo bash -s -- <ref>
```

It writes `src/` (this repository at the ref), `venv/`, `.env` from
`deploy/commerce-agent.env.example` (mode 600, kept on reinstall), and `console.sh`, which
loads `.env` and runs the Agent SDK console with
`--backend ledger_backend.deployment:build_merchant`. `ledger_backend.deployment` documents
each variable. With `WORKBENCH_ROOT` empty the console is read-only.

## Tests

`pytest integrations/supabase-ledger/tests` runs on the in-memory reader with ACME rows and
a fake workbench.
