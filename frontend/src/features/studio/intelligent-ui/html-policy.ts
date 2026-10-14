import { parse } from "acorn";

const forbidden = new Set([
  "window",
  "globalThis",
  "self",
  "parent",
  "top",
  "opener",
  "frames",
  "location",
  "navigator",
  "fetch",
  "XMLHttpRequest",
  "WebSocket",
  "EventSource",
  "Worker",
  "SharedWorker",
  "importScripts",
  "localStorage",
  "sessionStorage",
  "indexedDB",
  "caches",
  "cookie",
  "domain",
  "defaultView",
  "ownerDocument",
  "constructor",
  "prototype",
  "__proto__",
  "getPrototypeOf",
  "setPrototypeOf",
  "getOwnPropertyDescriptor",
  "getOwnPropertyDescriptors",
  "getOwnPropertyNames",
  "getOwnPropertySymbols",
  "defineProperty",
  "defineProperties",
  "assign",
  "eval",
  "Function",
  "Reflect",
  "Proxy",
  "Object",
  "Image",
  "Audio",
  "open",
  "write",
  "writeln",
  "createElement",
  "createElementNS",
  "createAttribute",
  "createAttributeNS",
  "setNamedItem",
  "setNamedItemNS",
  "innerHTML",
  "outerHTML",
  "insertAdjacentHTML",
  "setAttribute",
  "setAttributeNS",
  "href",
  "src",
  "srcset",
  "action",
  "httpEquiv",
  "contentWindow",
  "contentDocument",
  "getRootNode",
  "parentNode",
  "parentElement",
  "composedPath",
  "path",
  "sendBeacon",
  "serviceWorker",
  "postMessage",
  "view",
]);

export function validateOfflineHtml(html: string) {
  const scriptPattern = /<script\b[^>]*>([\s\S]*?)<\/script\s*>/gi;
  const markup = html.replace(scriptPattern, "");
  if (
    /<script\b|<\s*(?:iframe|object|embed|base|meta|link|animate|set\b)|\b(?:src|srcset|href|on\w+)\s*=|@import|url\s*\(/i.test(
      markup,
    )
  )
    throw new Error("HTML must be self-contained and isolated.");
  if (/<script\b[^>]*\bsrc\s*=/i.test(html))
    throw new Error("External scripts are unavailable.");
  for (const match of html.matchAll(scriptPattern)) {
    const tree = parse(match[1], { ecmaVersion: 2022, sourceType: "script" });
    let nodes = 0;
    const walk = (node: unknown, depth = 0): void => {
      if (depth > 60 || ++nodes > 5000)
        throw new Error("HTML script exceeds its limit.");
      if (Array.isArray(node)) {
        node.forEach((x) => walk(x, depth + 1));
        return;
      }
      if (!node || typeof node !== "object") return;
      const value = node as Record<string, unknown>;
      if (
        [
          "ThisExpression",
          "ImportExpression",
          "ImportDeclaration",
          "WithStatement",
          "MetaProperty",
        ].includes(String(value.type))
      )
        throw new Error("HTML script uses an unavailable runtime capability.");
      if (
        (value.type === "Identifier" && forbidden.has(String(value.name))) ||
        (value.type === "Literal" &&
          typeof value.value === "string" &&
          forbidden.has(value.value))
      )
        throw new Error("HTML script uses an unavailable runtime capability.");
      if (value.type === "MemberExpression" && value.computed) {
        const property = value.property as Record<string, unknown>;
        if (property.type !== "Literal" || typeof property.value !== "number")
          throw new Error(
            "HTML scripts use named properties or literal array indices.",
          );
      }
      if (value.type === "Property" && value.computed)
        throw new Error(
          "HTML scripts cannot construct dynamic property names.",
        );
      Object.values(value).forEach((x) => {
        if (x && typeof x === "object") walk(x, depth + 1);
      });
    };
    walk(tree);
  }
}
