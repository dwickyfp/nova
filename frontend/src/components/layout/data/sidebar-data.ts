import {
  Bot,
  Brain,
  Clock,
  Database,
  LayoutDashboard,
  ListTodo,
  Shield,
  SquareChartGantt,
  TrendingUp,
  Upload,
  Users,
  Zap,
  Activity,
  Workflow,
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
          title: 'Workspaces',
          url: '/workspaces',
          icon: SquareChartGantt,
        },
        {
          title: 'Database Explorer',
          url: '/database-explorer',
          icon: Database,
        },
        {
          title: 'Tasks',
          url: '/tasks-manager',
          icon: Workflow,
        },
        {
          title: 'ML Models',
          url: '/ml-models',
          icon: Brain,
        },
      ],
    },
    {
      title: 'Administrator',
      items: [
        {
          title: 'Users',
          url: '/users',
          icon: Users,
        },
        {
          title: 'Roles',
          url: '/roles',
          icon: Shield,
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
              url: '/query-cost',
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
