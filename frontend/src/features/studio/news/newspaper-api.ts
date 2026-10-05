import { api } from "@/lib/api-client";

export type StorySlice = { dimension: string; value: string };
export type SeriesPoint = { date: string; value: number; count: number };
export type StoryDriver = {
  dimension: string;
  value: string;
  before: number;
  after: number;
  change: number;
};
export type BusinessRule = {
  source: "metric" | "dimension" | "view" | "threshold";
  name: string;
  text: string;
};
export type StoryNarrative = {
  headline: string;
  deck: string;
  what_happened: string;
  why_it_matters: string;
  what_to_check: string;
};
export type Story = {
  id: string;
  revision: number;
  view_id: string;
  semantic_version: number;
  metric: string;
  metric_label: string;
  unit: string | null;
  slice: StorySlice | null;
  edition_date: string;
  baseline_dates: string[];
  before: number;
  after: number;
  change: number;
  relative_change: number | null;
  severity: "warning" | "critical";
  rank: number;
  confidence: string;
  series: SeriesPoint[];
  drivers: StoryDriver[];
  narrative: StoryNarrative;
  business_rules: BusinessRule[];
  narrative_source: "template" | "model";
  /** Good or bad for the business as the model judged the metric; null when unjudged. */
  impact?: "favorable" | "unfavorable" | "neutral" | null;
  /** Verified strings the page may emphasise inside the narrative. */
  highlights?: { text: string; kind: "change" | "figure" | "subject" }[];
  /** This reader's own reaction and where the story sits in their order. */
  reaction?: Reaction | null;
  score?: number;
  head?: boolean;
  reason?: string;
  /** The Semantic View the story comes from; the desk it belongs to. */
  view_name?: string;
};
export type Reaction = "like" | "dislike";
export type StoryDetail = Story & { view_name: string; pressed_at: string };
export type NewspaperSection = {
  view_id: string;
  name: string;
  edition_date: string;
  pressed_at: string;
  stories: Story[];
};
export type Newspaper = {
  edition_date: string | null;
  sections: NewspaperSection[];
};

export const newspaperApi = {
  read: (edition?: string, signal?: AbortSignal) =>
    api.get<Newspaper>(
      `/intelligence/newspaper${edition ? `?edition=${encodeURIComponent(edition)}` : ""}`,
      signal,
    ),
  react: (id: string, reaction: Reaction | null) =>
    api.put<{ id: string; reaction: Reaction | null }>(
      `/intelligence/stories/${encodeURIComponent(id)}/feedback`,
      { reaction },
    ),
  story: (id: string, signal?: AbortSignal) =>
    api.get<StoryDetail>(
      `/intelligence/stories/${encodeURIComponent(id)}`,
      signal,
    ),
};
