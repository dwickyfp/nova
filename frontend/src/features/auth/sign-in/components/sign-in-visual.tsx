import { Logo } from "@/assets/logo";

export function SignInVisual() {
  return (
    <aside
      className="relative hidden h-svh min-h-0 flex-col justify-between overflow-hidden border-s border-border bg-muted px-12 py-12 lg:flex"
      aria-label="Nova Enterprise Intelligence OS illustration"
    >
      <div className="flex min-h-0 flex-1 items-center justify-center py-8">
        <Logo
          alt="Nova phoenix"
          sizes="(min-width: 1536px) 512px, 40vw"
          className="h-full max-h-[32rem] w-full max-w-lg"
        />
      </div>
      <div className="max-w-md shrink-0">
        <p className="mb-4 text-xs font-semibold text-primary">
          Enterprise Intelligence OS
        </p>
        <p className="text-2xl font-semibold tracking-tight text-foreground">
          Understand, decide, act, and learn.
        </p>
        <p className="mt-2 max-w-sm text-sm leading-6 text-muted-foreground">
          Turn governed enterprise data into understanding, decisions, actions, and learning.
        </p>
      </div>
    </aside>
  );
}
