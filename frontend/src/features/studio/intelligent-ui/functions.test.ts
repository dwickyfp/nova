import { afterEach, describe, expect, it, vi } from "vitest";
import { createSchemaLibrary } from "./catalog";
import { uiFunctions } from "./functions";

const functions = createSchemaLibrary().functions!;
const run = (name: string, args: Record<string, unknown>) => functions[name].fn(args);
const cases: [string, Record<string, unknown>, unknown][] = [
  ["Append", { list: [1], item: 2 }, [1, 2]],
  ["Prepend", { list: [2], item: 1 }, [1, 2]],
  ["Concat", { first: [1], second: [2, 3] }, [1, 2, 3]],
  ["Remove", { list: [{ id: 1 }, { id: 2 }], item: { id: 1 } }, [{ id: 2 }]],
  ["RemoveAt", { list: [1, 2, 3], index: 1 }, [1, 3]],
  ["Update", { list: [1, 2], index: 1, item: 4 }, [1, 4]],
  ["Toggle", { list: [1, 2], item: 2 }, [1]],
  ["Includes", { list: [{ id: 1 }], item: { id: 1 } }, true],
  ["Find", { rows: [{ id: 1 }, { id: 2 }], field: "id", value: 2 }, { id: 2 }],
  ["IndexOf", { list: ["a", "b"], item: "b" }, 1],
  ["Take", { list: [1, 2, 3], count: 2 }, [1, 2]],
  ["Slice", { list: [1, 2, 3, 4], start: 1, end: 3 }, [2, 3]],
  ["Unique", { list: [{ id: 1 }, { id: 1 }, { id: 2 }] }, [{ id: 1 }, { id: 2 }]],
  ["Reverse", { list: [1, 2, 3] }, [3, 2, 1]],
  ["Range", { start: 3, end: -1, step: -2 }, [3, 1, -1]],
  ["Flatten", { list: [[1, 2], [3]] }, [1, 2, 3]],
  ["Chunk", { list: [1, 2, 3, 4, 5], size: 2 }, [[1, 2], [3, 4], [5]]],
  ["Pow", { base: 2, exponent: 3 }, 8],
  ["Sqrt", { value: 81 }, 9],
  ["Clamp", { value: 12, min: 0, max: 10 }, 10],
  ["Percent", { part: 1, total: 4 }, 25],
  ["Lerp", { start: 10, end: 30, fraction: 0.25 }, 15],
  ["Allocate", { total: 120, weights: [1, 2, 3] }, [20, 40, 60]],
  ["Payment", { principal: 1200, rate: 0, periods: 12 }, 100],
  ["Convert", { value: 10, factor: 1.8, offset: 32 }, 50],
  ["Format", { value: 1234.5, digits: 2, locale: "en-US", currency: "USD" }, "$1,234.50"],
  ["Join", { list: [1, 2, 3], separator: " / " }, "1 / 2 / 3"],
  ["Plural", { count: 1, singular: "item", plural: "items" }, "item"],
  ["Pad", { text: "7", length: 3, character: "0" }, "007"],
  ["Upper", { text: "Nova" }, "NOVA"],
  ["Lower", { text: "Nova" }, "nova"],
  ["Trim", { text: "  Nova \n" }, "Nova"],
  ["Capitalize", { text: "nova studio" }, "Nova Studio"],
  ["Split", { text: "a|b|c", separator: "|" }, ["a", "b", "c"]],
  ["Replace", { text: "a.b.a", search: "a", replacement: "x" }, "x.b.x"],
  ["Duration", { seconds: 125.9 }, "2:05"],
  ["Default", { value: "", fallback: "missing" }, "missing"],
  ["MixColor", { first: "primary", second: "muted", fraction: 0.25 },
    "color-mix(in srgb, var(--primary) 25%, var(--muted))"],
  ["FormatDate", { date: "2026-01-15T12:00:00Z", locale: "en-US" }, "Jan 15, 2026"],
  ["AddDays", { date: "2024-02-28", days: 2 }, "2024-03-01"],
  ["AddMonths", { date: "2024-01-31", months: 1 }, "2024-02-29"],
  ["DaysBetween", { first: "2024-03-01", second: "2024-02-28" }, -2],
  ["MonthsBetween", { first: "2025-12-31", second: "2026-02-01" }, 2],
  ["StartOf", { date: "2026-01-04", unit: "week" }, "2025-12-29"],
  ["GroupBy", { rows: [{ group: "a", n: 1 }, { group: "a", n: 2 }], field: "group" },
    { a: [{ group: "a", n: 1 }, { group: "a", n: 2 }] }],
  ["Pivot", { rows: [{ g: "a", c: "sales", n: "12.50" }, { g: "a", c: "sales", n: "7.25" }],
    group: "g", category: "c", value: "n" }, [{ g: "a", sales: 19.75 }]],
  ["Top", { rows: [{ n: "1" }, { n: "20" }, { n: "3" }], field: "n", count: 2 },
    [{ n: "20" }, { n: "3" }]],
  ["Between", { rows: [{ n: "1" }, { n: 2 }, { n: "3" }, { n: null }], field: "n", min: 1, max: 2 },
    [{ n: "1" }, { n: 2 }]],
  ["Bucket", { value: -1, width: 10 }, -10],
  ["Cumulative", { values: [2, -1, 4] }, [2, 1, 5]],
  ["MovingAvg", { values: [2, 4, 6], window: 2 }, [2, 3, 5]],
  ["Change", { current: -5, previous: -10 }, 50],
  ["Histogram", { values: [1, 9, 10, -1], width: 10 },
    [{ bucket: -10, count: 1 }, { bucket: 0, count: 2 }, { bucket: 10, count: 1 }]],
  ["Stats", { values: ["12.50", "7.50", null, "invalid", true] },
    { count: 2, sum: 20, mean: 10, min: 7.5, max: 12.5, median: 10 }],
];

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("catalog calculations", () => {
  it.each(cases)("%s returns its documented result without changing inputs", (name, args, expected) => {
    const before = structuredClone(args);
    expect(run(name, args)).toEqual(expected);
    expect(args).toEqual(before);
  });
  it("covers every callable catalog function", () => {
    expect(new Set([...cases.map(([name]) => name), "Pick", "Shuffle", "Random", "Now", "Today"]))
      .toEqual(new Set(uiFunctions.map((fn) => fn.name)));
  });
  it("keeps random outputs within the input population and leaves that population intact", () => {
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    const input = [1, 2, 3];
    expect(run("Pick", { list: input })).toBe(2);
    expect(run("Pick", { list: [] })).toBeNull();
    expect((run("Shuffle", { list: input }) as number[]).sort()).toEqual(input);
    expect(input).toEqual([1, 2, 3]);
    expect(run("Random", { min: 10, max: 20 })).toBe(15);
  });
  it("uses a consistent UTC clock", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-20T23:59:00Z"));
    expect(run("Now", {})).toBe("2026-10-20T23:59:00.000Z");
    expect(run("Today", {})).toBe("2026-10-20");
  });
  it("handles missing members, empty numeric populations and zero denominators explicitly", () => {
    expect(run("Find", { rows: [], field: "id", value: 1 })).toBeNull();
    expect(run("IndexOf", { list: [], item: 1 })).toBe(-1);
    expect(run("Toggle", { list: [], item: 1 })).toEqual([1]);
    expect(run("Percent", { part: 1, total: 0 })).toBeNull();
    expect(run("Change", { current: 1, previous: 0 })).toBeNull();
    expect(run("Allocate", { total: 10, weights: [0, 0] })).toEqual([0, 0]);
    expect(run("Stats", { values: [] })).toEqual({ count: 0, sum: 0, mean: null, min: null, max: null, median: null });
    expect(run("Default", { value: 0, fallback: 42 })).toBe(0);
    expect(run("Plural", { count: 0, singular: "item", plural: "items" })).toBe("items");
    expect(run("Payment", { principal: 100, rate: 0.1, periods: 1 })).toBeCloseTo(110);
  });
  it("bounds list and string growth and handles an unusable range step", () => {
    const list = Array.from({ length: 1000 }, (_, i) => i);
    for (const name of ["Append", "Prepend"]) expect(run(name, { list, item: 1001 })).toHaveLength(1000);
    expect(run("Concat", { first: list, second: list })).toHaveLength(1000);
    expect(run("Flatten", { list: [list, list] })).toHaveLength(1000);
    expect(run("Range", { start: 0, end: 10000, step: 1 })).toHaveLength(1000);
    expect(run("Range", { start: 1, end: 5, step: 0 })).toEqual([]);
    expect(run("Range", { start: 1, end: 5, step: -1 })).toEqual([]);
    expect(run("Replace", { text: "aa", search: "a", replacement: "x".repeat(16000) })).toHaveLength(16000);
    expect(run("Join", { list: ["x".repeat(16000), "y"], separator: "," })).toHaveLength(16000);
  });
  it("rejects precision loss and preserves the null semantics of invalid dates", () => {
    for (const value of ["9007199254740993", "1e-9999", "1e9999"])
      expect(() => run("Stats", { values: [value] })).toThrow("numeric precision");
    expect(run("AddDays", { date: "invalid", days: 1 })).toBeNull();
    expect(run("AddMonths", { date: "invalid", months: 1 })).toBeNull();
    expect(run("StartOf", { date: "2026-08-20", unit: "year" })).toBe("2026-01-01");
    expect(run("StartOf", { date: "2026-08-20", unit: "month" })).toBe("2026-08-01");
  });
});
