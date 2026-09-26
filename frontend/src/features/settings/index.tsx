import { useState, type FormEvent, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { UserRound } from "lucide-react";
import { toast } from "sonner";
import { useAuthStore } from "@/stores/auth-store";
import { api } from "@/lib/api-client";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Header } from "@/components/layout/header";
import { Main } from "@/components/layout/main";

type Profile = {
  username: string;
  first_name: string;
  last_name: string;
  email: string;
};

export function SettingsPage() {
  const username = useAuthStore((state) => state.auth.user?.username);
  const profile = useQuery({
    queryKey: ["account-profile", username],
    queryFn: () => api.get<Profile>("/auth/profile"),
    enabled: !!username,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  return (
    <>
      <Header className="border-b">
        <h1 className="text-base font-medium">Settings</h1>
      </Header>
      <Main fixed fluid className="@container/settings p-0">
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden @3xl/settings:flex-row">
          <aside className="shrink-0 border-b px-4 py-5 @3xl/settings:w-56 @3xl/settings:border-e @3xl/settings:border-b-0 @3xl/settings:px-4 @3xl/settings:py-7">
            <nav aria-label="Settings" className="space-y-2">
              <p className="px-3 text-xs font-medium text-muted-foreground">
                User
              </p>
              <Link
                to="/settings/profile"
                aria-current="page"
                className="flex items-center gap-2.5 rounded-md bg-accent px-3 py-2 text-sm font-medium text-accent-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <UserRound className="size-4" />
                Profile
              </Link>
            </nav>
          </aside>
          <section
            aria-labelledby="profile-heading"
            className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain"
          >
            <div className="mx-auto w-full max-w-3xl space-y-6 px-5 py-7 pb-20 @3xl/settings:px-10 @3xl/settings:py-10">
              <div className="space-y-1.5">
                <h2 id="profile-heading" className="text-2xl font-medium">
                  Profile
                </h2>
                <p className="text-sm text-muted-foreground">
                  Manage your personal details.
                </p>
              </div>
              {!username || profile.isPending ? (
                <p role="status" className="py-6 text-sm text-muted-foreground">
                  Loading your profile…
                </p>
              ) : profile.isError ? (
                <div role="alert" className="space-y-3 rounded-lg border p-5">
                  <p className="text-sm">Your profile could not be loaded.</p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void profile.refetch()}
                  >
                    Try again
                  </Button>
                </div>
              ) : (
                <ProfileForm
                  key={`${username}:${profile.dataUpdatedAt}`}
                  profile={profile.data}
                />
              )}
            </div>
          </section>
        </div>
      </Main>
    </>
  );
}

function ProfileForm({ profile }: { profile: Profile }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(profile);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(false);
  const dirty =
    draft.first_name !== profile.first_name ||
    draft.last_name !== profile.last_name ||
    draft.email !== profile.email;
  const initials =
    [draft.first_name, draft.last_name]
      .filter(Boolean)
      .map((part) => part.trim()[0] ?? "")
      .join("")
      .toUpperCase() || profile.username.slice(0, 2).toUpperCase();

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!dirty || saving) return;
    setSaving(true);
    setError(false);
    try {
      const updated = await api.put<Profile>("/auth/profile", {
        first_name: draft.first_name.trim(),
        last_name: draft.last_name.trim(),
        email: draft.email.trim(),
      });
      queryClient.setQueryData(["account-profile", profile.username], updated);
      toast.success("Profile saved.");
    } catch {
      setError(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={(event) => void save(event)} className="space-y-4">
      <div className="divide-y rounded-lg border px-4 @3xl/settings:px-5">
        <ProfileRow label="Profile picture">
          <div className="flex justify-end">
            <Avatar className="size-9 border">
              <AvatarFallback className="bg-muted text-xs font-medium">
                {initials}
              </AvatarFallback>
            </Avatar>
          </div>
        </ProfileRow>
        <ProfileRow label="Username" htmlFor="profile-username">
          <Input
            id="profile-username"
            value={profile.username}
            readOnly
            className="bg-muted/40 text-muted-foreground"
          />
        </ProfileRow>
        {(
          [
            ["first_name", "First name", "given-name"],
            ["last_name", "Last name", "family-name"],
            ["email", "Email", "email"],
          ] as const
        ).map(([field, label, autocomplete]) => (
          <ProfileRow key={field} label={label} htmlFor={`profile-${field}`}>
            <Input
              id={`profile-${field}`}
              name={field}
              type={field === "email" ? "email" : "text"}
              autoComplete={autocomplete}
              maxLength={field === "email" ? 254 : 100}
              value={draft[field]}
              disabled={saving}
              onChange={(event) => {
                setDraft({ ...draft, [field]: event.target.value });
                setError(false);
              }}
            />
          </ProfileRow>
        ))}
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2">
        <p
          role={error ? "alert" : "status"}
          className="me-auto text-xs text-muted-foreground"
        >
          {error ? "Could not save your profile. Please try again." : ""}
        </p>
        {dirty && (
          <Button
            type="button"
            variant="ghost"
            disabled={saving}
            onClick={() => {
              setDraft(profile);
              setError(false);
            }}
          >
            Cancel
          </Button>
        )}
        <Button type="submit" disabled={!dirty || saving}>
          {saving ? "Saving…" : "Save changes"}
        </Button>
      </div>
    </form>
  );
}

function ProfileRow({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div className="grid min-w-0 items-center gap-3 py-4 @3xl/settings:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] @3xl/settings:gap-6">
      {htmlFor ? (
        <Label htmlFor={htmlFor} className="text-sm font-normal">
          {label}
        </Label>
      ) : (
        <span className="text-sm">{label}</span>
      )}
      <div className="min-w-0">{children}</div>
    </div>
  );
}
