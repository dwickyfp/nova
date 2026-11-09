import { useCallback, useEffect, useId, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { StatusBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";

/** What the server shares about the Tavily key: whether it is set, never the key. */
export type WebSearchStatus = {
  provider: "tavily";
  configured: boolean;
  /** The key's public type prefix (``tvly-dev-``), never secret characters. */
  key_hint?: string | null;
};

/** A fixed-length mask: it does not reveal the stored key's length. */
const MASK = "•".repeat(20);

const ADMIN_ROLES = ["ACCOUNTADMIN", "SECURITYADMIN", "user_admin", "security_admin"];

/**
 * Write-only Tavily API key for Studio web search. The key is sent once,
 * stored encrypted, and never read back: the page only learns whether one is set.
 */
export function WebSearchKeyControl() {
  const user = useAuthStore((state) => state.auth.user);
  const canEdit = ADMIN_ROLES.includes(user?.activeRole ?? "");
  const [status, setStatus] = useState<WebSearchStatus | null>(null);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const id = useId();

  const load = useCallback(async () => {
    setError(null);
    try {
      setStatus(await api.get<WebSearchStatus>("/ai/web-search"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load web search settings.");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load, user?.username, user?.activeRole]);

  const run = async (action: () => Promise<WebSearchStatus>, done: string) => {
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      setStatus(await action());
      setValue("");
      setReplacing(false);
      setMessage(done);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to save the Tavily API key.");
    } finally {
      setBusy(false);
    }
  };
  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!value.trim()) return;
    void run(
      () => api.put<WebSearchStatus>("/ai/web-search/api-key", { api_key: value.trim() }),
      "Tavily API key saved.",
    );
  };
  const remove = () =>
    void run(
      () => api.delete<WebSearchStatus>("/ai/web-search/api-key"),
      "Tavily API key removed.",
    );

  const configured = status?.configured ?? false;
  const editing = canEdit && (!configured || replacing);
  return (
    <section aria-labelledby={`${id}-label`} className="space-y-3 rounded-md border p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <Label id={`${id}-label`} htmlFor={editing ? id : `${id}-masked`}>
            Tavily API key
          </Label>
          <p id={`${id}-description`} className="text-sm text-muted-foreground">
            Agents with web search turned on use it to search the public web.
          </p>
        </div>
        {status ? (
          configured ? (
            <StatusBadge tone="success" dot>
              Active
            </StatusBadge>
          ) : (
            <StatusBadge tone="neutral">Not set</StatusBadge>
          )
        ) : null}
      </div>
      {configured && !editing ? (
        <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
          {/* Only the key's public type prefix is known here; the rest is never sent. */}
          <output
            id={`${id}-masked`}
            aria-label="Stored Tavily API key, hidden"
            className="flex h-9 w-full min-w-0 items-center overflow-hidden rounded-md border bg-muted/40 px-3 font-mono text-sm text-muted-foreground sm:max-w-lg"
          >
            <span className="truncate">
              {status?.key_hint ?? ""}
              {MASK}
            </span>
          </output>
          {canEdit ? (
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setReplacing(true);
                  setMessage("");
                }}
              >
                Replace key
              </Button>
              <Button type="button" variant="outline" disabled={busy} onClick={remove}>
                Remove
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
      {editing ? (
        <form
          onSubmit={save}
          className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center"
        >
          <Input
            id={id}
            type="password"
            autoComplete="off"
            spellCheck={false}
            autoFocus={replacing}
            value={value}
            onChange={(event) => {
              setValue(event.target.value);
              setMessage("");
            }}
            placeholder={configured ? "Enter the new key" : "tvly-…"}
            aria-describedby={`${id}-description`}
            disabled={!status || busy}
            className="w-full min-w-0 sm:max-w-lg"
          />
          <div className="flex gap-2">
            <Button type="submit" disabled={!status || busy || !value.trim()}>
              {busy ? "Saving…" : "Save key"}
            </Button>
            {replacing ? (
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setReplacing(false);
                  setValue("");
                }}
              >
                Cancel
              </Button>
            ) : null}
          </div>
        </form>
      ) : null}
      {!canEdit ? (
        <p className="text-sm text-muted-foreground">
          An administrator can set the Tavily API key.
        </p>
      ) : null}
      {error ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-destructive">
          <span>{error}</span>
          {!status ? (
            <Button size="sm" variant="outline" onClick={() => void load()}>
              Retry
            </Button>
          ) : null}
        </div>
      ) : null}
      <p role="status" className="text-sm text-muted-foreground">
        {message}
      </p>
    </section>
  );
}
