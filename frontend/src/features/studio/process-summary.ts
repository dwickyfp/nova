import type { RailStep } from "./thought-turn";

/**
 * The summary line. It names the work the group contains, in the order it
 * happened, and caps at three verbs so a long run does not produce an essay.
 * A live group says what it is doing now instead of summarising.
 */
export function processSummary(steps: RailStep[], running: boolean): string {
  const live = [...steps].reverse().find((step) => step.status === "running" || step.status === "pending");
  if (running && live) {
    return live.text;
  }
  const verbs: string[] = [];
  for (const step of steps) {
    const verb =
      step.kind === "tool"
        ? `ran ${step.label}`
        : step.kind === "delegate"
          ? "worked with a specialist"
        : step.kind === "consent"
          ? "asked for approval"
          : step.kind === "note"
            ? step.label === "plan"
              ? "planned the run"
              : step.label === "queued" || step.label === "started"
                ? "started the run"
                : step.label === "waiting"
                  ? "waited for specialists"
                  : step.label === "completed"
                    ? "finished the answer"
                    : "noted a context change"
            : step.label === "plan"
              ? "planned the run"
              : step.label === "skill"
                ? "loaded a skill"
                : step.label === "observe"
                  ? "read the result"
                  : step.label === "answer"
                    ? "wrote the answer"
                    : "reasoned";
    if (!verbs.includes(verb)) verbs.push(verb);
  }
  if (!verbs.length) return "Worked through the request";
  if (verbs.length === 1) return capitalise(verbs[0]);
  const head = verbs.slice(0, 3).map(capitalise).join(", ");
  return verbs.length > 3 ? `${head}, and more` : head;
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
