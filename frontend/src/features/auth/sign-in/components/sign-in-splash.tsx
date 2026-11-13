import { useEffect, useRef } from "react";
import { Logo } from "@/assets/logo";
import "./sign-in-splash.css";

export function SignInSplash({ onReady }: { onReady: () => void }) {
  const statusRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    statusRef.current?.focus();
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const timer = window.setTimeout(
      onReady,
      reducedMotion.matches ? 200 : 1600,
    );
    return () => window.clearTimeout(timer);
  }, [onReady]);

  return (
    <div
      ref={statusRef}
      role="status"
      aria-label="Opening Nova"
      tabIndex={-1}
      className="nova-sign-in-splash fixed inset-0 z-50 flex flex-col items-center justify-center-safe gap-8 overflow-y-auto bg-background p-6 text-foreground"
    >
      <div
        className="nova-splash-phoenix relative size-36 max-h-[32dvh] max-w-[32dvh] shrink-0 sm:size-44"
        aria-hidden="true"
      >
        <Logo
          sizes="(min-width: 640px) 176px, 144px"
          className="nova-splash-wing nova-splash-wing-left absolute inset-0 size-full"
          fetchPriority="high"
        />
        <Logo
          sizes="(min-width: 640px) 176px, 144px"
          className="nova-splash-wing nova-splash-wing-right absolute inset-0 size-full"
          fetchPriority="high"
        />
        <Logo
          sizes="(min-width: 640px) 176px, 144px"
          className="nova-splash-body absolute inset-0 size-full"
          fetchPriority="high"
        />
      </div>
      <div className="space-y-2 text-center">
        <p className="text-xl font-semibold tracking-tight">Nova</p>
        <p className="text-sm text-muted-foreground">Opening your workspace…</p>
      </div>
    </div>
  );
}
