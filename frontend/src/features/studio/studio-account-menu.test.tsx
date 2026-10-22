import { describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import { StudioAccountMenu } from "./studio-account-menu";
import "@/styles/index.css";

vi.mock("@/components/sign-out-dialog", () => ({ SignOutDialog: () => null }));
vi.mock("@/context/theme-provider", () => ({
  useTheme: () => ({ theme: "dark", setTheme: vi.fn() }),
}));

const LONG_ROLE = "NOVA_TASK_DEMO_RUNTIME_ANALYST_WITH_A_VERY_LONG_NAME";

describe("StudioAccountMenu role switcher", () => {
  it("keeps long role names inside the menu and exposes the full name", async () => {
    await render(<StudioAccountMenu
      identity={{
        username: "role_owner",
        roles: ["ACCOUNTADMIN", LONG_ROLE],
        active_role: LONG_ROLE,
        warehouses: [],
        active_warehouse: null,
      }}
      onIdentityChange={() => {}}
    />);

    await page.getByRole("button", { name: /role_owner/ }).click();
    const trigger = page.getByRole("menuitem", { name: LONG_ROLE, exact: true }).first();
    await expect.element(trigger).toHaveAttribute("title", LONG_ROLE);
    await trigger.click();

    const option = page.getByRole("menuitem", { name: LONG_ROLE, exact: true }).nth(1);
    await expect.element(option).toHaveAttribute("title", LONG_ROLE);

    for (const item of [trigger.element(), option.element()]) {
      const menu = item.closest<HTMLElement>("[role='menu']")!;
      const label = item.querySelector<HTMLElement>("span")!;
      expect(item.getBoundingClientRect().right).toBeLessThanOrEqual(menu.getBoundingClientRect().right);
      expect(label.scrollWidth).toBeGreaterThan(label.clientWidth);
      expect(getComputedStyle(label).textOverflow).toBe("ellipsis");
    }
  });
});
