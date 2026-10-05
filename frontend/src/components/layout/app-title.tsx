import { Logo } from '@/assets/logo'
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
          data-sidebar-brand=''
          size='lg'
          className='h-14 gap-3 px-1 transition-none hover:bg-transparent hover:text-sidebar-foreground active:bg-transparent active:text-sidebar-foreground'
          asChild
        >
          <Link to='/' onClick={() => setOpenMobile(false)}>
            <span className='flex size-10 shrink-0 items-center justify-center p-0.5 group-data-[collapsible=icon]:size-8'>
              <Logo sizes='40px'
                className='size-full'
              />
            </span>
            <span className='grid min-w-0 flex-1 gap-1 text-start leading-tight group-data-[collapsible=icon]:hidden'>
              <span className='truncate text-lg font-medium leading-none text-primary'>
                nova
              </span>
              <span className='truncate text-xs leading-none text-sidebar-navigation-muted-foreground'>
                Enterprise Intelligence OS
              </span>
            </span>
          </Link>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
