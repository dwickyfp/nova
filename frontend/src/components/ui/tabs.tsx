import * as React from 'react'
import * as TabsPrimitive from '@radix-ui/react-tabs'
import { cn } from '@/lib/utils'

function Tabs({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Root>) {
  return (
    <TabsPrimitive.Root
      data-slot='tabs'
      className={cn('flex flex-col gap-2', className)}
      {...props}
    />
  )
}

function TabsList({
  className,
  children,
  indicatorVariant = 'pill',
  ref: forwardedRef,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.List> & {
  indicatorVariant?: 'pill' | 'underline'
}) {
  const listRef = React.useRef<HTMLDivElement>(null)
  const [indicator, setIndicator] = React.useState<{
    x: number
    y: number
    width: number
    height: number
    radius: string
  } | null>(null)

  React.useImperativeHandle(
    forwardedRef,
    () => listRef.current as HTMLDivElement
  )

  const updateIndicator = React.useCallback(() => {
    const list = listRef.current
    const activeTab = list?.querySelector<HTMLElement>(
      '[role="tab"][data-state="active"]'
    )

    if (!list || !activeTab) {
      setIndicator(null)
      return
    }

    const listRect = list.getBoundingClientRect()
    const tabRect = activeTab.getBoundingClientRect()
    const nextIndicator = {
      x: tabRect.left - listRect.left + list.scrollLeft,
      y:
        (indicatorVariant === 'underline' ? tabRect.bottom - 2 : tabRect.top) -
        listRect.top +
        list.scrollTop,
      width: tabRect.width,
      height: indicatorVariant === 'underline' ? 2 : tabRect.height,
      radius:
        indicatorVariant === 'underline'
          ? '0px'
          : window.getComputedStyle(activeTab).borderRadius,
    }

    setIndicator((current) =>
      current &&
      current.x === nextIndicator.x &&
      current.y === nextIndicator.y &&
      current.width === nextIndicator.width &&
      current.height === nextIndicator.height &&
      current.radius === nextIndicator.radius
        ? current
        : nextIndicator
    )
  }, [indicatorVariant])

  React.useLayoutEffect(() => {
    const list = listRef.current
    if (!list) return

    updateIndicator()

    const mutationObserver = new MutationObserver(updateIndicator)
    mutationObserver.observe(list, {
      attributes: true,
      childList: true,
      subtree: true,
      attributeFilter: ['data-state'],
    })

    const resizeObserver = new ResizeObserver(updateIndicator)
    resizeObserver.observe(list)
    list
      .querySelectorAll<HTMLElement>('[role="tab"]')
      .forEach((tab) => resizeObserver.observe(tab))
    list.addEventListener('scroll', updateIndicator, { passive: true })

    return () => {
      mutationObserver.disconnect()
      resizeObserver.disconnect()
      list.removeEventListener('scroll', updateIndicator)
    }
  }, [children, updateIndicator])

  return (
    <TabsPrimitive.List
      ref={listRef}
      data-slot='tabs-list'
      className={cn(
        'relative isolate inline-flex h-9 w-fit items-center justify-center rounded-lg bg-muted p-0.75 text-muted-foreground',
        className
      )}
      {...props}
    >
      {indicator && (
        <span
          aria-hidden='true'
          data-slot='tabs-indicator'
          className={cn(
            'pointer-events-none absolute left-0 top-0 z-0 transition-[transform,width,height] duration-200 ease-out motion-reduce:transition-none',
            indicatorVariant === 'underline'
              ? 'bg-primary'
              : 'border border-transparent bg-background shadow-sm dark:border-input dark:bg-input/30'
          )}
          style={{
            width: indicator.width,
            height: indicator.height,
            borderRadius: indicator.radius,
            transform: `translate3d(${indicator.x}px, ${indicator.y}px, 0)`,
          }}
        />
      )}
      {children}
    </TabsPrimitive.List>
  )
}

function TabsTrigger({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      data-slot='tabs-trigger'
      className={cn(
        "relative z-10 inline-flex h-[calc(100%-1px)] flex-1 items-center justify-center gap-1.5 rounded-md border border-transparent bg-transparent px-2 py-1 text-sm font-medium whitespace-nowrap text-foreground transition-colors duration-200 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-1 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-50 dark:text-muted-foreground dark:data-[state=active]:text-foreground [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className
      )}
      {...props}
    />
  )
}

function TabsContent({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      data-slot='tabs-content'
      className={cn('flex-1 outline-none', className)}
      {...props}
    />
  )
}

export { Tabs, TabsList, TabsTrigger, TabsContent }
