import { build, preview } from "vite";
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { resolve } from "node:path";

const output = resolve(
  process.env.NOVA_UI_QA_OUTPUT ?? "/tmp/nova-intelligent-ui-browser-qa",
);
mkdirSync(output, { recursive: true });
const fixture = resolve(".nova-ui-acceptance.html");
const production = `${output}/production`;
writeFileSync(
  fixture,
  '<!doctype html><html><head><title>Nova UI acceptance</title></head><body><div id="root"></div><script type="module" src="/src/test-utils/intelligent-ui-preview.tsx"></script></body></html>',
);
try {
  await build({
    build: {
      outDir: production,
      emptyOutDir: true,
      rolldownOptions: { input: fixture },
    },
  });
} finally {
  rmSync(fixture, { force: true });
}
const server = await preview({
  build: { outDir: production },
  preview: { port: 0, host: "127.0.0.1" },
});
const browser = await chromium.launch({ headless: true });
const report = [];
try {
  for (const theme of ["light", "dark"]) {
    for (const width of [1440, 768, 320]) {
      const page = await browser.newPage({
        viewport: { width, height: 1000 },
        colorScheme: theme,
      });
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(
        `http://127.0.0.1:${server.httpServer.address().port}/.nova-ui-acceptance.html?theme=${theme}`,
      );
      await page.getByText("Total: 100", { exact: true }).waitFor();
      await page.getByRole("spinbutton", { name: "Units" }).fill("7");
      await page.getByText("Total: 140", { exact: true }).waitFor();
      await page.getByRole("checkbox", { name: "Checklist complete" }).check();
      const slider = page.getByRole("slider", { name: "Rate" });
      await slider.focus();
      await page.keyboard.press("ArrowRight");
      await page.getByRole("button", { name: "Review with Nova" }).click();
      if (await page.locator("#action-result").textContent())
        throw new Error("Empty required form was accepted");
      await page
        .getByRole("textbox", { name: "Email", exact: true })
        .fill("qa@example.com");
      await page
        .getByRole("textbox", { name: "Notes" })
        .fill("Assumptions to review");
      await page.getByRole("button", { name: "Review with Nova" }).click();
      await page.getByText("Sent through Nova", { exact: true }).waitFor();
      await page
        .getByRole("combobox", { name: "Chart measure" })
        .selectOption("b");
      await page.getByText("View chart data", { exact: true }).click();
      await page.getByRole("cell", { name: "20", exact: true }).waitFor();
      await page
        .getByRole("combobox", { name: "Chart filter" })
        .selectOption("Example B");
      await page.getByRole("cell", { name: "15", exact: true }).waitFor();
      if (await page.getByRole("cell", { name: "20", exact: true }).count())
        throw new Error("Chart filter kept a row outside its selected scope");
      const chart = page.getByRole("region", {
        name: "Sample comparison",
        exact: true,
      });
      await chart.scrollIntoViewIfNeeded();
      await chart.locator(".recharts-rectangle").waitFor();
      const chartBounds = await chart
        .locator(".recharts-rectangle")
        .boundingBox();
      if (!chartBounds || chartBounds.width < 1 || chartBounds.height < 1)
        throw new Error("Filtered chart has no visible bar");
      await chart.screenshot({ path: `${output}/${theme}-${width}-chart.png` });
      await page.getByRole("button", { name: "Show answer" }).click();
      await page.getByRole("button", { name: "Show question" }).waitFor();
      await page.getByRole("button", { name: "Start", exact: true }).click();
      await page.locator("output").filter({ hasText: "0:02" }).waitFor();
      await page.getByRole("button", { name: "Reset", exact: true }).click();
      await page.locator("output").filter({ hasText: "0:03" }).waitFor();
      await page.getByRole("button", { name: "Next page" }).click();
      await page.getByText("Page two", { exact: true }).waitFor();
      await page.getByRole("button", { name: "Previous page" }).click();
      await page.getByLabel("Review date").fill("2026-10-14");
      await page.getByRole("radio", { name: "Second", exact: true }).check();
      await page.getByRole("radio", { name: "B", exact: true }).check();
      await page.getByRole("button", { name: "Increment count" }).click();
      await page.getByText("Clicks: 1", { exact: true }).waitFor();
      await page
        .getByRole("button", { name: "Local action Reset count" })
        .click();
      await page.getByText("Clicks: 0", { exact: true }).waitFor();
      for (const [name, url] of [
        [
          "Open UI reference",
          "https://github.com/thesysdev/open-intelligent-ui",
        ],
        ["Watch Public video", "https://www.youtube.com/watch?v=jNQXAC9IVRw"],
      ]) {
        const link = page.getByRole("link", { name, exact: true });
        if ((await link.getAttribute("href")) !== url)
          throw new Error("Public link destination changed");
        const opened = page.waitForEvent("popup");
        await link.click();
        const popup = await opened;
        await popup.close();
      }
      await page.getByText("Details", { exact: true }).click();
      await page
        .getByText("Only declared inputs are saved.", { exact: true })
        .waitFor();
      const game = page.frameLocator('iframe[title="Canvas game"]');
      await game.getByRole("button", { name: "Play canvas game" }).click();
      await game.getByText("Score: 1", { exact: true }).waitFor();
      const isolated = await game.locator("canvas").evaluate((canvas) => ({
        painted: canvas.getContext("2d").getImageData(5, 5, 1, 1).data[3] > 0,
        parentDenied: (() => {
          try {
            return !window.parent.document;
          } catch {
            return true;
          }
        })(),
      }));
      if (!isolated.painted || !isolated.parentDenied)
        throw new Error("Canvas or parent isolation failed");
      await page
        .getByText("Public media could not load.", { exact: false })
        .waitFor();
      await page.evaluate(() => window.uiAcceptance.flush());
      const saved = await page.evaluate(() => window.uiAcceptance.saved());
      if (
        saved.state.$units !== 7 ||
        saved.state.$ready !== true ||
        saved.state.$rate !== 4 ||
        saved.state.$date !== "2026-10-14" ||
        saved.state.$choice !== "Second" ||
        saved.state.$segment !== "B" ||
        saved.state.$clicks !== 0
      )
        throw new Error("Declarative state did not save");
      await game.getByText("Score: 1", { exact: true }).waitFor();
      await page
        .locator('iframe[title="Canvas game"]')
        .scrollIntoViewIfNeeded();
      await page
        .locator('iframe[title="Canvas game"]')
        .screenshot({ path: `${output}/${theme}-${width}-canvas.png` });
      const map = page.getByRole("region", {
        name: "Public itinerary",
        exact: true,
      });
      await map.scrollIntoViewIfNeeded();
      await map.locator(".maplibregl-marker").last().waitFor();
      const mapCanvas = await map.locator("canvas").boundingBox();
      const mapContainer = await map.locator(".maplibregl-map").boundingBox();
      if (
        !mapCanvas ||
        !mapContainer ||
        Math.abs(mapCanvas.width - mapContainer.width) > 2
      )
        throw new Error("Public map canvas does not fill its container");
      await map.screenshot({ path: `${output}/${theme}-${width}-map.png` });
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth + 1,
      );
      if (overflow) throw new Error(`Horizontal overflow at ${width}/${theme}`);
      await page.getByRole("spinbutton", { name: "Units" }).focus();
      await page.keyboard.press("Tab");
      if (await page.evaluate(() => document.activeElement === document.body))
        throw new Error("Keyboard focus lost");
      if (errors.length) throw new Error(errors.join("; "));
      const contrast = await page.evaluate(() => {
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = 1;
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        const rgb = (css) => {
          ctx.clearRect(0, 0, 1, 1);
          ctx.fillStyle = css;
          ctx.fillRect(0, 0, 1, 1);
          return [...ctx.getImageData(0, 0, 1, 1).data];
        };
        const luminance = (v) =>
          v
            .slice(0, 3)
            .map((c) => {
              const n = c / 255;
              return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
            })
            .reduce((s, n, i) => s + n * [0.2126, 0.7152, 0.0722][i], 0);
        const ratio = (a, b) => {
          const x = luminance(a),
            y = luminance(b);
          return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
        };
        const background = (el) => {
          for (let current = el; current; current = current.parentElement) {
            const color = rgb(getComputedStyle(current).backgroundColor);
            if (color[3] === 255) return color;
          }
          return rgb(getComputedStyle(document.body).backgroundColor);
        };
        const checks = [];
        const walker = document.createTreeWalker(
          document.querySelector("main"),
          NodeFilter.SHOW_TEXT,
        );
        while (walker.nextNode()) {
          const node = walker.currentNode,
            el = node.parentElement;
          if (
            !node.textContent.trim() ||
            !el.getClientRects().length ||
            el.closest(".maplibregl-map, script, style")
          )
            continue;
          const style = getComputedStyle(el);
          if (style.visibility === "hidden") continue;
          const large =
            parseFloat(style.fontSize) >= 24 ||
            (parseFloat(style.fontSize) >= 18.66 &&
              Number(style.fontWeight) >= 700);
          const bg =
            el.tagName.toLowerCase() === "text" &&
            el.closest("svg")?.querySelector("rect")
              ? rgb(
                  getComputedStyle(el.closest("svg").querySelector("rect"))
                    .fill,
                )
              : background(el);
          const fg = rgb(
            el.tagName.toLowerCase() === "text" ? style.fill : style.color,
          );
          checks.push({
            text: node.textContent.trim().slice(0, 40),
            ratio: ratio(fg, bg),
            required: large ? 3 : 4.5,
          });
        }
        for (const el of document.querySelectorAll(
          "main input, main select, main textarea",
        )) {
          const style = getComputedStyle(el);
          if (el.getClientRects().length)
            checks.push({
              text: `${el.tagName} ${el.type} boundary`,
              ratio: ratio(
                rgb(
                  parseFloat(style.borderTopWidth)
                    ? style.borderTopColor
                    : style.accentColor,
                ),
                background(el),
              ),
              required: 3,
            });
        }
        for (const el of document.querySelectorAll(
          "main .recharts-rectangle",
        )) {
          const style = getComputedStyle(el);
          checks.push({
            text: "Chart bar boundary",
            ratio: ratio(rgb(style.stroke), rgb(style.fill)),
            required: 3,
          });
        }
        return {
          minimum: Math.min(...checks.map((c) => c.ratio)),
          checks: checks.length,
          failures: checks.filter((c) => c.ratio + 0.01 < c.required),
        };
      });
      if (contrast.failures.length)
        throw new Error(
          `Contrast failures ${theme}: ${JSON.stringify(contrast.failures)}`,
        );
      await page.screenshot({
        path: `${output}/${theme}-${width}.png`,
        fullPage: true,
      });
      await page.evaluate(() => {
        document.documentElement.style.zoom = "2";
        window.dispatchEvent(new Event("resize"));
      });
      await page.evaluate(
        () =>
          new Promise((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(resolve)),
          ),
      );
      if (
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth + 1,
        )
      )
        throw new Error(
          `Zoom overflow at ${width}/${theme}: ${JSON.stringify(
            await page.evaluate(() => ({
              scroll: document.documentElement.scrollWidth,
              viewport: innerWidth,
              elements: [...document.querySelectorAll("main *")]
                .filter(
                  (el) => el.getBoundingClientRect().right > innerWidth + 1,
                )
                .slice(0, 8)
                .map((el) => ({
                  tag: el.tagName,
                  cls: el.className,
                  right: el.getBoundingClientRect().right,
                })),
            })),
          )}`,
        );
      report.push({
        theme,
        width,
        zoom: "200%",
        calculator: true,
        checklist: true,
        form: true,
        chart: true,
        svg: true,
        flashcard: true,
        timer: true,
        canvas: isolated,
        mediaFallback: true,
        state: true,
        keyboard: true,
        contrast,
        overflow: false,
      });
      await page.close();
    }
  }
  for (const theme of ["light", "dark"]) {
    for (const width of [1440, 768, 320]) {
      for (const mode of ["chat", "welcome"]) {
        const page = await browser.newPage({
          viewport: { width, height: 800 },
          colorScheme: theme,
        });
        await page.goto(
          `http://127.0.0.1:${server.httpServer.address().port}/.nova-ui-acceptance.html?theme=${theme}&mode=${mode}`,
        );
        if (mode === "chat")
          await page.getByText("Total: 100", { exact: true }).waitFor();
        else
          await page
            .getByText("What would you like to work on?", { exact: true })
            .waitFor();
        const message = page.getByRole("textbox", { name: "Message Nova" });
        await message.fill("Review these assumptions");
        await page
          .getByRole("button", { name: "Nova acceptance", exact: true })
          .click();
        await page.getByRole("menu").waitFor();
        await page
          .getByRole("menuitem", { name: "Nova acceptance", exact: true })
          .focus();
        await page.keyboard.press("Escape");
        await page.getByRole("menu").waitFor({ state: "hidden" });
        if (mode === "chat") {
          await page.getByRole("spinbutton", { name: "Units" }).fill("7");
        }
        await page.evaluate(() => {
          for (const el of document.querySelectorAll("main div"))
            if (getComputedStyle(el).overflowY === "auto")
              el.scrollTop = el.scrollHeight;
        });
        const layout = await page.evaluate(() => {
          const composer = document
            .querySelector('textarea[aria-label="Message Nova"]')
            .getBoundingClientRect();
          const header = document
            .querySelector("header")
            .getBoundingClientRect();
          return {
            composer: composer.top >= 0 && composer.bottom <= innerHeight,
            header: header.top >= 0 && header.bottom <= innerHeight,
            documentScroll:
              document.documentElement.scrollHeight > innerHeight + 1,
            overflow: document.documentElement.scrollWidth > innerWidth + 1,
          };
        });
        if (
          !layout.composer ||
          !layout.header ||
          layout.documentScroll ||
          layout.overflow
        )
          throw new Error(
            `Studio layout ${width}/${theme}/${mode}: ${JSON.stringify(layout)}`,
          );
        await page.screenshot({
          path: `${output}/${theme}-${width}-${mode}.png`,
          fullPage: true,
        });
        if (width === 320 && mode === "chat") {
          await page.setViewportSize({ width, height: 320 });
          await message.focus();
          await page.getByRole("spinbutton", { name: "Units" }).focus();
          const focusedInput = await page
            .getByRole("spinbutton", { name: "Units" })
            .boundingBox();
          const composer = await page
            .getByRole("textbox", { name: "Message Nova" })
            .boundingBox();
          if (
            !focusedInput ||
            !composer ||
            focusedInput.y < 0 ||
            focusedInput.y + focusedInput.height > composer.y
          )
            throw new Error(`Focused mobile input is covered in ${theme}`);
          await page.screenshot({
            path: `${output}/${theme}-320-keyboard.png`,
            fullPage: true,
          });
          await page.setViewportSize({ width, height: 800 });
        }
        const zoomPage = await browser.newPage({
          viewport: { width: Math.floor(width / 2), height: 400 },
          deviceScaleFactor: 2,
          colorScheme: theme,
        });
        await zoomPage.goto(page.url());
        await zoomPage
          .getByRole("textbox", { name: "Message Nova" })
          .fill("Review these assumptions");
        if (mode === "chat")
          await zoomPage.getByRole("spinbutton", { name: "Units" }).waitFor();
        const zoomLayout = await zoomPage.evaluate(() => {
          const composer = document
            .querySelector('textarea[aria-label="Message Nova"]')
            .getBoundingClientRect();
          return {
            composer: composer.top >= 0 && composer.bottom <= innerHeight + 1,
            overflow: document.documentElement.scrollWidth > innerWidth + 1,
            transcriptHeight:
              [...document.querySelectorAll("main div")].find(
                (el) => getComputedStyle(el).overflowY === "auto",
              )?.clientHeight ?? 0,
          };
        });
        if (
          !zoomLayout.composer ||
          zoomLayout.overflow ||
          zoomLayout.transcriptHeight < 44
        )
          throw new Error(
            `Studio zoom ${width}/${theme}/${mode}: ${JSON.stringify(zoomLayout)}`,
          );
        await zoomPage.screenshot({
          path: `${output}/${theme}-${width}-${mode}-zoom.png`,
          fullPage: true,
        });
        await zoomPage.close();
        report.push({
          theme,
          width,
          mode,
          layout,
          composer: true,
          agentMenu: true,
          keyboardEscape: true,
          mobileKeyboard:
            width === 320 && mode === "chat" ? "reduced viewport" : undefined,
          zoomLayout,
        });
        await page.close();
      }
    }
  }
  writeFileSync(`${output}/report.json`, JSON.stringify(report, null, 2));
  console.log(
    `PASS: ${report.length} browser cases across both themes and three viewports, 200% zoom, Studio scroll/composer, real canvas click and isolation. Evidence: ${output}`,
  );
} finally {
  await browser.close();
  await new Promise((resolve, reject) =>
    server.httpServer.close((error) => (error ? reject(error) : resolve())),
  );
}
