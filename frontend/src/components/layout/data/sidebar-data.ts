import {
  Bot,
  Brain,
  BrainCircuit,
  Blocks,
  BookOpen,
  Clock,
  Database,
  Network,
  House,
  ListTodo,
  Shield,
  Sparkles,
  SquareChartGantt,
  TrendingUp,
  Upload,
  Users,
  Zap,
  Activity,
  ArrowRightLeft,
  Workflow,
  Server,
  Settings,
  ShieldAlert,
  Wrench,
} from "lucide-react";
import { type SidebarData } from "../types";

export const sidebarData: SidebarData = {
  navGroups: [
    {
      title: "General",
      items: [
        {
          title: "Home",
          url: "/",
          icon: House,
        },
      ],
    },
    {
      title: "Data Management",
      items: [
        {
          title: "Workspaces",
          url: "/workspaces",
          icon: SquareChartGantt,
        },
        {
          title: "Database Explorer",
          url: "/database-explorer",
          icon: Database,
        },
        {
          title: "Tasks",
          url: "/tasks",
          icon: Workflow,
        },
        {
          title: "AI & ML",
          icon: Brain,
          items: [
            {
              title: "Agent",
              url: "/agents",
              icon: Bot,
              section: "AI",
            },
            {
              title: "Semantic",
              url: "/agents/semantic",
              icon: Blocks,
            },
            {
              title: "Semantic Views",
              url: "/semantic-views",
              icon: BrainCircuit,
            },
            {
              title: "AI Search",
              url: "/ai-search",
              icon: Sparkles,
            },
            {
              title: "Skills",
              url: "/agents/skills",
              icon: BookOpen,
            },
            {
              title: "Tools",
              url: "/agents/tools",
              icon: Wrench,
            },
            {
              title: "Nova Studio",
              url: "/studio",
              icon: Sparkles,
              // A full-page standalone surface: open it in a new tab so the
              // console keeps its current page, the way Snowflake CoWork opens
              // separately.
              newTab: true,
            },
            {
              title: "Feature Store",
              url: "/feature-store",
              icon: Network,
              section: "Machine Learning",
            },
            {
              title: "ML Models",
              url: "/ml-models",
              icon: Brain,
            },
          ],
        },
      ],
    },
    {
      title: "Access Control",
      items: [
        {
          title: "Users",
          url: "/users",
          icon: Users,
        },
        {
          title: "Roles",
          url: "/roles",
          icon: Shield,
        },
        {
          title: "Data Access",
          url: "/access-control",
          icon: ShieldAlert,
        },
        {
          title: "Audit",
          url: "/monitoring/audit",
          icon: Activity,
        },
      ],
    },
    {
      title: "Administrator",
      items: [
        {
          title: "AI Providers",
          url: "/ai-providers",
          icon: Bot,
        },
        {
          title: "Admin Settings",
          url: "/settings",
          icon: Settings,
        },
      ],
    },
    {
      title: "Operations",
      items: [
        {
          title: "Migration",
          url: "/migration",
          icon: ArrowRightLeft,
        },
        {
          title: "Monitoring",
          icon: Activity,
          items: [
            {
              title: "Query History",
              url: "/query-history",
              icon: Clock,
            },
            {
              title: "Active Queries",
              url: "/active-query",
              icon: Zap,
            },
            {
              title: "Audit Trail",
              url: "/monitoring/audit",
              icon: Shield,
            },
            {
              title: "Tasks",
              url: "/tasks",
              icon: ListTodo,
            },
            {
              title: "Query Cost",
              url: "/query-cost",
              icon: TrendingUp,
            },
            {
              title: "Data Loads",
              url: "/monitoring/loads",
              icon: Upload,
            },
            {
              title: "Cluster Monitor",
              url: "/monitoring/cluster",
              icon: Server,
            },
            {
              title: "Production Health",
              url: "/monitoring/health",
              icon: ShieldAlert,
            },
          ],
        },
      ],
    },
  ],
};
