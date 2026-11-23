import { describe, expect, it } from "vitest";
import { columnLabel, formatResultValue } from "./result-format";

describe("formatResultValue", () => {
  it("reads engine decimals as amounts", () => {
    expect(formatResultValue("24999000.00000000", "revenue")).toBe("24,999,000");
    expect(formatResultValue("10165333.33333333", "average_order_value")).toBe("10,165,333.33");
    expect(formatResultValue(145886000, "revenue")).toBe("145,886,000");
    expect(formatResultValue("0.12345", "share")).toBe("0.1235");
    expect(formatResultValue("-1200.5", "change")).toBe("-1,200.5");
  });

  it("keeps labels, codes and large integers exactly as returned", () => {
    expect(formatResultValue(2025, "order_year")).toBe("2025");
    expect(formatResultValue("123456", "customer_id")).toBe("123456");
    expect(formatResultValue("00123", "branch")).toBe("00123");
    expect(formatResultValue("12345678901234567", "amount")).toBe("12345678901234567");
    expect(formatResultValue("Jakarta", "shipping_city")).toBe("Jakarta");
    expect(formatResultValue(true, "active")).toBe("true");
  });

  it("shows wall-clock dates as written, without a timezone shift", () => {
    expect(formatResultValue("2025-06-01T00:00:00", "order_date")).toBe("Jun 1, 2025");
    expect(formatResultValue("2025-06-01", "order_date")).toBe("Jun 1, 2025");
    expect(formatResultValue("2025-06-01 14:30:00", "created_at")).toBe("Jun 1, 2025, 14:30");
    expect(formatResultValue("2025-02-30", "order_date")).toBe("2025-02-30");
  });
});

describe("columnLabel", () => {
  it("turns snake_case names into words and keeps other names", () => {
    expect(columnLabel("shipping_city")).toBe("Shipping city");
    expect(columnLabel("revenue")).toBe("Revenue");
    expect(columnLabel("customer_id")).toBe("Customer ID");
    expect(columnLabel("Revenue (IDR)")).toBe("Revenue (IDR)");
    expect(columnLabel("orderDate")).toBe("orderDate");
  });
});
