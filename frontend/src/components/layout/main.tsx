import { cn } from '@/lib/utils'

type MainProps = React.HTMLAttributes<HTMLElement> & {
  fixed?: boolean
  scroll?: boolean
  fluid?: boolean
  ref?: React.Ref<HTMLElement>
}

export function Main({ fixed, scroll, className, fluid, ...props }: MainProps) {
  const bounded = fixed || scroll
  return (
    <main
      data-layout={bounded ? 'fixed' : 'auto'}
      className={cn(
        'px-4 py-6',

        // Bound the content to the viewport. Simple pages scroll here; complex
        // pages use `fixed` and place their own scroller inside the main area.
        bounded && 'flex min-h-0 min-w-0 grow flex-col',
        scroll ? 'overflow-y-auto overscroll-contain' : fixed && 'overflow-hidden',

        // If layout is not fluid, set the max-width
        !fluid &&
          '@7xl/content:mx-auto @7xl/content:w-full @7xl/content:max-w-7xl',
        className
      )}
      {...props}
    />
  )
}
