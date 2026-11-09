import { afterEach, expect, it } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { CitationList } from "./result-cards";
import "@/styles/index.css";

afterEach(async () => {
  await cleanup();
});

it("links web sources and labels them apart from governed citations", async () => {
  const screen = await render(
    <CitationList
      citations={[
        { source: "web", title: "Rupiah closes higher", url: "https://news.example.com/rupiah", snippet: "news.example.com" },
        { source: "web", title: "Unsafe page", url: "javascript:alert(1)" },
        { source: "orders.notes", title: "Order note", snippet: "Delivered late" },
      ]}
    />,
  );
  const link = screen.getByRole("link", { name: "Rupiah closes higher" });
  await expect.element(link).toHaveAttribute("href", "https://news.example.com/rupiah");
  await expect.element(link).toHaveAttribute("target", "_blank");
  await expect.element(link).toHaveAttribute("rel", "noopener noreferrer nofollow");
  // A non-http(s) URL is shown as text, never as a link.
  await expect.element(screen.getByRole("link", { name: "Unsafe page" })).not.toBeInTheDocument();
  await expect.element(screen.getByText("Unsafe page")).toBeVisible();
  await expect.element(screen.getByText("Web source").first()).toBeVisible();
  expect(screen.getByText("Web source").elements()).toHaveLength(2);
  await expect.element(screen.getByText("Order note")).toBeVisible();
});
