---
name: monthly-revenue-review
description: Monthly revenue review. Use when someone asks for a monthly revenue review, month-over-month (MoM) revenue growth, or revenue against store targets for NOVA_DEMO.
---

# Monthly revenue review

Use this playbook when the user asks for a monthly revenue review for a month
(for example "July 2025").

## Inputs

- The review month, as YYYY-MM. If the user names only a month, ask for the year.
- The previous month is the comparison month.

## Steps

1. Get revenue and order count for the review month and the previous month
   from the NOVA_DEMO orders data. Query the governed sales data (the
   `demo_sales` semantic view, revenue by order month) when it is available.
   Revenue counts every order status except `cancelled`. This SQL pattern is
   the definition; use it as written when explicit SQL is available:

   ```sql
   SELECT DATE_FORMAT(order_date, '%Y-%m') AS month,
          SUM(total_amount) AS revenue,
          COUNT(*) AS orders
   FROM NOVA_DEMO.orders
   WHERE DATE_FORMAT(order_date, '%Y-%m') IN ('<previous YYYY-MM>', '<review YYYY-MM>')
     AND status <> 'cancelled'
   GROUP BY 1
   ORDER BY 1
   ```

2. Compute MoM growth as
   `(revenue_review - revenue_previous) / revenue_previous * 100`, rounded to
   one decimal place.
3. Call the `get_store_targets` tool from the connected target service with
   `month` set to the review month. Use its `total_target`.
4. Compute attainment as `revenue_review / total_target * 100`, rounded to one
   decimal place.

## Output

Present a short KPI table with these rows, in IDR:

| KPI | Value |
| --- | --- |
| Revenue (review month) | ... |
| Revenue (previous month) | ... |
| MoM growth | ...% |
| Target (review month) | ... |
| Attainment | ...% |

Then add two sentences: whether the month met its target, and the main driver
you can see in the data. Say where each number came from (the orders query or
the target service). Do not invent targets when the target service fails; say
it was unavailable.
