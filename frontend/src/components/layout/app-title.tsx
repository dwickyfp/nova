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
              <span className='truncate text-lg font-medium leading-none text-primary'>
                nova
              </span>
              <span className='truncate text-[11px] font-medium leading-none text-muted-foreground'>
                Data warehouse + AI
              </span>
            </span>
          </Link>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
