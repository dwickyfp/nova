import { Link } from '@tanstack/react-router'
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar'

export function AppTitle() {
  const { setOpenMobile } = useSidebar()
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton
          size='lg'
          className='h-14 gap-3 px-1 transition-none hover:bg-transparent hover:text-sidebar-foreground active:bg-transparent active:text-sidebar-foreground'
          asChild
        >
          <Link to='/' onClick={() => setOpenMobile(false)}>
            <span className='flex size-10 shrink-0 items-center justify-center p-0.5 group-data-[collapsible=icon]:size-8'>
              <img
                src='/images/nova-mark.svg'
                alt=''
                className='size-full'
                aria-hidden='true'
              />
            </span>
            <span className='grid min-w-0 flex-1 gap-1 text-start leading-tight group-data-[collapsible=icon]:hidden'>
              <span className='truncate font-manrope text-lg font-semibold leading-none text-primary'>
                nova
              </span>
              <span className='inline-flex h-5 max-w-fit items-center gap-1.5 rounded-md border border-primary/15 bg-primary/5 px-1.5 text-[10px] font-semibold leading-none text-muted-foreground shadow-[inset_0_1px_0_rgb(255_255_255_/_0.55)] dark:border-primary/25 dark:bg-primary/10'>
                <span
                  className='size-1.5 rounded-full bg-chart-2 shadow-[0_0_0_2px_rgb(20_168_154_/_0.12)]'
                  aria-hidden='true'
                />
                <span className='truncate'>
                  Powered by{' '}
                  <span className='bg-gradient-to-r from-[#368a98] via-[#368a98] to-[#f6bd1f] bg-clip-text text-transparent'>
                    StarRocks
                  </span>
                </span>
              </span>
            </span>
          </Link>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
