# supabase-ledger

A `MerchantBackend` over one tenant of a Supabase operations ledger that a Cafe24 sync and
a Zigzag sync feed. It reads; it does not write. Every query is bound to the tenant id the
deployment supplies, so one ledger can serve several stores.

| Module | Contents |
|---|---|
| `tenant.py` | `TenantConfig` and `tenant_from_env` |
| `rows.py` | `ProductRow`, `DailyOrders`: the ledger records read |
| `reader.py` | `LedgerReader`, the read port; `InMemoryLedgerReader` |
| `postgres.py` | `PostgresLedgerReader` with its SQL; `readonly_fetch` on asyncpg |
| `catalog.py` | Rows to listings: a family per Cafe24 product number, a variant per barcode |
| `periods.py` | `last_7_days`, `last_30_days`, `last_90_days`, `start/end` to date windows |
| `merchant.py` | `LedgerMerchantBackend` |

## What it reads

| Ledger | Backend |
|---|---|
| `public.products` | listings, variants, low-stock alerts, pricing context (cost, margin) |
| `ops.channel_order_mirror` | daily orders and sales per channel: snapshot and `query_metrics` |

A family's id is `p<cafe24_product_no>`; a variant's, or an unlinked row's, is its barcode.
The comma-separated option string maps onto `LEDGER_OPTION_LABELS` in order. A zero
selling price is marked `price_status: unset`, and margin is then None.

Reported as unknown, never as zero: traffic and conversion, campaign performance, and
sales on a channel whose mirror has no order amounts (the snapshot's `note` names it).
Order issues are not derived yet. Every `stage_*` call and `apply_change` raise
`ChangeNotApplicable`; turn the merchant config's write switches off to keep those tools
out of the prompt.

## Run

```bash
pip install -e "./integrations/supabase-ledger[postgres]"
export LEDGER_TENANT_ID=<tenant id>             # required
export LEDGER_OPTION_LABELS=color,size          # optional
export LEDGER_LOW_STOCK_THRESHOLD=0             # optional; 0 turns low-stock alerts off
export LEDGER_ZERO_STOCK_SELLABLE=true          # optional; zero stock stays on sale
export LEDGER_DSN=postgresql://<read-only role>@<host>/postgres
```

```python
from ledger_backend.merchant import LedgerMerchantBackend
from ledger_backend.postgres import PostgresLedgerReader, readonly_fetch
from ledger_backend.tenant import tenant_from_env

backend = LedgerMerchantBackend(
    PostgresLedgerReader(readonly_fetch(os.environ["LEDGER_DSN"]), timezone="Asia/Seoul"),
    tenant_from_env(),
    MerchantAgentConfig(enable_listing_edits=False, enable_pricing=False, enable_campaigns=False),
)
```

`readonly_fetch` runs each statement in a read-only transaction; give it a role that can
select only the two tables.

## Tests

`pytest integrations/supabase-ledger/tests` runs on the in-memory reader with ACME rows.
