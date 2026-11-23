import { useRef, useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { page, userEvent } from "vitest/browser";
import "@/styles/index.css";
import { Composer } from "./studio-composer";
import { matchCommands, commandQuery, type StudioCommand } from "./studio-commands";

function Harness({ onKeyDown = () => {} }: { onKeyDown?: (e: React.KeyboardEvent) => void }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [value, setValue] = useState("");
  return (
    <div style={{ display: "flex", flexDirection: "column", height: 640 }}>
      <button type="button">Elsewhere</button>
      <div data-testid="welcome" style={{ marginTop: "auto" }}>
        <h1>Good evening, alice</h1>
        <p>What would you like to work on?</p>
      </div>
      <Composer
        ref={ref}
        value={value}
        onChange={setValue}
        onKeyDown={onKeyDown}
        onSend={() => {}}
        onStop={() => {}}
        readingFiles={false}
        attachments={[]}
        onAddFiles={() => {}}
        onRemoveAttachment={() => {}}
        streaming={false}
        disabled={false}
        agent={null}
        agents={[]}
        onSelectAgent={() => {}}
        agentPicker={false}
      />
    </div>
  );
}

const box = () => page.getByRole("textbox", { name: "Message Nova" });
const rect = (el: Element) => {
  const { x, y, width, height } = el.getBoundingClientRect();
  return { x, y, width, height };
};

describe("command filtering", () => {
  const commands: StudioCommand[] = [
    { name: "/side", description: "Side question", group: "Commands" },
    { name: "/create-skill-with-chat", description: "Skill", group: "Commands" },
    { name: "/fork", description: "Fork", group: "Commands" },
  ];

  it("only reads a single slash word", () => {
    expect(commandQuery("/cre")).toBe("cre");
    expect(commandQuery("/")).toBe("");
    expect(commandQuery("/create-skill-with-chat ")).toBeNull();
    expect(commandQuery("hello /x")).toBeNull();
  });

  it("puts prefix matches before substring matches", () => {
    expect(matchCommands("", commands).map((c) => c.name)).toEqual(["/side", "/create-skill-with-chat", "/fork"]);
    expect(matchCommands("s", commands).map((c) => c.name)).toEqual(["/side", "/create-skill-with-chat"]);
    expect(matchCommands("FO", commands).map((c) => c.name)).toEqual(["/fork"]);
    expect(matchCommands("zzz", commands)).toEqual([]);
  });
});

describe("the slash-command popover", () => {
  it("floats above the composer without moving the page", async () => {
    const screen = await render(<Harness />);
    const welcome = screen.getByTestId("welcome").element();
    const composer = box().element();
    const before = { welcome: rect(welcome), composer: rect(composer) };

    await userEvent.fill(box(), "/");
    const option = page.getByRole("option", { name: /^\/create-skill-with-chat/ });
    await expect.element(option).toBeVisible();
    expect(rect(welcome)).toEqual(before.welcome);
    expect(rect(composer)).toEqual(before.composer);

    const menu = page.getByTestId("studio-command-menu").element();
    expect(menu.getBoundingClientRect().bottom).toBeLessThanOrEqual(composer.getBoundingClientRect().top);
    await expect.element(page.getByText("Commands", { exact: true })).toBeVisible();
    await expect.element(page.getByText("<task>")).toBeVisible();

    await userEvent.fill(box(), "/cre");
    await expect.element(option).toBeVisible();
    expect(rect(welcome)).toEqual(before.welcome);

    await userEvent.keyboard("{Escape}");
    await expect.element(option).not.toBeInTheDocument();
    expect(rect(welcome)).toEqual(before.welcome);
    expect(rect(composer)).toEqual(before.composer);
  });

  it("filters as you type and closes when nothing matches", async () => {
    await render(<Harness />);
    await userEvent.fill(box(), "/skill");
    await expect.element(page.getByRole("option", { name: /^\/create-skill-with-chat/ })).toBeVisible();
    await userEvent.fill(box(), "/nothing-like-this");
    await expect.element(page.getByRole("listbox")).not.toBeInTheDocument();
    await userEvent.fill(box(), "/create-skill-with-chat question");
    await expect.element(page.getByRole("listbox")).not.toBeInTheDocument();
  });

  it("is driven from the keyboard and keeps focus in the message box", async () => {
    const onKeyDown = vi.fn();
    await render(<Harness onKeyDown={onKeyDown} />);
    await userEvent.fill(box(), "/");
    await expect.element(page.getByRole("listbox", { name: "Commands" })).toBeVisible();

    const textarea = box().element();
    const option = page.getByRole("option", { name: /^\/create-skill-with-chat/ }).element();
    expect(textarea.getAttribute("aria-controls")).toBe(page.getByRole("listbox").element().id);
    expect(textarea.getAttribute("aria-activedescendant")).toBe(option.id);
    expect(option.getAttribute("aria-selected")).toBe("true");

    await userEvent.keyboard("{ArrowDown}");
    await userEvent.keyboard("{ArrowUp}");
    expect(textarea.getAttribute("aria-activedescendant")).toBe(option.id);

    await userEvent.keyboard("{Enter}");
    await expect.element(box()).toHaveValue("/create-skill-with-chat ");
    await expect.element(page.getByRole("listbox")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(textarea);
    // Choosing a command does not send the message.
    expect(onKeyDown).not.toHaveBeenCalled();

    await userEvent.fill(box(), "/cr");
    await userEvent.keyboard("{Tab}");
    await expect.element(box()).toHaveValue("/create-skill-with-chat ");

    // With the list dismissed, Enter goes back to the composer's own handler.
    await userEvent.fill(box(), "/c");
    await userEvent.keyboard("{Escape}");
    await userEvent.keyboard("{Enter}");
    expect(onKeyDown).toHaveBeenCalled();
  });

  it("selects with the mouse and closes on blur", async () => {
    await render(<Harness />);
    await userEvent.fill(box(), "/");
    await userEvent.click(page.getByRole("option", { name: /^\/create-skill-with-chat/ }));
    await expect.element(box()).toHaveValue("/create-skill-with-chat ");
    expect(document.activeElement).toBe(box().element());

    await userEvent.fill(box(), "/");
    await expect.element(page.getByRole("listbox")).toBeVisible();
    await userEvent.click(page.getByRole("button", { name: "Elsewhere" }));
    await expect.element(page.getByRole("listbox")).not.toBeInTheDocument();
  });
});
