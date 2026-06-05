import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Download, ExternalLink, FileArchive, Loader2, Play, RefreshCw, SearchCheck, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import {
  createCheckmarxScan,
  downloadCheckmarxExcel,
  getCheckmarxScan,
  listCheckmarxScanResults,
  listCheckmarxScans,
  type CheckmarxScanJob,
  type CheckmarxScanResult,
} from '@/features/checkmarx/services/checkmarxScan';

function errorMessage(error: unknown, fallback: string) {
  if (typeof error === 'object' && error && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail.map((item) => (typeof item === 'string' ? item : JSON.stringify(item))).join('；') || fallback;
    }
  }
  return error instanceof Error ? error.message : fallback;
}

function judgementLabel(value: boolean | null | undefined) {
  if (value === true) return '真实漏洞';
  if (value === false) return '误报';
  return '未知';
}

function judgementClass(value: boolean | null | undefined) {
  if (value === true) return 'border-red-200 bg-red-50 text-red-700';
  if (value === false) return 'border-emerald-200 bg-emerald-50 text-emerald-700';
  return 'border-slate-200 bg-slate-50 text-slate-600';
}

function formatJobTime(value?: string | null) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString();
}

export default function CheckmarxScan() {
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [projectName, setProjectName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState<CheckmarxScanJob | null>(null);
  const [scanJobs, setScanJobs] = useState<CheckmarxScanJob[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [results, setResults] = useState<CheckmarxScanResult[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [exporting, setExporting] = useState(false);

  const active = job?.status === 'pending' || job?.status === 'running';
  const stats = useMemo(() => {
    const real = results.filter((item) => item.ai_judgement === true).length;
    const falsePositive = results.filter((item) => item.ai_judgement === false).length;
    const unknown = results.length - real - falsePositive;
    return { total: results.length, real, falsePositive, unknown };
  }, [results]);

  const loadScanJobs = async (preferredJobId?: string) => {
    try {
      setLoadingJobs(true);
      const jobs = await listCheckmarxScans();
      setScanJobs(jobs);
      const nextId = preferredJobId || selectedJobId || jobs[0]?.id;
      if (nextId) {
        await selectJob(nextId, jobs);
      } else {
        setJob(null);
        setSelectedJobId(null);
        setResults([]);
      }
    } catch (error) {
      toast.error(errorMessage(error, '加载 Checkmarx 扫描任务失败'));
    } finally {
      setLoadingJobs(false);
    }
  };

  const selectJob = async (jobId: string, knownJobs = scanJobs) => {
    try {
      setRefreshing(true);
      const knownJob = knownJobs.find((item) => item.id === jobId);
      const nextJob = knownJob ? await getCheckmarxScan(jobId) : await getCheckmarxScan(jobId);
      setJob(nextJob);
      setSelectedJobId(jobId);
      const rows = await listCheckmarxScanResults(jobId);
      setResults(rows);
      setScanJobs((current) => current.map((item) => (item.id === jobId ? { ...item, ...nextJob } : item)));
    } catch (error) {
      toast.error(errorMessage(error, '加载 Checkmarx 扫描结果失败'));
    } finally {
      setRefreshing(false);
    }
  };

  const refreshJob = async (jobId = job?.id) => {
    if (!jobId) return;
    try {
      setRefreshing(true);
      const nextJob = await getCheckmarxScan(jobId);
      setJob(nextJob);
      setSelectedJobId(jobId);
      const rows = await listCheckmarxScanResults(jobId);
      setResults(rows);
      const jobs = await listCheckmarxScans();
      setScanJobs(jobs);
    } catch (error) {
      toast.error(errorMessage(error, '刷新 Checkmarx 扫描状态失败'));
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadScanJobs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!active || !job?.id) return;
    const timer = window.setInterval(() => {
      refreshJob(job.id);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [active, job?.id]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!zipFile) {
      toast.error('请选择 ZIP 文件');
      return;
    }
    if (!zipFile.name.toLowerCase().endsWith('.zip')) {
      toast.error('只能上传 ZIP 文件');
      return;
    }
    if (!projectName.trim() || !username.trim() || !password) {
      toast.error('请填写 Project Name、用户名和密码');
      return;
    }

    try {
      setSubmitting(true);
      setResults([]);
      const created = await createCheckmarxScan({
        file: zipFile,
        projectName: projectName.trim(),
        baseUrl,
        username: username.trim(),
        password,
      });
      setJob(created);
      setSelectedJobId(created.id);
      setScanJobs((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      toast.success('Checkmarx 扫描已提交');
    } catch (error) {
      toast.error(errorMessage(error, '提交 Checkmarx 扫描失败'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleExport = async () => {
    if (!job?.id) return;
    try {
      setExporting(true);
      const blob = await downloadCheckmarxExcel(job.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `checkmarx-${job.scan_id || job.id}.xlsx`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(errorMessage(error, '导出 Excel 失败'));
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="min-h-screen w-full min-w-0 bg-[linear-gradient(180deg,#f8fafc_0%,#f1f6f4_52%,#f8fafc_100%)]">
      <div className="mx-auto w-full max-w-none space-y-5">
        <section className="rounded-2xl border border-slate-200 bg-white/95 p-5 shadow-[0_18px_46px_rgba(15,23,42,0.06)]">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div className="flex items-center gap-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-emerald-200 bg-emerald-50 text-emerald-700">
                <SearchCheck className="h-6 w-6" />
              </div>
              <div>
                <h1 className="text-3xl font-bold tracking-normal text-slate-950">Checkmarx扫描</h1>
                <div className="mt-1 text-sm text-slate-500">SAST 上传、AI 降误报、结果导出</div>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
                <div className="text-xs text-slate-500">结果</div>
                <div className="mt-1 text-2xl font-bold text-slate-950">{stats.total}</div>
              </div>
              <div className="rounded-xl border border-red-100 bg-red-50 px-4 py-3">
                <div className="text-xs text-red-500">真实</div>
                <div className="mt-1 text-2xl font-bold text-red-700">{stats.real}</div>
              </div>
              <div className="rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3">
                <div className="text-xs text-emerald-600">误报</div>
                <div className="mt-1 text-2xl font-bold text-emerald-700">{stats.falsePositive}</div>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
                <div className="text-xs text-slate-500">未知</div>
                <div className="mt-1 text-2xl font-bold text-slate-700">{stats.unknown}</div>
              </div>
            </div>
          </div>
        </section>

        <section className="grid gap-5 xl:grid-cols-[420px_minmax(0,1fr)]">
          <form onSubmit={handleSubmit} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_14px_36px_rgba(15,23,42,0.05)]">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                <FileArchive className="h-4 w-4 text-emerald-700" />
                扫描配置
              </div>
              <Button type="submit" disabled={submitting || active} className="h-10 rounded-lg">
                {submitting || active ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                {active ? '运行中' : '开始扫描'}
              </Button>
            </div>

            <div className="space-y-2">
              <Label htmlFor="checkmarx-zip">源码 ZIP</Label>
              <Input
                id="checkmarx-zip"
                type="file"
                accept=".zip,application/zip"
                onChange={(event) => setZipFile(event.target.files?.[0] || null)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="checkmarx-project">Project Name</Label>
              <Input
                id="checkmarx-project"
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                placeholder="chartbrew-4.9.0"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="checkmarx-base-url">Checkmarx URL</Label>
              <Input
                id="checkmarx-base-url"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="留空使用后端环境变量"
              />
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="checkmarx-username">用户名</Label>
                <Input
                  id="checkmarx-username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  autoComplete="username"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="checkmarx-password">密码</Label>
                <Input
                  id="checkmarx-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                />
              </div>
            </div>
          </form>

          <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_14px_36px_rgba(15,23,42,0.05)]">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                <ShieldCheck className="h-4 w-4 text-emerald-700" />
                扫描状态
              </div>
              <div className="flex gap-2">
                <Button type="button" variant="outline" className="h-10 rounded-lg" disabled={!job || refreshing} onClick={() => refreshJob()}>
                  <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
                  刷新
                </Button>
                <Button type="button" variant="outline" className="h-10 rounded-lg" disabled={!job || exporting} onClick={handleExport}>
                  {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                  导出 Excel
                </Button>
              </div>
            </div>

            <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-slate-950">{job?.project_name || '未提交扫描'}</div>
                  <div className="mt-1 text-xs text-slate-500">{job?.current_step || '等待配置'}</div>
                </div>
                <div className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-semibold text-slate-700">
                  {job?.status || 'idle'}
                </div>
              </div>
              <Progress className="mt-4" value={job?.progress || 0} />
              {job?.error_message && (
                <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {job.error_message}
                </div>
              )}
            </div>
          </div>
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_14px_36px_rgba(15,23,42,0.05)]">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-slate-900">扫描任务记录</div>
              <div className="mt-1 text-xs text-slate-500">点击任务查看对应扫描状态和结果</div>
            </div>
            <Button type="button" variant="outline" className="h-10 rounded-lg" disabled={loadingJobs} onClick={() => loadScanJobs()}>
              <RefreshCw className={`h-4 w-4 ${loadingJobs ? 'animate-spin' : ''}`} />
              刷新任务
            </Button>
          </div>

          {scanJobs.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
              暂无扫描任务记录
            </div>
          ) : (
            <div className="grid gap-3 xl:grid-cols-2">
              {scanJobs.map((scanJob) => {
                const selected = selectedJobId === scanJob.id;
                const running = scanJob.status === 'pending' || scanJob.status === 'running';
                return (
                  <button
                    key={scanJob.id}
                    type="button"
                    onClick={() => selectJob(scanJob.id)}
                    className={`rounded-xl border p-4 text-left transition ${
                      selected
                        ? 'border-emerald-300 bg-emerald-50/80 shadow-[0_12px_26px_rgba(16,185,129,0.12)]'
                        : 'border-slate-200 bg-slate-50 hover:border-emerald-200 hover:bg-white'
                    }`}
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-slate-950">{scanJob.project_name}</div>
                        <div className="mt-1 truncate text-xs text-slate-500">{scanJob.source_filename}</div>
                      </div>
                      <span className={`rounded-lg border px-3 py-1 text-xs font-semibold ${running ? 'border-blue-200 bg-blue-50 text-blue-700' : selected ? 'border-emerald-200 bg-white text-emerald-700' : 'border-slate-200 bg-white text-slate-600'}`}>
                        {scanJob.status}
                      </span>
                    </div>
                    <Progress className="mt-3" value={scanJob.progress || 0} />
                    <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-500 sm:grid-cols-4">
                      <div>
                        <div className="text-slate-400">scan_id</div>
                        <div className="mt-1 font-mono text-slate-700">{scanJob.scan_id || '-'}</div>
                      </div>
                      <div>
                        <div className="text-slate-400">结果</div>
                        <div className="mt-1 font-semibold text-slate-700">{scanJob.results_count || 0} 条</div>
                      </div>
                      <div>
                        <div className="text-slate-400">进度</div>
                        <div className="mt-1 font-semibold text-slate-700">{scanJob.progress || 0}%</div>
                      </div>
                      <div>
                        <div className="text-slate-400">创建时间</div>
                        <div className="mt-1 text-slate-700">{formatJobTime(scanJob.created_at)}</div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_14px_36px_rgba(15,23,42,0.05)]">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div className="text-sm font-semibold text-slate-900">扫描结果</div>
            <div className="text-xs text-slate-500">{results.length} 条</div>
          </div>
          <Table className="text-sm">
            <TableHeader>
              <TableRow>
                <TableHead>scan_id</TableHead>
                <TableHead>path_id</TableHead>
                <TableHead>Vulnerability</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>URL</TableHead>
                <TableHead>AI判断</TableHead>
                <TableHead>AI判断原因</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {results.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="h-36 text-center text-sm text-slate-500">
                    暂无结果
                  </TableCell>
                </TableRow>
              ) : (
                results.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="whitespace-nowrap font-mono text-xs">{item.scan_id}</TableCell>
                    <TableCell className="whitespace-nowrap font-mono text-xs">{item.path_id}</TableCell>
                    <TableCell className="min-w-[220px] max-w-[360px] text-sm font-semibold text-slate-900">
                      {item.vulnerability}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm">{item.type}</TableCell>
                    <TableCell>
                      <a
                        className="inline-flex items-center gap-1 text-sm font-semibold text-emerald-700 hover:text-emerald-900"
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        打开
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    </TableCell>
                    <TableCell>
                      <span className={`inline-flex min-w-[72px] justify-center rounded-md border px-2 py-1 text-xs font-semibold ${judgementClass(item.ai_judgement)}`}>
                        {judgementLabel(item.ai_judgement)}
                      </span>
                    </TableCell>
                    <TableCell className="min-w-[320px] max-w-[560px] text-sm leading-6 text-slate-600">
                      {item.ai_reason || 'N/A'}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </section>
      </div>
    </div>
  );
}
