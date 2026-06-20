import {
  Bot,
  Clock,
  Database,
  LayoutDashboard,
  ListTodo,
  Shield,
  TrendingUp,
  Upload,
  Users,
  Zap,
  Activity,
} from 'lucide-react'
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
      ],
    },
    {
      title: 'Data Management',
      items: [
        {
          title: 'Database Explorer',
          url: '/database-explorer',
          icon: Database,
        },
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
        {
          title: 'AI Providers',
          url: '/ai-providers',
          icon: Bot,
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
