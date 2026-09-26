import { useEffect, useRef, useState } from "react";
import { Copy, Check } from "lucide-react";
import { toast } from "sonner";
import { useAuthStore } from "@/stores/auth-store";
import { api } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type ProxyInfo = {
  enabled: boolean;
  host: string;
  port: number;
};

type ConnectionInfoDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function ConnectionInfoDialog({
  open,
  onOpenChange,
}: ConnectionInfoDialogProps) {
  const user = useAuthStore((state) => state.auth.user);
  const [info, setInfo] = useState<ProxyInfo | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const titleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setError(false);
    setInfo(null);
    api
      .get<{ proxy: ProxyInfo }>("/system/info")
      .then((res) => {
        if (!cancelled) setInfo(res.proxy);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [open, attempt]);

  const example =
    info?.enabled && info.host
      ? `mysql -h '${info.host.replace(/'/g, "'\\''")}' -P ${info.port} -u <your_username> -p`
      : "";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="flex max-h-[calc(100dvh-2rem)] flex-col gap-0 overflow-hidden rounded-xl p-0 shadow-xl sm:max-w-3xl"
        showCloseButton={false}
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          titleRef.current?.focus();
        }}
      >
        <DialogHeader className="shrink-0 gap-1.5 px-5 pt-7 pb-5 text-center sm:px-8 sm:text-center">
          <DialogTitle
            ref={titleRef}
            tabIndex={-1}
            className="font-semibold outline-none"
          >
            Account details
          </DialogTitle>
          <DialogDescription>
            Your Nova account and connection settings.
          </DialogDescription>
        </DialogHeader>
        <Tabs
          defaultValue="account"
          className="min-h-0 flex-1 gap-0 overflow-hidden"
        >
          <div className="shrink-0 px-5 sm:px-8">
            <TabsList
              aria-label="Account information"
              indicatorVariant="underline"
              className="h-auto w-full justify-start gap-4 overflow-x-auto rounded-none border-b bg-transparent p-0 sm:gap-7"
            >
              {[
                ["account", "Account"],
                ["connection", "Connection"],
                ["commands", "SQL commands"],
              ].map(([value, label]) => (
                <TabsTrigger
                  key={value}
                  value={value}
                  className="h-auto flex-none rounded-none border-0 px-1 py-3 text-xs text-muted-foreground hover:text-foreground focus-visible:ring-0 focus-visible:outline-2 focus-visible:-outline-offset-1 data-[state=active]:text-accent-foreground sm:text-sm dark:data-[state=active]:text-accent-foreground"
                >
                  {label}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pt-3 pb-6 sm:px-8">
            <TabsContent value="account" className="m-0">
              <DetailsTable
                rows={[
                  { label: "Login name", value: user?.username ?? "" },
                  { label: "Active role", value: user?.activeRole ?? "" },
                  {
                    label: "Available roles",
                    value: user?.roles.join(", ") ?? "",
                  },
                  { label: "Console URL", value: window.location.origin },
                  { label: "SQL engine", value: "StarRocks" },
                  { label: "Client protocol", value: "MySQL" },
                ]}
              />
            </TabsContent>
            <TabsContent value="connection" className="m-0">
              {error ? (
                <div role="alert" className="space-y-3 py-8 text-sm">
                  <p>Connection details could not be loaded.</p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setAttempt((value) => value + 1)}
                  >
                    Try again
                  </Button>
                </div>
              ) : !info ? (
                <p role="status" className="py-8 text-sm text-muted-foreground">
                  Loading connection details…
                </p>
              ) : (
                <>
                  <DetailsTable
                    rows={[
                      {
                        label: "SQL connection",
                        value: info.enabled ? "Enabled" : "Disabled",
                      },
                      { label: "Host", value: info.host, mono: true },
                      { label: "Port", value: String(info.port), mono: true },
                      { label: "Username", value: user?.username ?? "" },
                      {
                        label: "Authentication",
                        value: "Your StarRocks account",
                      },
                    ]}
                  />
                  <p className="mt-5 text-xs leading-relaxed text-muted-foreground">
                    {info.enabled
                      ? "Connect with any MySQL-compatible client using your Nova username and password."
                      : "SQL client connections are disabled for this Nova instance."}
                  </p>
                </>
              )}
            </TabsContent>
            <TabsContent value="commands" className="m-0 space-y-6 py-3">
              <CommandBlock
                title="Connect from your terminal"
                description="Replace <your_username> with your login name. You will be prompted for your password."
                value={example}
                unavailable={
                  error
                    ? "Connection details are unavailable. Try again in the Connection tab."
                    : !info
                      ? "Loading connection details…"
                      : "SQL client connections are disabled."
                }
              />
              <CommandBlock
                title="Query a stage file"
                description="Replace the stage and file names with a file you have access to."
                value="SELECT * FROM @stage1.data.csv;"
              />
            </TabsContent>
          </div>
        </Tabs>
        <div className="flex shrink-0 items-center justify-between gap-3 border-t bg-muted/30 px-5 py-4 sm:px-8">
          <span className="text-xs text-muted-foreground">
            Nova · Account information
          </span>
          <DialogClose asChild>
            <Button variant="outline" size="sm" className="px-5">
              Close
            </Button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function DetailsTable({
  rows,
}: {
  rows: { label: string; value: string; mono?: boolean }[];
}) {
  return (
    <table className="block w-full table-fixed text-left text-sm sm:table">
      <thead className="hidden text-xs text-muted-foreground sm:table-header-group">
        <tr className="border-b">
          <th
            scope="col"
            className="w-2/5 px-2 py-3 font-medium sm:w-1/3 sm:px-3"
          >
            Name
          </th>
          <th scope="col" className="px-2 py-3 font-medium sm:px-3">
            Value
          </th>
          <th scope="col" className="w-10">
            <span className="sr-only">Copy value</span>
          </th>
        </tr>
      </thead>
      <tbody className="block divide-y sm:table-row-group">
        {rows.map(({ label, value, mono }) => (
          <tr
            key={label}
            className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-2 gap-y-1 py-3 transition-colors hover:bg-muted/40 sm:table-row sm:py-0"
          >
            <th
              scope="row"
              className="px-2 align-top text-xs font-normal text-muted-foreground sm:px-3 sm:py-3.5 sm:text-sm"
            >
              {label}
            </th>
            <td
              className={cn(
                "px-2 align-top [overflow-wrap:anywhere] sm:px-3 sm:py-3.5",
                mono && "font-mono text-xs",
              )}
            >
              {value || (
                <span className="text-muted-foreground">Not available</span>
              )}
            </td>
            <td className="col-start-2 row-span-2 row-start-1 align-top sm:py-1.5">
              <CopyButton label={label} value={value} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CommandBlock({
  title,
  description,
  value,
  unavailable,
}: {
  title: string;
  description: string;
  value: string;
  unavailable?: string;
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="text-xs leading-relaxed text-muted-foreground">
        {description}
      </p>
      {value ? (
        <div className="flex items-start gap-3 rounded-md border bg-muted/30 p-3">
          <code className="min-w-0 flex-1 self-center font-mono text-xs leading-relaxed [overflow-wrap:anywhere]">
            {value}
          </code>
          <CopyButton label={title} value={value} />
        </div>
      ) : (
        <p role="status" className="py-3 text-sm text-muted-foreground">
          {unavailable}
        </p>
      )}
    </section>
  );
}

function CopyButton({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Could not copy. Select and copy the value manually.");
    }
  }

  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      className="text-muted-foreground hover:text-foreground"
      onClick={() => void handleCopy()}
      disabled={!value}
      aria-label={copied ? `Copied ${label}` : `Copy ${label}`}
      title={copied ? "Copied" : `Copy ${label}`}
    >
      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
    </Button>
  );
}
