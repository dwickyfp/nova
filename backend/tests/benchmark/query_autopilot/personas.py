from dataclasses import dataclass


@dataclass(frozen=True)
class Workload:
    persona: str
    role: str
    queries: tuple[str, ...]


def workloads(parameter: int) -> tuple[Workload, ...]:
    category, store, customer = 1 + parameter % 20, 1 + parameter % 5, 1 + parameter % 100
    return (
        Workload(
            "executive",
            "autopilot_executive",
            (
                (
                    "SELECT date_trunc('month',ordered_at) AS month,COUNT(*) "
                    "AS orders,SUM(total) AS revenue FROM orders WHERE status='completed' "
                    "GROUP BY 1 ORDER BY 1"
                ),
                (
                    f"SELECT store_id,SUM(total) FROM orders WHERE store_id={store} "
                    f"AND status='completed' GROUP BY store_id"
                ),
            ),
        ),
        Workload(
            "finance",
            "autopilot_finance",
            (
                (
                    "SELECT method,status,COUNT(*),SUM(amount) FROM payments "
                    "GROUP BY method,status ORDER BY method,status"
                ),
                (
                    f"SELECT o.order_id,o.total,p.amount FROM orders o INNER "
                    f"JOIN payments p ON o.order_id=p.order_id WHERE o.customer_id={customer} "
                    f"ORDER BY o.order_id"
                ),
            ),
        ),
        Workload(
            "marketing",
            "autopilot_marketing",
            (
                (
                    "SELECT c.channel,COUNT(*),SUM(o.total*a.weight) FROM "
                    "campaigns c INNER JOIN campaign_attribution a ON c.campaign_id=a.campaign_id "
                    "INNER JOIN orders o ON o.order_id=a.order_id GROUP BY "
                    "c.channel ORDER BY c.channel"
                ),
                (
                    f"SELECT p.category_id,SUM(i.quantity),SUM(i.quantity*i.unit_price) "
                    f"FROM products p INNER JOIN order_items i ON p.product_id=i.product_id "
                    f"WHERE p.category_id={category} GROUP BY p.category_id"
                ),
            ),
        ),
        Workload(
            "operations",
            "autopilot_operations",
            (
                (
                    f"SELECT status,COUNT(*) FROM orders WHERE store_id={store} "
                    f"GROUP BY status ORDER BY status"
                ),
                (
                    f"SELECT order_id,total FROM orders WHERE customer_id={customer} "
                    f"AND status='pending' ORDER BY order_id"
                ),
            ),
        ),
    )
