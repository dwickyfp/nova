import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { StudioSettings } from "@/features/agents/api";

/**
 * Studio Settings — Account, Role & Warehouse, and Preferences.
 *
 * Role and warehouse are what a query actually runs as, so they sit first after
 * identity. Both are chosen from what the engine reports for this user; the
 * backend re-checks privileges when the turn runs, so a stale selection fails
 * with the engine's own message rather than silently escalating.
 */
export function StudioSettingsDialog({
  open,
  onOpenChange,
  settings,
  onSave,
  saving,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  settings: StudioSettings | undefined;
  onSave: (patch: Partial<StudioSettings["preferences"]>) => void;
  saving: boolean;
}) {
  const [theme, setTheme] = useState<"light" | "dark" | "system">("system");
  const [preferredName, setPreferredName] = useState("");
  const [role, setRole] = useState<string | null>(null);
  const [warehouse, setWarehouse] = useState<string | null>(null);
  const [extendedThinking, setExtendedThinking] = useState(true);

  useEffect(() => {
    if (!settings) return;
    setTheme(settings.preferences.theme);
    setPreferredName(settings.preferences.preferred_name ?? "");
    setRole(settings.preferences.role);
    setWarehouse(settings.preferences.warehouse);
    setExtendedThinking(settings.preferences.extended_thinking);
  }, [settings]);

  const identity = settings?.identity;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
        </DialogHeader>

        <div className="space-y-6">
          <section className="space-y-3">
            <h3 className="text-sm font-medium">Account</h3>
            <div className="space-y-2">
              <Label>Username</Label>
              <Input value={identity?.username ?? ""} disabled />
            </div>
            <div className="space-y-2">
              <Label htmlFor="studio-name">Preferred name</Label>
              <Input
                id="studio-name"
                value={preferredName}
                onChange={(e) => setPreferredName(e.target.value)}
                placeholder="How Studio greets you"
              />
            </div>
            <div className="space-y-2">
              <Label>Theme</Label>
              <Select
                value={theme}
                onValueChange={(v) => setTheme(v as typeof theme)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="system">System</SelectItem>
                  <SelectItem value="light">Light</SelectItem>
                  <SelectItem value="dark">Dark</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </section>

          <section className="space-y-3">
            <h3 className="text-sm font-medium">Role and warehouse</h3>
            <div className="space-y-2">
              <Label>Role</Label>
              <Select
                value={role ?? "__default__"}
                onValueChange={(v) => setRole(v === "__default__" ? null : v)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Default role" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__default__">Default role</SelectItem>
                  {identity?.roles.map((r) => (
                    <SelectItem key={r} value={r}>
                      {r}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Queries run as this role. StarRocks still enforces privileges.
              </p>
            </div>
            <div className="space-y-2">
              <Label>Warehouse</Label>
              <Select
                value={warehouse ?? "__default__"}
                onValueChange={(v) =>
                  setWarehouse(v === "__default__" ? null : v)
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="Default warehouse" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__default__">Default warehouse</SelectItem>
                  {identity?.warehouses.map((w) => (
                    <SelectItem key={w} value={w}>
                      {w}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {identity && identity.warehouses.length === 0 ? (
                <p className="text-xs text-warning-strong">
                  No warehouses are visible to your role.
                </p>
              ) : null}
            </div>
          </section>

          <section className="space-y-3">
            <h3 className="text-sm font-medium">Preferences</h3>
            <label className="flex items-center justify-between gap-4 rounded-md border p-3">
              <span>
                <span className="block text-sm">Extended thinking</span>
                <span className="block text-xs text-muted-foreground">
                  Show the plan and reasoning steps in a chat.
                </span>
              </span>
              <Switch
                checked={extendedThinking}
                onCheckedChange={setExtendedThinking}
                aria-label="Extended thinking"
              />
            </label>
          </section>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={saving}
            onClick={() =>
              onSave({
                theme,
                preferred_name: preferredName || null,
                role,
                warehouse,
                extended_thinking: extendedThinking,
              })
            }
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
