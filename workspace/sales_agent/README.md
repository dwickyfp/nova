# Sales Agent demo

This fixture provisions a realistic, deterministic Indonesian omnichannel sales
warehouse for Nova Agent Studio. It is synthetic: names are generated from a
fixed vocabulary, email addresses use the reserved `.test` domain, and no real
customer data or credentials are included.

## What it creates

- Database/schema: `NOVA_SALES` (a StarRocks database is Nova's schema boundary)
- Fact tables: `fact_sales` (2,400,000 rows), `fact_returns`, and
  `fact_sales_targets`
- Dimensions: locations, stores, sales representatives, customers, products,
  and campaigns
- Views: customer 360 and monthly sales-representative performance
- Semantic model: `nova_sales_360` (Ossie 0.1.1)
- Agent Studio agent: `Sales Agent`, owned by `root`

The data covers 2023-01-01 through 2026-09-21 and deliberately includes channel
shift toward mobile, regional mix, seasonal promotions, customer tiers,
cancellations, returns, margins, and monthly sales targets.

## Rebuild

From the repository root, with the local Nova stack running:

```bash
docker exec -i nova-starrocks-fe mysql -h 127.0.0.1 -P 9030 -u root \
  < workspace/sales_agent/01_create_sales_warehouse.sql

cd backend
STARROCKS_FE_MYSQL_PORT=29030 uv run python \
  ../workspace/sales_agent/seed_sales_agent.py --owner nova_admin
```

The SQL script intentionally refuses to run over an existing populated fixture.
The Python metadata seed defaults to `nova_admin` and is idempotent by owner and
object name. Rebuilding the
warehouse itself requires explicitly reviewing and dropping `NOVA_SALES` first.

## Good exploration prompts

- Compare recognized revenue, gross margin, and order count by channel for 2025.
- Which regions lost revenue versus the previous month, and was the change driven
  by fewer orders or lower average order value?
- Show the top 10 sales representatives by quota attainment in the latest
  completed month, including actual revenue and target.
- Which customer tiers have the highest return rate and lowest gross margin?
- Break down revenue by product category and campaign during Ramadan periods.
- Find high-revenue customers whose last purchase was more than 90 days ago.
- Which stores have high sales but unusually high return rates?
- Chart monthly mobile-app revenue and compare it with physical-store revenue.
