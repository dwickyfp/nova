import { afterEach, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api-client";
import { DecisionShares, EffectivenessPanel } from "./decision-governance";
import "@/styles/index.css";

function mount(child: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{child}</QueryClientProvider>,
  );
}

afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
});

it.each([
  { width: 320, dark: true },
  { width: 1280, dark: false },
])(
  "shares and removes access at $width px with keyboard controls",
  async ({ width, dark }) => {
    await page.viewport(width, 700);
    document.documentElement.classList.toggle("dark", dark);
    let items: {
      share_id: string;
      target_name: string;
      target_type: string;
    }[] = [];
    const get = vi
      .spyOn(api, "get")
      .mockImplementation(async () => ({ items }));
    const post = vi.spyOn(api, "post").mockImplementation(async () => {
      items = [
        {
          share_id: "grant-1",
          target_name: "finance_reviewer",
          target_type: "user",
        },
      ];
      return items[0];
    });
    const remove = vi.spyOn(api, "delete").mockImplementation(async () => {
      items = [];
    });
    const screen = await mount(
      <DecisionShares decisionId="decision-1" epoch={1} />,
    );
    expect(get).not.toHaveBeenCalled();
    await userEvent.tab();
    await expect
      .element(screen.getByRole("button", { name: "Manage decision sharing" }))
      .toHaveFocus();
    await userEvent.keyboard("{Enter}");
    await expect
      .element(screen.getByText("This decision has no shares."))
      .toBeVisible();
    await screen
      .getByRole("textbox", { name: "Name", exact: true })
      .fill("finance_reviewer");
    await userEvent.keyboard("{Enter}");
    await expect
      .element(
        screen.getByRole("button", {
          name: "Remove access for finance_reviewer",
        }),
      )
      .toBeVisible();
    expect(post).toHaveBeenCalledWith(
      "/intelligence/decisions/decision-1/shares",
      {
        object_type: "decision",
        object_id: "decision-1",
        target_type: "user",
        target_name: "finance_reviewer",
      },
    );
    await screen
      .getByRole("button", { name: "Remove access for finance_reviewer" })
      .click();
    await expect
      .element(screen.getByText("This decision has no shares."))
      .toBeVisible();
    expect(remove).toHaveBeenCalledWith(
      "/intelligence/decisions/decision-1/shares/grant-1",
    );
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
  },
);

it("hides old shares when the security epoch changes and access is denied", async () => {
  const get = vi
    .spyOn(api, "get")
    .mockResolvedValue({
      items: [
        {
          share_id: "grant-1",
          target_name: "private_reviewer",
          target_type: "user",
        },
      ],
    });
  const client = new QueryClient();
  const component = (epoch: number) => (
    <QueryClientProvider client={client}>
      <DecisionShares decisionId="decision-1" epoch={epoch} />
    </QueryClientProvider>
  );
  const screen = await render(component(1));
  await screen.getByRole("button", { name: "Manage decision sharing" }).click();
  await expect
    .element(screen.getByText("private_reviewer · user"))
    .toBeVisible();
  get.mockRejectedValue(new ApiError(403, "Denied"));
  await screen.rerender(component(2));
  await expect.element(screen.getByRole("alert")).toBeVisible();
  await expect
    .element(screen.getByText("private_reviewer · user"))
    .not.toBeInTheDocument();
});

it("keeps missing effectiveness dimensions unavailable and follows the authorized cursor", async () => {
  const get = vi
    .spyOn(api, "get")
    .mockResolvedValueOnce({
      dimensions: {
        attributed_business_impact: { mean: null, available_outcomes: 0 },
      },
      next_after: "opaque/page",
    })
    .mockResolvedValueOnce({
      dimensions: {
        forecast_relative_error: { mean: 0.25, available_outcomes: 2 },
      },
      next_after: null,
    });
  const screen = await mount(<EffectivenessPanel epoch={1} />);
  await screen.getByRole("button", { name: "Decision effectiveness" }).click();
  await expect
    .element(screen.getByText("Unavailable · 0 measured outcomes"))
    .toBeVisible();
  await screen.getByRole("button", { name: "Load more outcomes" }).click();
  await expect
    .element(screen.getByText("0.25 · 2 measured outcomes"))
    .toBeVisible();
  expect(get).toHaveBeenLastCalledWith(
    "/intelligence/effectiveness?after=opaque%2Fpage",
  );
});
