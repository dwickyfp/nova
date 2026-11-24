import { type ComponentProps } from "react";
import { cn } from "@/lib/utils";

const LIGHT =
  "/images/nova-mark-64.png 64w, /images/nova-mark-128.png 128w, /images/nova-mark-256.png 256w, /images/nova-mark-512.png 512w, /images/nova-phoenix-master.png 1024w";
const DARK =
  "/images/nova-mark-64-dark.png 64w, /images/nova-mark-128-dark.png 128w, /images/nova-mark-256-dark.png 256w, /images/nova-mark-512-dark.png 512w, /images/nova-phoenix-master-dark.png 1024w";

/**
 * The red Nova phoenix (--brand-mark). Dark mode swaps in the same composition
 * with the red lifted to --brand-mark's dark value, so the mark keeps 3:1
 * against the dark navigation glass. The swap follows the `.dark` class.
 */
export function Logo({
  className,
  alt = "",
  ...props
}: Omit<ComponentProps<"img">, "src" | "srcSet">) {
  const shared = {
    sizes: "24px",
    width: 128,
    height: 128,
    alt,
    "aria-hidden": alt === "" ? true : undefined,
    ...props,
  };
  return (
    <>
      <img
        src="/images/nova-mark-128.png"
        srcSet={LIGHT}
        className={cn("size-6 object-contain", className, "dark:hidden")}
        {...shared}
      />
      <img
        src="/images/nova-mark-128-dark.png"
        srcSet={DARK}
        className={cn("size-6 object-contain", className, "hidden dark:block")}
        {...shared}
      />
    </>
  );
}
