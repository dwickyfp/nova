import { api } from "@/lib/api-client";
import type { WorkspaceEntry, WorkspaceFileResponse } from "./types";

export const starterTemplates = [
  {
    id: "query-stage",
    title: "Preview orders from a stage",
    category: "Stages",
    topic: "Choose a stage",
    sql: `SELECT *
FROM @stage_name.orders.csv
LIMIT 25;`,
  },
  {
    id: "demo-sales",
    title: "Analyze demo sales by city",
    category: "Analytics",
    topic: "Sample data",
    sql: `SELECT c.city,
       COUNT(*) AS order_count,
       SUM(o.total_amount) AS revenue_idr
FROM NOVA_DEMO.orders AS o
JOIN NOVA_DEMO.customers AS c
  ON c.customer_id = o.customer_id
WHERE o.status = 'completed'
GROUP BY c.city
ORDER BY revenue_idr DESC;`,
  },
  {
    id: "profile-sales",
    title: "Profile a sales query",
    category: "Monitoring",
    topic: "EXPLAIN ANALYZE",
    sql: `EXPLAIN ANALYZE
SELECT p.category,
       SUM(i.subtotal) AS revenue_idr
FROM NOVA_DEMO.order_items AS i
JOIN NOVA_CATALOG.products AS p
  ON p.product_id = i.product_id
GROUP BY p.category
ORDER BY revenue_idr DESC;`,
  },
  {
    id: "demo-order-anomalies",
    title: "Detect unusual demo orders",
    category: "Machine learning",
    topic: "Anomaly detection",
    sql: `CREATE ML_MODEL nova_demo_order_anomalies
TYPE = ANOMALY_DETECTION
FEATURES = (total_amount, payment_method)
MODE = INTERACTIVE
AS SELECT order_id, total_amount, payment_method
FROM NOVA_DEMO.orders;`,
  },
  {
    id: "classify-products",
    title: "Classify catalog products",
    category: "AI functions",
    topic: "Configure a model",
    sql: `SELECT product_id,
       product_name,
       AI_CLASSIFY(product_name, 'Electronics,Audio,Accessories,Storage') AS ai_category
FROM NOVA_CATALOG.products
LIMIT 10;`,
  },
  {
    id: "daily-sales-task",
    title: "Schedule a demo sales rollup",
    category: "Tasks",
    topic: "Automation",
    sql: `CREATE TABLE IF NOT EXISTS NOVA_ANALYTICS.demo_daily_sales (
  order_day DATE NOT NULL,
  revenue_idr DECIMAL(16,2) NOT NULL
) PRIMARY KEY (order_day)
DISTRIBUTED BY HASH(order_day) BUCKETS 2
PROPERTIES ("replication_num" = "1");

CREATE TASK NOVA_ANALYTICS.default.demo_daily_sales_refresh
SCHEDULE = 'USING CRON 0 2 * * * Asia/Jakarta'
AS INSERT INTO NOVA_ANALYTICS.demo_daily_sales
SELECT DATE(order_date) AS order_day,
       SUM(total_amount) AS revenue_idr
FROM NOVA_DEMO.orders
WHERE status = 'completed'
GROUP BY DATE(order_date);`,
  },
] as const;

export function getStarterTemplate(id: string) {
  return starterTemplates.find((template) => template.id === id);
}

export async function createStarterTemplateFile(
  template: (typeof starterTemplates)[number],
  entries: WorkspaceEntry[],
): Promise<WorkspaceFileResponse> {
  const existing = new Set(
    entries
      .filter((entry) => entry.parent_path === "")
      .map((entry) => entry.name.toLowerCase()),
  );
  let name = `${template.title}.sql`;
  let suffix = 2;
  while (existing.has(name.toLowerCase())) {
    name = `${template.title}-${suffix++}.sql`;
  }
  return api.post<WorkspaceFileResponse>("/workspaces/files", {
    name,
    parent_path: "",
    content: template.sql,
  });
}
