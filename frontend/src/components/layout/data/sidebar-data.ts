import {
  Bot,
  Brain,
  Clock,
  Database,
  FolderOpen,
  House,
  ListTodo,
  Shield,
  SquareChartGantt,
  TrendingUp,
  Upload,
  Users,
  Zap,
  Activity,
  ArrowRightLeft,
  FunctionSquare,
  GitFork,
  Layers,
  Workflow,
} from 'lucide-react';
import { type SidebarData } from '../types';

export const sidebarData: SidebarData = {
  navGroups: [
    {
      title: 'General',
      items: [
        {
          title: 'Home',
          url: '/',
          icon: House,
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
          title: 'Stages',
          url: '/stages',
          icon: FolderOpen,
        },
        {
          title: 'External Catalogs',
          url: '/external-catalogs',
          icon: Layers,
        },
        {
          title: 'Migration',
          url: '/migration',
          icon: ArrowRightLeft,
        },
        {
          title: 'Tasks',
          url: '/tasks-manager',
          icon: Workflow,
        },
        {
          title: 'Task Graphs',
          url: '/task-graphs',
          icon: GitFork,
        },
        {
          title: 'ML Models',
          url: '/ml-models',
          icon: Brain,
        },
        {
          title: 'Functions',
          url: '/functions',
          icon: FunctionSquare,
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
};
