/** The server accepts thread titles up to 256 characters. */
const LIMIT = 120;

/**
 * A history title from the first message. The whole message used to be sent,
 * so a long first question was refused and the conversation never started.
 */
export function threadTitle(text: string): string {
  const line = text.split(/\s+/).filter(Boolean).join(" ");
  return line.length <= LIMIT ? line : `${line.slice(0, LIMIT - 1).trimEnd()}…`;
}
