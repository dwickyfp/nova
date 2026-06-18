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
            <span className='grid flex-1 text-start leading-tight group-data-[collapsible=icon]:hidden'>
              <span className='truncate text-base font-bold tracking-tight'>
                Nova
              </span>
              <span className='truncate text-[11px] font-medium text-muted-foreground'>
                Powered by StarRocks
              </span>
            </span>
          </Link>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
