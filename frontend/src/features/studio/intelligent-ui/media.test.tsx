import { afterEach, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { PublicMedia } from "./media";
import { UiRuntimeContext } from "./runtime-context";
import { uiApi } from "./api";

vi.mock("./api", () => ({ uiApi: { media: vi.fn() } }));
afterEach(() => vi.resetAllMocks());
const source = {
  agentId: "agent",
  threadId: "thread",
  messageId: "message",
  contentId: "content",
};
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
