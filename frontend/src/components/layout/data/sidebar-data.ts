import {
  Clock,
  Database,
  LayoutDashboard,
  ListTodo,
  Package,
  Shield,
  TrendingUp,
  Upload,
  Users,
  Zap,
  MessagesSquare,
  Activity,
} from 'lucide-react'
import { ClerkLogo } from '@/assets/clerk-logo'
import { type SidebarData } from '../types'

export const sidebarData: SidebarData = {
  navGroups: [
    {
      title: 'General',
      items: [
        {
          title: 'Dashboard',
          url: '/',
          icon: LayoutDashboard,
        },
        {
          title: 'Tasks',
          url: '/tasks',
          icon: ListTodo,
          showChevron: true,
        },
        {
          title: 'Apps',
          url: '/apps',
          icon: Package,
          showChevron: true,
        },
        {
          title: 'Chats',
          url: '/chats',
          badge: '3',
          icon: MessagesSquare,
          showChevron: true,
        },
        {
          title: 'Secured by Clerk',
          icon: ClerkLogo,
          showChevron: true,
          items: [
            {
              title: 'Sign In',
              url: '/clerk/sign-in',
            },
            {
              title: 'Sign Up',
              url: '/clerk/sign-up',
            },
            {
              title: 'User Management',
              url: '/clerk/user-management',
            },
          ],
        },
      ],
    },
    {
      title: 'Data Management',
      items: [
        {
          title: 'Workspaces',
          url: '/workspaces',
          icon: Database,
        },
      ],
    },
    {
      title: 'Administrator',
      items: [
        {
          title: 'Users & Roles',
          url: '/users',
          icon: Users,
        },
      ],
    },
    {
      title: 'Operations',
      items: [
        {
          title: 'Monitoring',
          icon: Activity,
          items: [
            {
              title: 'Query History',
              url: '/query-history',
              icon: Clock,
            },
            {
              title: 'Active Queries',
              url: '/active-query',
              icon: Zap,
            },
            {
              title: 'Audit Trail',
              url: '/monitoring/audit',
              icon: Shield,
            },
            {
              title: 'Tasks',
              url: '/tasks',
              icon: ListTodo,
            },
            {
              title: 'Query Cost',
              url: '/monitoring/cost',
              icon: TrendingUp,
            },
            {
              title: 'Data Loads',
              url: '/monitoring/loads',
              icon: Upload,
            },
          ],
        },
      ],
    },
  ],
}
