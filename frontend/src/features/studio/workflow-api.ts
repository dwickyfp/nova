import { api } from "@/lib/api-client";
import type { EvidenceEnvelope } from "./evidence-health";
import type { Investigation, News, MonitorConfiguration } from "@/features/intelligence/lifecycle-api";

export type WorkIntent = "ANSWER" | "ANALYZE" | "INVESTIGATE" | "PLAN" | "RESEARCH" | "ACT";
export type MissionStageKind = "investigate" | "evidence" | "scenarios" | "decide" | "approve" | "execute" | "verify" | "observe" | "improve";
export type MissionObjectRef = { kind: "investigation" | "decision" | "action" | "outcome"; id: string; revision: number };
export type Mission = {
  mission_id: string;
  thread_id: string;
  agent_id?: string | null;
  objective: string;
  work_intent: WorkIntent;
  status: "planned" | "running" | "completed" | "blocked" | "cancelling" | "cancelled";
  revision: number;
  run_ids: string[];
  stages: { kind: MissionStageKind; label: string; status: "planned" | "running" | "completed" | "blocked" | "cancelled"; source_refs: string[] }[];
  evidence_refs: string[];
  object_refs: MissionObjectRef[];
  cancel_requested: boolean;
  created_at: string;
  updated_at: string;
  continuation?: { mode: "continue" | "new" | "none"; reason: string; mission_id?: string | null } | null;
  investigation_requirements?: { required_inputs: string[]; established: EvidenceEnvelope } | null;
};
export type MissionDeliverable = {
  deliverable_id: string;
  mission_id: string;
  mission_revision: number;
  kind: "decision_memo" | "action_plan" | "analysis_summary" | "investigation_report" | "scenario_comparison" | "outcome_report";
  title: string;
  markdown: string;
  evidence_refs: string[];
  object_refs: MissionObjectRef[];
  created_at: string;
  sources?: { kind: string; id: string; revision: number; fingerprint: string; semantic?: { view_id: string; version: number; fingerprint: string } | null; facts: Record<string, unknown> }[];
};
export type ResumableMission = Pick<Mission, "mission_id" | "thread_id" | "objective" | "status" | "revision"> & { resume_required: boolean };

export function mergeMissionProjection(current: { missions: Mission[] } | undefined, mission: Mission, threadId: string): { missions: Mission[] } | undefined {
  if (mission.thread_id !== threadId) return current;
  const rows = current?.missions ?? [];
  const previous = rows.find((item) => item.mission_id === mission.mission_id);
  if (previous && previous.revision >= mission.revision) return current;
  return { missions: previous ? rows.map((item) => item.mission_id === mission.mission_id ? mission : item) : [mission, ...rows] };
}

const missionPath = (id: string) => `/agents/studio/missions/${encodeURIComponent(id)}`;
export const workflowApi = {
  list: (threadId: string, signal?: AbortSignal) => api.get<{ missions: Mission[] }>(
    `/agents/studio/threads/${encodeURIComponent(threadId)}/missions`, signal,
  ),
  create: (threadId: string, body: { objective: string; work_intent: WorkIntent; operation_id: string; new_mission?: boolean; continue_mission_id?: string }) => api.post<Mission>(
    `/agents/studio/threads/${encodeURIComponent(threadId)}/missions`, body,
  ),
  get: (missionId: string, signal?: AbortSignal) => api.get<Mission>(missionPath(missionId), signal),
  resumable: (threadId: string, signal?: AbortSignal) => api.get<{ missions: ResumableMission[] }>(`/agents/studio/threads/${encodeURIComponent(threadId)}/missions/resumable`, signal),
  resume: (mission: ResumableMission, operation_id: string) => api.post<Mission>(`${missionPath(mission.mission_id)}/resume`, { expected_revision: mission.revision, operation_id }),
  canonical: <T>(missionId: string, reference: MissionObjectRef, signal?: AbortSignal) => api.get<T>(`${missionPath(missionId)}/objects/${reference.kind}/${encodeURIComponent(reference.id)}?revision=${reference.revision}`, signal),
  investigationContext: (missionId: string, investigationId: string, revision: number, signal?: AbortSignal) => api.get<{ investigation: Investigation; news: News; monitor: MonitorConfiguration }>(`${missionPath(missionId)}/objects/investigation/${encodeURIComponent(investigationId)}/context?revision=${revision}`, signal),
  attachRun: (missionId: string, run_id: string) => api.post<Mission>(`${missionPath(missionId)}/runs`, { run_id }),
  cancel: (mission: Mission) => api.post<Mission>(`${missionPath(mission.mission_id)}/cancel`, { expected_revision: mission.revision }),
  link: (mission: Mission, object_ref: MissionObjectRef) => api.post<Mission>(`${missionPath(mission.mission_id)}/objects`, { expected_revision: mission.revision, object_ref }),
  deliverables: (missionId: string, signal?: AbortSignal) => api.get<{ deliverables: MissionDeliverable[] }>(`${missionPath(missionId)}/deliverables`, signal),
  deliver: (mission: Mission, kind: MissionDeliverable["kind"], operation_id: string) => api.post<MissionDeliverable>(`${missionPath(mission.mission_id)}/deliverables`, { expected_revision: mission.revision, kind, operation_id }),
};

export type StudioResource = { resource_id: string; message_id: string; name: string; media_type: string; size_bytes: number; digest: string; attachment_index: number };
export const workflowResourcesApi = {
  list: (threadId: string, rootRunId: string, participant = "/root", signal?: AbortSignal) => api.get<{ resources: StudioResource[] }>(`/agents/studio/threads/${encodeURIComponent(threadId)}/resources?root_run_id=${encodeURIComponent(rootRunId)}&participant=${encodeURIComponent(participant)}`, signal),
  grant: (threadId: string, body: { root_run_id: string; grantor: string; target: string; resource_refs: string[] }) => api.post<{ resource_refs: string[]; participant: string }>(`/agents/studio/threads/${encodeURIComponent(threadId)}/resources/grants`, body),
};
