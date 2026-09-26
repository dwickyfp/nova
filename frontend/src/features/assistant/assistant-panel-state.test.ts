import { describe, expect, it } from "vitest";
import {
  ASSISTANT_DEFAULT_OFFSET_Y,
  ASSISTANT_DEFAULT_WIDTH,
  ASSISTANT_MAX_WIDTH,
  ASSISTANT_MIN_WIDTH,
  ASSISTANT_OFFSET_Y_MARGIN,
  ASSISTANT_TOGGLE_REST_BOTTOM,
  assistantCollapsedToPersist,
  clampAssistantOffsetY,
  clampAssistantWidth,
  parseAssistantOffsetY,
  parseAssistantWidth,
} from "./assistant-panel-state";

describe("assistant panel persistence polarity", () => {
  it("persists an open panel as not collapsed", () => {
    const persisted = assistantCollapsedToPersist(true);
    expect(persisted).toBe(false);
  });

  it("persists a closed panel as collapsed", () => {
    const persisted = assistantCollapsedToPersist(false);
    expect(persisted).toBe(true);
  });
});

describe("assistant panel width bounds", () => {
  it("clamps below the minimum up to the minimum", () => {
    expect(clampAssistantWidth(ASSISTANT_MIN_WIDTH - 200)).toBe(
      ASSISTANT_MIN_WIDTH,
    );
  });

  it("clamps above the maximum down to the maximum", () => {
    expect(clampAssistantWidth(ASSISTANT_MAX_WIDTH + 200)).toBe(
      ASSISTANT_MAX_WIDTH,
    );
  });

  it("keeps a width inside the range and rounds it", () => {
    expect(clampAssistantWidth(480.6)).toBe(481);
  });

  it("falls back to the default for a non-finite width", () => {
    expect(clampAssistantWidth(Number.NaN)).toBe(ASSISTANT_DEFAULT_WIDTH);
  });

  it("parses a persisted value with the same clamp", () => {
    expect(parseAssistantWidth("500")).toBe(500);
    expect(parseAssistantWidth("9999")).toBe(ASSISTANT_MAX_WIDTH);
    expect(parseAssistantWidth("10")).toBe(ASSISTANT_MIN_WIDTH);
    expect(parseAssistantWidth(undefined)).toBe(ASSISTANT_DEFAULT_WIDTH);
    expect(parseAssistantWidth("not-a-number")).toBe(ASSISTANT_DEFAULT_WIDTH);
  });
});

describe("assistant toggle vertical offset", () => {
  // A resting button in an 800px viewport: 48px tall, 16px from the bottom.
  const restRect = {
    top: 800 - ASSISTANT_TOGGLE_REST_BOTTOM - 48,
    bottom: 800 - ASSISTANT_TOGGLE_REST_BOTTOM,
  };
  const viewport = 800;

  it("defaults to zero and parses a persisted value as an integer", () => {
    expect(parseAssistantOffsetY(undefined)).toBe(ASSISTANT_DEFAULT_OFFSET_Y);
    expect(parseAssistantOffsetY("120")).toBe(120);
    expect(parseAssistantOffsetY("-40")).toBe(-40);
    expect(parseAssistantOffsetY("not-a-number")).toBe(
      ASSISTANT_DEFAULT_OFFSET_Y,
    );
  });

  it("allows moving up until the margin below the top edge", () => {
    const maxUp = clampAssistantOffsetY(viewport, restRect, viewport);
    expect(maxUp).toBe(restRect.top - ASSISTANT_OFFSET_Y_MARGIN);
  });

  it("allows moving down only as far as the bottom margin", () => {
    const maxDown = clampAssistantOffsetY(-viewport, restRect, viewport);
    // The resting rect sits `ASSISTANT_TOGGLE_REST_BOTTOM` from the edge, so
    // there is exactly the difference to the margin left to move down.
    expect(maxDown).toBe(
      -(ASSISTANT_TOGGLE_REST_BOTTOM - ASSISTANT_OFFSET_Y_MARGIN),
    );
  });

  it("keeps an in-range offset untouched", () => {
    expect(clampAssistantOffsetY(60, restRect, viewport)).toBe(60);
    expect(clampAssistantOffsetY(-0, restRect, viewport)).toBe(0);
  });

  it("falls back to zero for a non-finite offset", () => {
    expect(clampAssistantOffsetY(Number.NaN, restRect, viewport)).toBe(
      ASSISTANT_DEFAULT_OFFSET_Y,
    );
  });
});
