import { apiClient } from "./serverClient";

export interface OneClickCveProject {
  id: string;
  batch_id: string;
  project_id?: string | null;
  agent_task_id?: string | null;
  github_full_name: string;
  repository_url: string;
  description?: string | null;
  language?: string | null;
  stars: number;
  pushed_at?: string | null;
  updated_at?: string | null;
  default_branch?: string | null;
  version_label?: string | null;
  version_source?: string | null;
  has_security_advisory: boolean;
  advisory_count: number;
  has_security_policy: boolean;
  has_private_vulnerability_reporting: boolean;
  score: number;
  status: string;
  findings_count: number;
  error_message?: string | null;
  created_at?: string | null;
}

export interface OneClickCveBatch {
  id: string;
  user_id: string;
  requested_count: number;
  found_count: number;
  status: string;
  current_step?: string | null;
  error_message?: string | null;
  summary_json?: Record<string, unknown> | null;
  prefer_security_advisory: boolean;
  started_at?: string | null;
  completed_at?: string | null;
  created_at?: string | null;
  projects: OneClickCveProject[];
}

export async function createOneClickCveBatch(targetCount: number, preferSecurityAdvisory = true): Promise<OneClickCveBatch> {
  const response = await apiClient.post("/one-click-cve/batches", {
    target_count: targetCount,
    prefer_security_advisory: preferSecurityAdvisory,
  });
  return response.data;
}

export async function listOneClickCveBatches(limit = 30): Promise<OneClickCveBatch[]> {
  const response = await apiClient.get("/one-click-cve/batches", { params: { limit } });
  return response.data;
}

export async function getOneClickCveBatch(batchId: string): Promise<OneClickCveBatch> {
  const response = await apiClient.get(`/one-click-cve/batches/${batchId}`);
  return response.data;
}

export async function cancelOneClickCveBatch(batchId: string): Promise<OneClickCveBatch> {
  const response = await apiClient.post(`/one-click-cve/batches/${batchId}/cancel`);
  return response.data;
}
