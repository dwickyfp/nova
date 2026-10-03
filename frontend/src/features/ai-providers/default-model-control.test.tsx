import { afterEach, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { api } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import { DefaultModelControl } from "./default-model-control";
import "@/styles/index.css";

const providers = [
  {
    id: "deepseek",
    name: "DeepSeek",
    is_active: true,
    models: [
      {
        id: "flash",
        name: "deepseek-v4-1-flash",
        display_name: null,
        type: "llm",
        is_active: true,
      },
      {
        id: "embed",
        name: "Embedding",
        display_name: null,
        type: "embedding",
        is_active: true,
      },
    ],
  },
];

afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
  useAuthStore.getState().auth.setUser(null);
  document.documentElement.classList.remove("dark");
});

it.each([
  { width: 320, dark: true },
  { width: 1280, dark: false },
])("saves the registered default at $width px", async ({ width, dark }) => {
  await page.viewport(width, 800);
  document.documentElement.classList.toggle("dark", dark);
  useAuthStore
    .getState()
    .auth.setUser({
      username: "admin",
      roles: ["ACCOUNTADMIN"],
      activeRole: "ACCOUNTADMIN",
    });
  vi.spyOn(api, "get").mockResolvedValue({ model_id: null });
  const put = vi.spyOn(api, "put").mockResolvedValue({ model_id: "flash" });
  const screen = await render(
    <DefaultModelControl providers={providers} loading={false} />,
  );
  await screen
    .getByRole("combobox", { name: "Default assistant model" })
    .click();
  await expect
    .element(screen.getByRole("option", { name: "Embedding" }))
    .not.toBeInTheDocument();
  await screen
    .getByRole("option", { name: "deepseek-v4-1-flash · DeepSeek" })
    .click();
  await screen.getByRole("button", { name: "Save default" }).click();
  expect(put).toHaveBeenCalledWith("/ai/default-model", { model_id: "flash" });
  await expect
    .element(screen.getByRole("status"))
    .toHaveTextContent("Default model saved.");
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
  await page.screenshot();
});

it("keeps an unavailable model visible and denies editing for a data role", async () => {
  useAuthStore
    .getState()
    .auth.setUser({
      username: "analyst",
      roles: ["analyst"],
      activeRole: "analyst",
    });
  vi.spyOn(api, "get").mockResolvedValue({ model_id: "removed" });
  const screen = await render(
    <DefaultModelControl providers={providers} loading={false} />,
  );
  await expect
    .element(screen.getByRole("combobox"))
    .toHaveTextContent("Configured model unavailable");
  await expect.element(screen.getByRole("combobox")).toBeDisabled();
  await expect
    .element(screen.getByRole("button", { name: "Save default" }))
    .not.toBeInTheDocument();
});

it("shows a failed save without claiming the new default was saved", async () => {
  useAuthStore
    .getState()
    .auth.setUser({
      username: "admin",
      roles: ["ACCOUNTADMIN"],
      activeRole: "ACCOUNTADMIN",
    });
  vi.spyOn(api, "get").mockResolvedValue({ model_id: null });
  vi.spyOn(api, "put").mockRejectedValue(
    new Error("Model is no longer active"),
  );
  const screen = await render(
    <DefaultModelControl providers={providers} loading={false} />,
  );
  await screen.getByRole("combobox").click();
  await screen
    .getByRole("option", { name: "deepseek-v4-1-flash · DeepSeek" })
    .click();
  await screen.getByRole("button", { name: "Save default" }).click();
  await expect
    .element(screen.getByRole("alert"))
    .toHaveTextContent("Model is no longer active");
  await expect.element(screen.getByRole("status")).toHaveTextContent("");
  await expect
    .element(screen.getByRole("button", { name: "Save default" }))
    .toBeEnabled();
});
