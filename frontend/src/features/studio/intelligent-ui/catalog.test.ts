import { describe, expect, it } from "vitest";
import { uiExamples, UI_CATALOG_VERSION, withFieldPaths } from "./catalog";
import { validateProgram } from "./validator";

describe("catalog prompt examples", () => {
  it.each(uiExamples)("$purpose", ({ program }) => {
    const business = program.includes("@Evidence");
    const validation = validateProgram(program, {
      "result-id": [{
        month: "January", revenue: 100, longitude: 106.8, latitude: -6.2, branch: "A", name: "A", geometry: "LINESTRING (106.8 -6.2,107.6 -6.9)", progress: 0.5, destination_longitude: 107.6, destination_latitude: -6.9,
      }],
    }, business);
    expect(validation.catalog_version).toBe(UI_CATALOG_VERSION);
    expect(validation.evidence_ids).toEqual(business ? ["result-id"] : []);
  });
});

describe("map action selections", () => {
  const evidence = { places: [
    { branch: "Jakarta", lon: 106.8, lat: -6.2, distance: 0 },
    { branch: "Bandung", lon: 107.6, lat: -6.9, distance: 120 },
    { branch: "Invalid", lon: null, lat: -7, distance: 150 },
  ] };
  const program = '$radius=200\n$selected=-1\nroot=Col([GeoMap(@Top(@Between(@Evidence("places"),"distance",0,$radius),"distance",10),"lon","lat",null,"branch","Branches",$selected)])';
  it("resolves the displayed sorted and filtered row, not the original index", () => {
    expect(validateProgram(program, evidence, true, { $radius: 130, $selected: 0 }).selections)
      .toEqual([{ title: "Branches", row: evidence.places[1], displayed_rows: 2 }]);
    expect(validateProgram(program, evidence, true, { $radius: 0, $selected: 0 }).selections[0].row)
      .toEqual(evidence.places[0]);
  });
  it.each([-1, 3, 0.5, 0])("does not guess absent, stale, fractional or invalid geometry selections (%s)", (selected) => {
    expect(validateProgram(program, evidence, true, { $selected: selected }).selections[0].row).toBeNull();
  });
  it("refuses random ordering that cannot preserve selection across runtimes", () => {
    expect(() => validateProgram(program.replace('@Top(@Between(@Evidence("places"),"distance",0,$radius),"distance",10)', '@Shuffle(@Evidence("places"))'), evidence, true))
      .toThrow("deterministic");
  });
});

describe("conditional program validation", () => {
  it.each([
    '$derived=@Sum([1,2])',
    '$derived=[$base]',
    '$derived=$base*2',
    '$derived={amount:$base}',
  ])("rejects unevaluated state expressions: %s", (declaration) => {
    expect(() => validateProgram(`$base=5\n${declaration}\nroot=Col([Text($derived)])`))
      .toThrow("State defaults must be literal");
  });
  it("keeps derived regular variables reactive", () => {
    const program = '$a=25\n$b=75\nweights=[$a,$b]\ntotal=@Sum(weights)\nroot=Col([Text("Total: "+total)])';
    expect(validateProgram(program).display_text).toContain("100");
    expect(validateProgram(program, {}, false, { $a: 50 }).display_text).toContain("125");
  });
  it("collects displayed calculator values without executing button arithmetic", () => {
    const parsed = validateProgram(
      '$units=5\nroot=Col([Text("Total: "+($units*20)),Button("Double",@Set($units,$units*2),"double")])',
    );
    expect(parsed.simulation_tables).toEqual([{ columns: ["Calculated value"], rows: [[100]] }]);
  });
  it("exports evaluated visible simulation rows without promoting them to evidence", () => {
    const program = '$total=8000000\n$show=true\nroot=Col([$show ? Chart([{name:"Venue",amount:$total*0.25},{name:"Konsumsi",amount:$total*0.2}],"name","amount","bar") : Table([{name:"Hidden",amount:999}])])';
    const parsed = validateProgram(program);
    expect(parsed.simulation_tables).toEqual([{
      columns: ["name", "amount"], rows: [["Venue", 2000000], ["Konsumsi", 1600000]],
    }]);
    expect(parsed.evidence_ids).toEqual([]);
    expect(validateProgram(program, {}, false, { $total: 4000000 }).simulation_tables[0].rows)
      .toEqual([["Venue", 1000000], ["Konsumsi", 800000]]);
    expect(validateProgram('root=Col([Table(@Evidence("e"))])',
      { e: [{ amount: 42 }] }, true).simulation_tables).toEqual([]);
  });
  it("declares follow-up actions before their conditional branch becomes visible", () => {
    const program = '$show=false\nroot=Col([$show ? Button("Continue",@ToAssistant("Use inputs"),"next") : Text("Fill the form")])';
    expect(validateProgram(program).actions).toEqual([{ id: "next", assistant: true }]);
    expect(validateProgram(program, {}, false, { $show: true }).actions)
      .toEqual([{ id: "next", assistant: true }]);
  });
  it("refuses invented business rows even in a hidden branch", () => {
    expect(() => validateProgram('$show=false\nroot=Col([$show ? Table([{amount:999}]) : Text("Ready")])', {}, true))
      .toThrow("verified evidence");
  });
  it("refuses duplicate actions across mutually exclusive branches", () => {
    expect(() => validateProgram('$show=false\nroot=Col([$show ? Button("A",@ToAssistant("A"),"same") : Button("B",@ToAssistant("B"),"same")])'))
      .toThrow("unique");
  });
  it("validates hidden sandbox content and rejects repeated action controls", () => {
    expect(() => validateProgram('$show=false\nroot=Col([$show ? HtmlView("<script>fetch(\\"https://example.com\\")</script>","Unsafe") : Text("Ready")])'))
      .toThrow();
    expect(() => validateProgram('root=Col([@Each([1,2],"n",Button("Next",@ToAssistant("Next"),"next"))])'))
      .toThrow("passive");
  });
});

describe("semantic column names with a dot", () => {
  const evidence = { e: [
    { "orders.month": "2026-06-01", "orders.revenue": "20", lon: 106.8, lat: -6.2 },
    { "orders.month": "2026-05-01", "orders.revenue": "10", lon: 107.6, lat: -6.9 },
    { "orders.month": "2026-07-01", "orders.revenue": "30", lon: 110.4, lat: -7.0 },
  ] };
  it("sorts rows by a <table>.<column> field instead of keeping the query order", () => {
    const program = '$s=0\nroot=Col([GeoMap(@Sort(@Evidence("e"),"orders.month","asc"),"lon","lat",null,"orders.month","Where",$s)])';
    const first = validateProgram(program, evidence, true).selections[0].row;
    expect(first).toEqual(evidence.e[1]);
    const latest = validateProgram(program.replace('"asc"', '"desc"'), evidence, true).selections[0].row;
    expect(latest).toEqual(evidence.e[2]);
  });
  it("keeps the visible columns and the source rows unchanged", () => {
    const [row] = withFieldPaths(evidence.e)!;
    expect(Object.keys(row)).toEqual(["orders.month", "orders.revenue", "lon", "lat"]);
    expect(JSON.stringify(row)).toBe(JSON.stringify(evidence.e[0]));
    expect((row as { orders?: { month?: string } }).orders?.month).toBe("2026-06-01");
    expect(Object.keys(evidence.e[0])).not.toContain("orders");
  });
  it("never shadows a real column that shares the table name", () => {
    const [row] = withFieldPaths([{ orders: 5, "orders.month": "2026-05-01" }])!;
    expect(row.orders).toBe(5);
  });
  it("never traverses prototypes while resolving result column paths", () => {
    const source = {
      "__proto__.docsQaPolluted": true,
      "constructor.prototype.docsQaPolluted": true,
      "orders.__proto__.docsQaPolluted": true,
      "orders.month": "2026-05-01",
    };
    const [row] = withFieldPaths([source])!;
    expect(Object.prototype).not.toHaveProperty("docsQaPolluted");
    expect(row).toEqual(source);
    expect(Object.getPrototypeOf(row.orders)).toBeNull();
  });
});
