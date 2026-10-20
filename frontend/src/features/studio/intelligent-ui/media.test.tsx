import { afterEach, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { PublicMedia } from "./media";
import { UiRuntimeContext } from "./runtime-context";
import { uiApi } from "./api";
import "maplibre-gl/dist/maplibre-gl.css";
import "@/styles/index.css";

vi.mock("./api", () => ({ uiApi: { media: vi.fn(), mapMetric: vi.fn().mockResolvedValue({}) } }));
afterEach(() => vi.resetAllMocks());
const source = {
  agentId: "agent",
  threadId: "thread",
  messageId: "message",
  contentId: "content",
};
it.each([true, false])("retains public places when media is unavailable (source=%s)", async (hasSource) => {
  vi.mocked(uiApi.media).mockRejectedValueOnce(new Error("Unavailable"));
  const screen = await render(
    <UiRuntimeContext.Provider value={{ source: hasSource ? source : undefined,
      readOnly: false, markAction: () => {} }}>
      <PublicMedia name="Map" p={{ label: "Jakarta landmarks", markers: [
        { type: "Marker", props: { label: "Monas", longitude: 106.8272, latitude: -6.1754 } },
        { type: "Marker", props: { label: "Museum Nasional", longitude: 106.8227, latitude: -6.1764 } },
      ] }} />
    </UiRuntimeContext.Provider>,
  );
  await expect.element(screen.getByRole("status")).toHaveTextContent("Places are listed below");
  await expect.element(screen.getByRole("listitem").filter({ hasText: "Monas" })).toBeVisible();
  await expect.element(screen.getByRole("listitem").filter({ hasText: "Museum Nasional" })).toBeVisible();
  expect(uiApi.media).toHaveBeenCalledTimes(hasSource ? 1 : 0);
});

it.each(["light", "dark"])("keeps public map popup text readable in %s", async (theme) => {
  const screen = await render(
    <div className={theme}>
      <section className="nova-public-map">
        <div className="maplibregl-popup maplibregl-popup-anchor-bottom">
          <div className="maplibregl-popup-tip" />
          <div className="maplibregl-popup-content"><p>Monas</p></div>
        </div>
      </section>
    </div>,
  );
  const popup = screen.container.querySelector(".maplibregl-popup-content")!;
  const style = getComputedStyle(popup);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const context = canvas.getContext("2d", { willReadFrequently: true })!;
  const luminance = (color: string) => {
    context.clearRect(0, 0, 1, 1);
    context.fillStyle = color;
    context.fillRect(0, 0, 1, 1);
    const rgb = [...context.getImageData(0, 0, 1, 1).data].slice(0, 3).map((value) => {
      const channel = value / 255;
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
  };
  const values = [luminance(style.color), luminance(style.backgroundColor)].sort((a, b) => b - a);
  expect((values[0] + 0.05) / (values[1] + 0.05)).toBeGreaterThanOrEqual(4.5);
  expect(getComputedStyle(screen.container.querySelector(".maplibregl-popup-tip")!).borderTopColor)
    .toBe(style.backgroundColor);
});

function image(query: string) {
  return (
    <UiRuntimeContext.Provider
      value={{ source, readOnly: false, markAction: () => {} }}
    >
      <PublicMedia name="SearchImage" p={{ query, alt: query }} />
    </UiRuntimeContext.Provider>
  );
}
it("recovers from a failed image when the source changes", async () => {
  vi.mocked(uiApi.media).mockRejectedValueOnce(new Error("Unavailable"));
  const screen = await render(image("London"));
  await expect.element(screen.getByText(/could not load/)).toBeVisible();
  vi.mocked(uiApi.media).mockResolvedValueOnce({
    url: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
    source: "https://commons.wikimedia.org/",
    description: "Paris",
  });
  await screen.rerender(image("Paris"));
  await expect
    .element(screen.getByRole("img", { name: "Paris" }))
    .toBeVisible();
  await expect
    .element(screen.getByText(/could not load/))
    .not.toBeInTheDocument();
});
it("ignores a stale media request even when the transport resolves after abort", async () => {
  let finish!: (value: Record<string, unknown>) => void;
  vi.mocked(uiApi.media).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const screen = await render(image("London"));
  await vi.waitFor(() => expect(uiApi.media).toHaveBeenCalledTimes(1));
  vi.mocked(uiApi.media).mockResolvedValueOnce({
    url: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
    source: "https://commons.wikimedia.org/",
    description: "Paris",
  });
  await screen.rerender(image("Paris"));
  await expect.element(screen.getByText(/Paris ·/)).toBeVisible();
  finish({
    url: "https://upload.wikimedia.org/old.jpg",
    description: "London",
  });
  await expect.element(screen.getByText(/Paris ·/)).toBeVisible();
});
it("clears a previous failure when revisiting a source succeeds", async () => {
  vi.mocked(uiApi.media).mockRejectedValueOnce(new Error("Unavailable"));
  const screen = await render(image("London"));
  await expect.element(screen.getByText(/could not load/)).toBeVisible();
  vi.mocked(uiApi.media).mockRejectedValueOnce(new Error("Unavailable"));
  await screen.rerender(image("Paris"));
  await vi.waitFor(() => expect(uiApi.media).toHaveBeenCalledTimes(2));
  await expect.element(screen.getByText(/could not load/)).toBeVisible();
  vi.mocked(uiApi.media).mockResolvedValueOnce({
    url: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
    source: "https://commons.wikimedia.org/",
    description: "London",
  });
  await screen.rerender(image("London"));
  await expect.element(screen.getByRole("img", { name: "London" })).toBeVisible();
  await expect.element(screen.getByText(/could not load/)).not.toBeInTheDocument();
});
