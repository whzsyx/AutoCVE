import { apiClient } from '@/shared/api/serverClient';

export interface CheckmarxScanJob {
  id: string;
  status: string;
  current_step?: string | null;
  progress: number;
  project_name: string;
  source_filename: string;
  checkmarx_base_url?: string | null;
  checkmarx_project_id?: string | null;
  scan_id?: string | null;
  totals_json?: string | null;
  error_message?: string | null;
  created_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
  results_count: number;
}

export interface CheckmarxScanResult {
  id: string;
  scan_id: string;
  path_id: string;
  vulnerability: string;
  type: string;
  severity?: number | null;
  url: string;
  ai_judgement?: boolean | null;
  ai_reason?: string | null;
}

export async function createCheckmarxScan(params: {
  file: File;
  projectName: string;
  username: string;
  password: string;
  baseUrl?: string;
}): Promise<CheckmarxScanJob> {
  const formData = new FormData();
  formData.append('file', params.file);
  formData.append('project_name', params.projectName);
  formData.append('username', params.username);
  formData.append('password', params.password);
  if (params.baseUrl?.trim()) {
    formData.append('base_url', params.baseUrl.trim());
  }

  const response = await apiClient.post<CheckmarxScanJob>('/checkmarx/scans', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
}

export async function getCheckmarxScan(jobId: string): Promise<CheckmarxScanJob> {
  const response = await apiClient.get<CheckmarxScanJob>(`/checkmarx/scans/${jobId}`);
  return response.data;
}

export async function listCheckmarxScans(): Promise<CheckmarxScanJob[]> {
  const response = await apiClient.get<CheckmarxScanJob[]>('/checkmarx/scans');
  return response.data;
}

export async function listCheckmarxScanResults(jobId: string): Promise<CheckmarxScanResult[]> {
  const response = await apiClient.get<CheckmarxScanResult[]>(`/checkmarx/scans/${jobId}/results`);
  return response.data;
}

export async function downloadCheckmarxExcel(jobId: string): Promise<Blob> {
  const response = await apiClient.get(`/checkmarx/scans/${jobId}/export.xlsx`, {
    responseType: 'blob',
  });
  return response.data;
}
