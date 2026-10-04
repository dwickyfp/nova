import { Logo } from "@/assets/logo";

export function SignInVisual() {
  return (
    <aside
      className="relative hidden h-svh min-h-0 flex-col justify-between overflow-hidden border-s border-border bg-muted px-12 py-12 lg:flex"
      aria-label="Nova data platform illustration"
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
          Data warehouse + AI
        </p>
        <p className="text-2xl font-semibold tracking-tight text-foreground">
          From raw data to intelligent action.
        </p>
        <p className="mt-2 max-w-sm text-sm leading-6 text-muted-foreground">
          Query, govern, and build AI experiences on one fast columnar engine.
        </p>
      </div>
    </aside>
  );
}
