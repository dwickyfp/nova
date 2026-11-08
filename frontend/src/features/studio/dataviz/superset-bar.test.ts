import { afterEach, describe, expect, it } from "vitest";
import { dockLeft, measureBarGap, supersetBarStacked } from "./superset-bar";

/** A Superset-shaped bar: brand and main menu on the left, actions on the right. */
function bar(height: number): string {
  return `<!doctype html><body style="margin:0">
    <header style="display:flex;height:${height}px;width:1000px">
      <div class="ant-col" style="display:flex;width:600px">
        <span style="width:140px">Superset</span>
        <ul data-test="navbar-top" style="display:flex;margin:0;padding:0;width:460px">
          <li style="width:100px">Dashboards</li><li style="width:100px">SQL</li>
        </ul>
      </div>
      <div class="ant-col" style="display:flex;justify-content:flex-end;width:400px">
        <ul style="display:flex;margin:0;padding:0"><li style="width:120px">Settings</li></ul>
      </div>
    </header></body>`;
}

const frames: HTMLIFrameElement[] = [];

async function framed(html: string): Promise<HTMLIFrameElement> {
  const frame = document.createElement("iframe");
  frame.style.width = "1000px";
  frame.style.height = "400px";
  frames.push(frame);
  const loaded = new Promise((resolve) => frame.addEventListener("load", resolve, { once: true }));
  frame.srcdoc = html;
  document.body.append(frame);
  await loaded;
  return frame;
}

afterEach(() => frames.splice(0).forEach((frame) => frame.remove()));

describe("Superset's bar", () => {
  it("finds the free stretch between the menu items and the actions", async () => {
    const gap = measureBarGap(await framed(bar(60)));
    // Brand 140 + two items 200 = 340; the actions start at 1000 - 120 = 880.
    expect(gap).toEqual({ left: 340, right: 880, height: 60 });
  });

  it("has no free stretch when Superset stacks its bar", async () => {
    const frame = await framed(bar(300));
    expect(measureBarGap(frame)).toBeNull();
    expect(supersetBarStacked(frame)).toBe(true);
    expect(measureBarGap(null)).toBeNull();
  });

  it("docks a group centered in the stretch, or not at all", () => {
    const gap = { left: 340, right: 880, height: 60 };
    expect(dockLeft(gap, 200)).toBe(510);
    // 540 wide, 16 clear on each side: a 520-wide group no longer fits.
    expect(dockLeft(gap, 508)).toBe(356);
    expect(dockLeft(gap, 520)).toBeNull();
    expect(dockLeft(null, 200)).toBeNull();
  });
});
