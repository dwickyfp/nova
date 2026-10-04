import { type ComponentProps } from "react";
import { cn } from "@/lib/utils";

export function Logo({
  className,
  alt = "",
  ...props
}: Omit<ComponentProps<"img">, "src" | "srcSet">) {
  return (
    <img
      src="/images/nova-mark-128.png"
      srcSet="/images/nova-mark-64.png 64w, /images/nova-mark-128.png 128w, /images/nova-mark-256.png 256w, /images/nova-mark-512.png 512w, /images/nova-phoenix-master.png 1024w"
      sizes="24px"
      width={128}
      height={128}
      alt={alt}
      aria-hidden={alt === "" ? true : undefined}
      className={cn("size-6 object-contain", className)}
      {...props}
    />
  );
}
