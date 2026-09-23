import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const origin = process.env.NOVA_UI_URL || "http://127.0.0.1:5173";
const credentialFile =
  process.env.RBAC_DEMO_CREDENTIAL_FILE ||
  path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../workspace/rbac_city_demo/.env",
  );
const passwords = Object.fromEntries(
  fs
    .readFileSync(credentialFile, "utf8")
    .trim()
    .split("\n")
    .map((line) => {
      const separator = line.indexOf("=");
      return [line.slice(0, separator), line.slice(separator + 1)];
    }),
);

async function checkUser(browser, username, city, amount) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.goto(`${origin}/sign-in`);
    await page.getByRole("textbox", { name: /username/i }).fill(username);
    await page
      .getByLabel("Password", { exact: true })
      .fill(passwords[username]);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(`${origin}/`);

    await page.goto(`${origin}/workspaces`);
    await page.getByText("New File").first().waitFor();
    await page.getByText("New File").first().click();
    await page.locator(".monaco-editor").click();
    await page.keyboard.insertText(
      "SELECT id, city, amount FROM rbac_city_demo.city_sales ORDER BY id",
    );
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await page.getByText("Rows 1–2 of 2").waitFor({ timeout: 15000 });
    const workspaceText = await page.locator("body").innerText();
    const ownAmounts =
      city === "Jakarta" ? ["100.00", "300.00"] : ["200.00", "400.00"];
    if (
      ownAmounts.some((value) => !workspaceText.includes(`${city}\t${value}`))
    ) {
      throw new Error(
        `${username}: Workspace did not show the expected city amount`,
      );
    }
    const forbidden = city === "Jakarta" ? "Bandung" : "Jakarta";
    if (workspaceText.includes(`\t${forbidden}\t`)) {
      throw new Error(`${username}: Workspace showed the other city`);
    }

    await page.goto(`${origin}/studio`);
    await page.getByText("City RBAC Agent").first().waitFor({ timeout: 15000 });
    await page.getByRole("button", { name: "New chat" }).click();
    await page.locator("textarea").fill("Berapa total_amount per city?");
    await page.locator("textarea").press("Enter");
    try {
      await page
        .getByText(city, { exact: true })
        .last()
        .waitFor({ timeout: 60000 });
    } catch (error) {
      const snapshot = (await page.locator("body").innerText()).slice(-1200);
      throw new Error(`${username}: Studio result timed out. ${snapshot}`, {
        cause: error,
      });
    }
    const studioText = await page.locator("body").innerText();
    if (!studioText.includes(amount)) {
      throw new Error(`${username}: Studio did not show the expected total`);
    }

    if (city === "Jakarta") {
      await page.getByRole("button", { name: "New chat" }).click();
      await page
        .locator("textarea")
        .fill("Berapa total_amount per city hanya untuk Bandung?");
      await page.locator("textarea").press("Enter");
      await page
        .getByText("The authorized query returned no rows for this request.")
        .waitFor({ timeout: 60000 });
      await page.getByText("The query returned no rows.").waitFor();
    }

    if (errors.length) throw new Error(`${username}: ${errors.join("; ")}`);
    console.log(`PASS ${username}: Workspace and Studio show ${city} only`);
  } finally {
    await context.close();
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    await checkUser(browser, "rbac_jakarta", "Jakarta", "400.00");
    await checkUser(browser, "rbac_bandung", "Bandung", "600.00");
    console.log(
      "PASS Jakarta asks Bandung in Studio: empty result and no browser errors",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
