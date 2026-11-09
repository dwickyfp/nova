import { afterEach, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { api } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import { WebSearchKeyControl } from "./web-search-key-control";
import "@/styles/index.css";

const KEY = "tvly-dev-0123456789abcdef";
const SET = { provider: "tavily", configured: true, key_hint: "tvly-dev-" };
const UNSET = { provider: "tavily", configured: false, key_hint: null };

afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
  useAuthStore.getState().auth.setUser(null);
  document.documentElement.classList.remove("dark");
});

function signIn(role: string) {
  useAuthStore.getState().auth.setUser({ username: "u", roles: [role], activeRole: role });
}

it.each([
  { width: 375, dark: true },
  { width: 1280, dark: false },
])("saves the key write-only and then shows it masked at $width px", async ({ width, dark }) => {
  await page.viewport(width, 800);
  document.documentElement.classList.toggle("dark", dark);
  signIn("ACCOUNTADMIN");
  vi.spyOn(api, "get").mockResolvedValue(UNSET);
  const put = vi.spyOn(api, "put").mockResolvedValue(SET);
  const screen = await render(<WebSearchKeyControl />);

  await expect.element(screen.getByText("Not set")).toBeVisible();
  const input = screen.getByRole("textbox", { name: "Tavily API key" });
  await expect.element(input).toHaveAttribute("type", "password");
  await input.fill(KEY);
  await screen.getByRole("button", { name: "Save key" }).click();

  expect(put).toHaveBeenCalledWith("/ai/web-search/api-key", { api_key: KEY });
  await expect.element(screen.getByText("Active")).toBeVisible();
  await expect.element(screen.getByText("Tavily API key saved.")).toBeVisible();
  // Once stored, only the public prefix and a fixed mask are shown; the key is gone.
  const masked = screen.getByRole("status", { name: "Stored Tavily API key, hidden" });
  await expect.element(masked).toHaveTextContent(/^tvly-dev-•{20}$/);
  await expect.element(screen.getByRole("textbox")).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain(KEY);
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
  await page.screenshot();
});

it("replaces or removes a stored key", async () => {
  signIn("ACCOUNTADMIN");
  vi.spyOn(api, "get").mockResolvedValue(SET);
  const put = vi.spyOn(api, "put").mockResolvedValue({ ...SET, key_hint: "tvly-" });
  const remove = vi.spyOn(api, "delete").mockResolvedValue(UNSET);
  const screen = await render(<WebSearchKeyControl />);

  await expect.element(screen.getByText("Active")).toBeVisible();
  await screen.getByRole("button", { name: "Replace key" }).click();
  await screen.getByRole("button", { name: "Cancel" }).click();
  await expect.element(screen.getByRole("textbox")).not.toBeInTheDocument();

  await screen.getByRole("button", { name: "Replace key" }).click();
  await screen.getByRole("textbox", { name: "Tavily API key" }).fill("tvly-0123456789abcdef");
  await screen.getByRole("button", { name: "Save key" }).click();
  expect(put).toHaveBeenCalledWith("/ai/web-search/api-key", {
    api_key: "tvly-0123456789abcdef",
  });
  await expect
    .element(screen.getByRole("status", { name: "Stored Tavily API key, hidden" }))
    .toHaveTextContent(/^tvly-•+$/);

  await screen.getByRole("button", { name: "Remove" }).click();
  expect(remove).toHaveBeenCalledWith("/ai/web-search/api-key");
  await expect.element(screen.getByText("Not set")).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Remove" })).not.toBeInTheDocument();
});

it("shows only the masked status to a data role", async () => {
  signIn("ANALYST");
  vi.spyOn(api, "get").mockResolvedValue(SET);
  const screen = await render(<WebSearchKeyControl />);
  await expect.element(screen.getByText("Active")).toBeVisible();
  await expect
    .element(screen.getByRole("status", { name: "Stored Tavily API key, hidden" }))
    .toBeVisible();
  await expect
    .element(screen.getByText("An administrator can set the Tavily API key."))
    .toBeVisible();
  await expect.element(screen.getByRole("textbox")).not.toBeInTheDocument();
  await expect.element(screen.getByRole("button", { name: "Replace key" })).not.toBeInTheDocument();
});

it("reports a save failure without echoing the key", async () => {
  signIn("ACCOUNTADMIN");
  vi.spyOn(api, "get").mockResolvedValue(UNSET);
  vi.spyOn(api, "put").mockRejectedValue(new Error("Enter a valid Tavily API key."));
  const screen = await render(<WebSearchKeyControl />);
  await screen.getByRole("textbox", { name: "Tavily API key" }).fill(KEY);
  await screen.getByRole("button", { name: "Save key" }).click();
  await expect.element(screen.getByRole("alert")).toHaveTextContent("Enter a valid Tavily API key.");
  await expect.element(screen.getByText("Not set")).toBeVisible();
});
