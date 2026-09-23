import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StudioCapabilities } from "./studio-capabilities";
import { SkillEditor } from "./skill-editor";
import { SkillUpload } from "./skill-upload";
import { extractSkillDraft } from "./skill-document";
import { MAX_SKILL_BYTES } from "./skill-document";
import "@/styles/index.css";

const mocks = vi.hoisted(() => ({
  personal: vi.fn(),
  upload: vi.fn(),
  verify: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
  capabilities: vi.fn(),
}));
vi.mock("@/features/agents/api", () => ({
  skillsApi: mocks,
  studioApi: mocks,
}));

const documentText =
  "---\nname: weekly-review\ndescription: Review weekly reports\n---\nAsk for a report. Summarize changes.";
const skill = {
  skill_id: "s1",
  name: "weekly-review",
  description: "Review weekly reports",
  body: documentText,
  source: "user",
};

function wrap(children: React.ReactNode) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <div className="flex min-h-dvh flex-col">{children}</div>
    </QueryClientProvider>
  );
}

describe("Studio personal capabilities", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mocks.personal.mockResolvedValue({ skills: [], count: 0 });
    mocks.capabilities.mockResolvedValue({
      connectors: [
        {
          server_id: "c1",
          name: "Company docs",
          description: "Internal documentation",
          is_active: true,
        },
      ],
    });
    mocks.upload.mockResolvedValue(skill);
    mocks.verify.mockResolvedValue({
      name: skill.name,
      description: skill.description,
    });
    mocks.update.mockResolvedValue(skill);
    mocks.remove.mockResolvedValue(undefined);
  });
  afterEach(() => {
    document.documentElement.classList.remove("dark");
  });

  it("opens the create menu and dispatches chat creation", async () => {
    const create = vi.fn();
    const screen = await render(
      wrap(<StudioCapabilities onCreateWithChat={create} />),
    );
    await expect
      .element(screen.getByText("Get started with skills"))
      .toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Create", exact: true }),
    );
    await userEvent.click(
      page.getByRole("menuitem", { name: "Create with chat" }),
    );
    expect(create).toHaveBeenCalledOnce();
    await userEvent.click(
      screen.getByRole("button", { name: /Create a skill/ }),
    );
    await expect
      .element(page.getByRole("menuitem", { name: "Upload a skill" }))
      .toBeVisible();
    await userEvent.keyboard("{Escape}");
    await userEvent.click(
      screen.getByRole("button", { name: "MCP Connectors" }),
    );
    await expect.element(screen.getByText("Company docs")).toBeVisible();
    await expect
      .element(screen.getByRole("button", { name: "Create", exact: true }))
      .not.toBeInTheDocument();
    await userEvent.fill(
      screen.getByRole("textbox", { name: "Search connectors" }),
      "unmatched",
    );
    await expect
      .element(screen.getByText("No connectors match your search"))
      .toBeVisible();
  });

  it("filters user skills, opens and saves an edit, and confirms deletion", async () => {
    mocks.personal.mockResolvedValue({
      skills: [
        skill,
        {
          ...skill,
          skill_id: "builtin:x",
          name: "platform-secret",
          source: "builtin",
        },
      ],
    });
    const screen = await render(
      wrap(<StudioCapabilities onCreateWithChat={vi.fn()} />),
    );
    await expect
      .element(screen.getByText("weekly-review", { exact: true }))
      .toBeVisible();
    await expect
      .element(screen.getByText("platform-secret"))
      .not.toBeInTheDocument();
    await userEvent.fill(
      screen.getByRole("textbox", { name: "Search skills" }),
      "missing",
    );
    await expect
      .element(screen.getByText("No skills match your search."))
      .toBeVisible();
    await userEvent.fill(
      screen.getByRole("textbox", { name: "Search skills" }),
      "",
    );
    await userEvent.click(screen.getByText("weekly-review", { exact: true }));
    await userEvent.fill(
      page.getByRole("textbox", { name: "SKILL.md" }),
      documentText + "\nUse concise bullets.",
    );
    await userEvent.click(
      page.getByRole("button", { name: "Save skill", exact: true }),
    );
    await expect.poll(() => mocks.update.mock.calls.length).toBe(1);
    expect(mocks.update).toHaveBeenCalledWith(
      "s1",
      documentText + "\nUse concise bullets.",
    );
    await expect.element(page.getByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Delete weekly-review" }),
    );
    await userEvent.click(
      page.getByRole("button", { name: "Cancel", exact: true }),
    );
    expect(mocks.remove).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "Delete weekly-review" }),
    );
    await userEvent.click(
      page.getByRole("button", { name: "Delete skill", exact: true }),
    );
    await expect.poll(() => mocks.remove.mock.calls.length).toBe(1);
    expect(mocks.remove).toHaveBeenCalledWith("s1");
  });

  it("preserves an invalid draft and lets the user retry", async () => {
    mocks.upload.mockRejectedValueOnce(new Error("Add a description."));
    const close = vi.fn();
    await render(
      wrap(<SkillEditor initialDocument={documentText} onClose={close} />),
    );
    await userEvent.click(
      page.getByRole("button", { name: "Save skill", exact: true }),
    );
    await expect
      .element(page.getByRole("alert"))
      .toHaveTextContent("Add a description.");
    await expect
      .element(page.getByRole("textbox", { name: "SKILL.md" }))
      .toHaveValue(documentText);
    expect(close).not.toHaveBeenCalled();
    await userEvent.click(
      page.getByRole("button", { name: "Save skill", exact: true }),
    );
    await expect.poll(() => close.mock.calls.length).toBe(1);
  });

  it("uploads a real Markdown file for review before saving", async () => {
    const screen = await render(
      wrap(<StudioCapabilities onCreateWithChat={vi.fn()} />),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Create", exact: true }),
    );
    await userEvent.click(
      page.getByRole("menuitem", { name: "Upload a skill" }),
    );
    await expect
      .element(page.getByRole("button", { name: "Verify skill", exact: true }))
      .toBeDisabled();
    await userEvent.upload(
      screen.getByLabelText("Upload SKILL.md"),
      new File([documentText], "SKILL.md", { type: "text/markdown" }),
    );
    await expect
      .element(page.getByRole("button", { name: "Verify skill", exact: true }))
      .toBeEnabled();
    await userEvent.click(
      page.getByRole("button", { name: "Verify skill", exact: true }),
    );
    await expect
      .element(page.getByText("Verification complete", { exact: true }))
      .toBeVisible();
    expect(mocks.verify).toHaveBeenCalledWith(documentText);
    expect(mocks.upload).not.toHaveBeenCalled();
    await userEvent.click(
      page.getByRole("button", { name: "Complete", exact: true }),
    );
    await expect.poll(() => mocks.upload.mock.calls.length).toBe(1);
    expect(mocks.upload).toHaveBeenCalledWith(documentText);
  });

  it("keeps failed verification retryable and resets verification when going back", async () => {
    const close = vi.fn();
    await render(wrap(<SkillUpload onClose={close} />));
    await userEvent.upload(
      page.getByLabelText("Upload SKILL.md"),
      new File(["bad"], "SKILL.md"),
    );
    mocks.verify.mockRejectedValueOnce(new Error("Add YAML frontmatter."));
    await userEvent.click(
      page.getByRole("button", { name: "Verify skill", exact: true }),
    );
    await expect
      .element(page.getByRole("alert"))
      .toHaveTextContent("Add YAML frontmatter.");
    expect(mocks.upload).not.toHaveBeenCalled();
    await userEvent.upload(
      page.getByLabelText("Upload SKILL.md"),
      new File([documentText], "SKILL.md"),
    );
    await userEvent.click(
      page.getByRole("button", { name: "Verify skill", exact: true }),
    );
    await expect
      .element(page.getByText("Verification complete", { exact: true }))
      .toBeVisible();
    await userEvent.click(page.getByText("Review SKILL.md", { exact: true }));
    await expect
      .element(page.getByText(documentText, { exact: true }))
      .toBeVisible();
    await userEvent.click(
      page.getByRole("button", { name: "Back", exact: true }),
    );
    await expect
      .element(page.getByRole("button", { name: "Verify skill", exact: true }))
      .toBeVisible();
    await userEvent.click(
      page.getByRole("button", { name: "Cancel", exact: true }),
    );
    expect(close).toHaveBeenCalledOnce();
  });

  it("accepts 25 MB files and rejects files above the limit", async () => {
    await render(wrap(<SkillUpload onClose={vi.fn()} />));
    const contents = new Uint8Array(MAX_SKILL_BYTES + 1).fill(120);
    await userEvent.upload(
      page.getByLabelText("Upload SKILL.md"),
      new File([contents], "SKILL.md"),
    );
    await expect
      .element(page.getByRole("alert"))
      .toHaveTextContent("25 MB or smaller");
    await expect
      .element(page.getByRole("button", { name: "Verify skill", exact: true }))
      .toBeDisabled();
    await userEvent.upload(
      page.getByLabelText("Upload SKILL.md"),
      new File([contents.subarray(0, MAX_SKILL_BYTES)], "SKILL.md"),
    );
    await expect
      .element(page.getByRole("button", { name: "Verify skill", exact: true }))
      .toBeEnabled();
    await userEvent.click(
      page.getByRole("button", { name: "Verify skill", exact: true }),
    );
    await expect.poll(() => mocks.verify.mock.calls.length).toBe(1);
    expect(mocks.verify.mock.calls[0][0].length).toBe(MAX_SKILL_BYTES);
  });

  it("renders the upload modal in both themes and at mobile widths", async () => {
    await render(wrap(<SkillUpload onClose={vi.fn()} />));
    for (const theme of ["light", "dark"]) {
      document.documentElement.classList.toggle("dark", theme === "dark");
      for (const width of [375, 1440]) {
        await page.viewport(width, 900);
        await expect.element(page.getByRole("dialog")).toBeVisible();
        await expect
          .poll(
            () =>
              getComputedStyle(document.querySelector('[role="dialog"]')!)
                .opacity,
          )
          .toBe("1");
        expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
        await page.screenshot({
          path: `__screenshots__/nova-skill-upload-${theme}-${width}.png`,
        });
      }
    }
  });

  it("shows a loading state, then an error with a working retry", async () => {
    let reject!: (error: Error) => void;
    mocks.personal.mockReturnValueOnce(
      new Promise((_resolve, rejectPromise) => {
        reject = rejectPromise;
      }),
    );
    const screen = await render(
      wrap(<StudioCapabilities onCreateWithChat={vi.fn()} />),
    );
    await expect
      .element(screen.getByRole("status"))
      .toHaveTextContent("Loading your skills");
    reject(new Error("Unavailable"));
    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("Your skills could not be loaded.");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await expect
      .element(screen.getByText("Get started with skills"))
      .toBeVisible();
  });

  it("reflows in both themes across mobile, tablet and desktop widths", async () => {
    const screen = await render(
      wrap(<StudioCapabilities onCreateWithChat={vi.fn()} />),
    );
    await expect
      .element(screen.getByText("Get started with skills"))
      .toBeVisible();
    for (const theme of ["light", "dark"]) {
      document.documentElement.classList.toggle("dark", theme === "dark");
      const tokens = getComputedStyle(document.documentElement);
      for (const [foreground, background] of [
        ["--foreground", "--background"],
        ["--muted-foreground", "--background"],
        ["--primary-foreground", "--primary"],
        ["--foreground", "--card"],
        ["--muted-foreground", "--accent"],
      ]) {
        const ratio = contrast(
          tokens.getPropertyValue(foreground),
          tokens.getPropertyValue(background),
        );
        expect(
          ratio,
          `${theme}: ${foreground} on ${background}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
      for (const width of [375, 768, 1440]) {
        await page.viewport(width, 900);
        await expect
          .element(screen.getByRole("button", { name: "Create", exact: true }))
          .toBeVisible();
        expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
        if (width !== 768)
          await page.screenshot({
            path: `__screenshots__/nova-capabilities-${theme}-${width}.png`,
          });
      }
    }
    await userEvent.click(
      screen.getByRole("button", { name: "Create", exact: true }),
    );
    await userEvent.keyboard("{Escape}");
    await expect.element(page.getByRole("menu")).not.toBeInTheDocument();
  });

  it("extracts only a complete fenced skill draft", () => {
    expect(extractSkillDraft("```skill\n" + documentText + "\n```")).toBe(
      documentText,
    );
    expect(extractSkillDraft("```skill\n" + documentText)).toBeNull();
    expect(extractSkillDraft("Here is some advice.")).toBeNull();
    const nested = documentText + "\n```sql\nSELECT 1;\n```";
    expect(extractSkillDraft("````skill\n" + nested + "\n````")).toBe(nested);
  });
});

function contrast(foreground: string, background: string) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const context = canvas.getContext("2d")!;
  function luminance(color: string) {
    context.clearRect(0, 0, 1, 1);
    context.fillStyle = color;
    context.fillRect(0, 0, 1, 1);
    const channels = Array.from(context.getImageData(0, 0, 1, 1).data)
      .slice(0, 3)
      .map((value) => {
        const channel = value / 255;
        return channel <= 0.04045
          ? channel / 12.92
          : ((channel + 0.055) / 1.055) ** 2.4;
      });
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  }
  const a = luminance(foreground),
    b = luminance(background);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
