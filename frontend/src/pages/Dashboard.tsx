/**
 * Dashboard Page
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  Bot,
  Calendar,
  CheckCircle2,
  Clock,
  Code,
  FileText,
  FolderGit2,
  GitBranch,
  Shield,
  ShieldAlert,
  Wrench,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api, isDemoMode } from "@/shared/config/database";
import type { AuditTask, Project, ProjectStats, UnifiedTask } from "@/shared/types";
import { getAgentTasks, type AgentTask } from "@/shared/api/agentTasks";
import { listVulnerabilities, type ManagedVulnerability } from "@/shared/api/vulnerabilities";

const runningStatuses = new Set([
  "running",
  "initializing",
  "planning",
  "indexing",
  "analyzing",
  "verifying",
  "reporting",
]);

export default function Dashboard() {
  const [stats, setStats] = useState<ProjectStats | null>(null);
  const [recentProjects, setRecentProjects] = useState<Project[]>([]);
  const [recentTasks, setRecentTasks] = useState<UnifiedTask[]>([]);
  const [vulnerabilities, setVulnerabilities] = useState<ManagedVulnerability[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDashboardData();
  }, []);

  const loadDashboardData = async () => {
    try {
      setLoading(true);

      const results = await Promise.allSettled([
        api.getProjectStats(),
        api.getProjects(),
        api.getAuditTasks(),
        getAgentTasks({ limit: 10 }),
        listVulnerabilities({ skip: 0, limit: 500 }),
      ]);

      if (results[0].status === "fulfilled") {
        setStats(results[0].value);
      } else {
        setStats({
          total_projects: 0,
          active_projects: 0,
          total_tasks: 0,
          completed_tasks: 0,
          total_issues: 0,
          resolved_issues: 0,
          avg_quality_score: 0,
        });
      }

      if (results[1].status === "fulfilled") {
        setRecentProjects(Array.isArray(results[1].value) ? results[1].value.slice(0, 6) : []);
      } else {
        setRecentProjects([]);
      }

      const tasks: AuditTask[] =
        results[2].status === "fulfilled" && Array.isArray(results[2].value) ? results[2].value : [];

      const agentTasksList: AgentTask[] =
        results[3].status === "fulfilled" && Array.isArray(results[3].value) ? results[3].value : [];

      const unified: UnifiedTask[] = [
        ...tasks.map((task) => ({ kind: "audit" as const, task })),
        ...agentTasksList.map((task) => ({ kind: "agent" as const, task })),
      ];
      unified.sort((a, b) => new Date(b.task.created_at).getTime() - new Date(a.task.created_at).getTime());
      setRecentTasks(unified.slice(0, 10));

      if (results[4].status === "fulfilled") {
        setVulnerabilities(Array.isArray(results[4].value) ? results[4].value : []);
      } else {
        setVulnerabilities([]);
      }
    } catch (error) {
      console.error("仪表盘数据加载失败:", error);
      toast.error("数据加载失败");
    } finally {
      setLoading(false);
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "completed":
        return <Badge className="cyber-badge-success">完成</Badge>;
      case "running":
      case "initializing":
      case "planning":
      case "indexing":
      case "analyzing":
      case "verifying":
      case "reporting":
        return <Badge className="cyber-badge-info">运行中</Badge>;
      case "failed":
        return <Badge className="cyber-badge-danger">失败</Badge>;
      case "cancelled":
        return <Badge className="cyber-badge-muted">已取消</Badge>;
      case "paused":
        return <Badge className="cyber-badge-muted">已暂停</Badge>;
      default:
        return <Badge className="cyber-badge-muted">待处理</Badge>;
    }
  };

  const pendingIssues = stats ? Math.max(stats.total_issues - stats.resolved_issues, 0) : 0;
  const runningTaskCount = recentTasks.filter((item) => runningStatuses.has(item.task.status)).length;
  const completedTaskCount = stats?.completed_tasks || 0;
  const failedTaskCount = recentTasks.filter((item) => item.task.status === "failed").length;
  const queuedTaskCount = Math.max((stats?.total_tasks || 0) - completedTaskCount - runningTaskCount - failedTaskCount, 0);
  const riskCounts = buildRiskCounts(vulnerabilities);
  const highRiskTotal = riskCounts.critical + riskCounts.high;
  const totalRisk = vulnerabilities.length || stats?.total_issues || 0;
  const unclassifiedRisk = Math.max(
    totalRisk - riskCounts.critical - riskCounts.high - riskCounts.medium - riskCounts.low,
    0
  );

  const summaryCards = [
    {
      label: "总项目数",
      value: stats?.total_projects || 0,
      detail: `活跃: ${stats?.active_projects || 0}`,
      icon: Code,
      tone: "text-[hsl(var(--primary))]",
      dot: "bg-emerald-500",
    },
    {
      label: "审计任务",
      value: stats?.total_tasks || 0,
      detail: `已完成: ${stats?.completed_tasks || 0}`,
      icon: Activity,
      tone: "text-emerald-600",
      dot: "bg-emerald-500",
    },
    {
      label: "发现问题",
      value: stats?.total_issues || 0,
      detail: `待解决: ${pendingIssues}`,
      icon: AlertTriangle,
      tone: "text-amber-600",
      dot: "bg-amber-500",
    },
  ];

  if (loading) {
    return (
      <div className="relative flex min-h-screen items-center justify-center overflow-hidden cyber-bg-elevated">
        <div className="absolute inset-0 cyber-grid-subtle pointer-events-none" />
        <div className="relative z-10 space-y-4 text-center">
          <div className="loading-spinner mx-auto" />
          <p className="text-base text-muted-foreground">加载数据中...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative min-h-screen overflow-hidden cyber-bg-elevated p-6">
      <div className="absolute inset-0 cyber-grid-subtle pointer-events-none" />

      <div className="relative z-10 mx-auto max-w-[1500px] space-y-5">
        <section className="overflow-hidden rounded-[30px] border border-[#d6e4da] bg-[linear-gradient(180deg,#ffffff,#f4faf6)] shadow-[0_24px_60px_rgba(96,120,101,0.08)]">
          <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_360px]">
            <div className="relative min-h-[230px] px-6 py-6 lg:px-8 lg:py-7">
              <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,#e4f1e8,#ffffff_60%)]" />
              <div className="relative flex h-full flex-col justify-between gap-8">
                <div className="space-y-4">
                  <span className="inline-flex w-fit items-center rounded-full border border-[#c7decf] bg-[#eaf4ed] px-3 py-1 text-xs font-semibold text-[hsl(var(--primary))]">
                    AIAudit Workspace
                  </span>
                  <div className="space-y-2">
                    <h1 className="text-3xl font-semibold text-slate-900 md:text-4xl">工作台</h1>
                    <p className="max-w-2xl text-sm leading-7 text-slate-600 md:text-base">
                      统一查看项目、审计任务与漏洞发现。
                    </p>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-3 text-sm text-slate-500">
                  <span className="inline-flex items-center gap-2 rounded-full border border-[#e2e8f0] bg-white px-3 py-2 shadow-sm">
                    <Calendar className="h-4 w-4 text-slate-400" />
                    {new Date().toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric" })}
                  </span>
                  <span className="inline-flex items-center gap-2 rounded-full border border-[#c7decf] bg-[#f6fbf7] px-3 py-2 shadow-sm">
                    <Shield className="h-4 w-4 text-[hsl(var(--primary))]" />
                    核心审计入口
                  </span>
                </div>
              </div>
            </div>

            <div className="border-t border-[#d6e4da] bg-white p-5 xl:border-l xl:border-t-0">
              <div className="flex h-full flex-col justify-between rounded-[24px] border border-[#dbe7df] bg-[#f8fbf9] p-5">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-semibold text-slate-500">审计运行态势</p>
                    <p className="mt-1 text-2xl font-bold text-slate-900">{stats?.total_tasks || 0}</p>
                  </div>
                  <span className="flex h-12 w-12 items-center justify-center rounded-[18px] border border-[#dbe7df] bg-white text-[hsl(var(--primary))]">
                    <Activity className="h-5 w-5" />
                  </span>
                </div>
                <div className="mt-6 grid grid-cols-2 gap-3 text-sm">
                  <StatusPill label="已完成" value={completedTaskCount} tone="text-emerald-700" />
                  <StatusPill label="运行中" value={runningTaskCount} tone="text-sky-700" />
                  <StatusPill label="失败" value={failedTaskCount} tone="text-rose-700" />
                  <StatusPill label="待处理" value={queuedTaskCount} tone="text-slate-700" />
                </div>
              </div>
            </div>
          </div>
        </section>

        {isDemoMode && (
          <div className="rounded-[24px] border border-amber-300 bg-amber-50 p-4 shadow-[0_12px_28px_rgba(160,115,48,0.07)]">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 text-amber-600" />
              <div className="text-sm text-slate-700">
                当前使用<span className="font-bold text-amber-700">演示模式</span>，显示的是模拟数据。
                <Link to="/admin" className="ml-2 font-bold text-[hsl(var(--primary))] hover:underline">
                  前往配置
                </Link>
              </div>
            </div>
          </div>
        )}

        <section className="grid gap-4 md:grid-cols-3">
          {summaryCards.map((card) => {
            const Icon = card.icon;
            return (
              <div
                key={card.label}
                className="rounded-[26px] border border-[#dbe7df] bg-white p-5 shadow-[0_18px_44px_rgba(84,110,93,0.07)]"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-slate-500">{card.label}</p>
                    <p className="mt-2 text-3xl font-bold leading-none text-slate-900">{card.value}</p>
                    <p className="mt-3 flex items-center gap-2 text-sm text-slate-500">
                      <span className={`h-2 w-2 rounded-full ${card.dot}`} />
                      {card.detail}
                    </p>
                  </div>
                  <span className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-[18px] border border-slate-200/70 bg-slate-50 ${card.tone}`}>
                    <Icon className="h-5 w-5" />
                  </span>
                </div>
              </div>
            );
          })}
        </section>

        <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="space-y-5">
            <div className="rounded-[28px] border border-[#dbe7df] bg-white p-5 shadow-[0_18px_48px_rgba(84,110,93,0.08)]">
              <div className="section-header">
                <FileText className="h-5 w-5 text-[hsl(var(--primary))]" />
                <h3 className="section-title">项目概览</h3>
                <Link to="/projects" className="ml-auto">
                  <Button variant="ghost" size="sm" className="rounded-full text-muted-foreground hover:text-foreground">
                    查看全部 <ArrowUpRight className="ml-1 h-3 w-3" />
                  </Button>
                </Link>
              </div>
              <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
                {recentProjects.length > 0 ? (
                  recentProjects.map((project) => (
                    <Link
                      key={project.id}
                      to={`/projects/${project.id}`}
                      className="group rounded-[22px] border border-[#dce3e0] bg-[#f8fbf9] p-4 transition hover:border-[#b7d1c1] hover:bg-white"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <h4 className="min-w-0 truncate text-base font-semibold text-slate-800 transition group-hover:text-[hsl(var(--primary))]">
                          {project.name}
                        </h4>
                        <Badge className={`shrink-0 ${project.is_active ? "cyber-badge-success" : "cyber-badge-muted"}`}>
                          {project.is_active ? "活跃" : "暂停"}
                        </Badge>
                      </div>
                      <p className="mt-3 line-clamp-2 min-h-[48px] text-sm leading-6 text-slate-500">
                        {project.description || "暂无描述"}
                      </p>
                      <div className="mt-4 flex items-center text-sm text-slate-500">
                        <Calendar className="mr-2 h-4 w-4" />
                        {new Date(project.created_at).toLocaleDateString("zh-CN")}
                      </div>
                    </Link>
                  ))
                ) : (
                  <div className="col-span-full empty-state py-12">
                    <Code className="empty-state-icon" />
                    <p className="empty-state-title">暂无项目</p>
                    <p className="empty-state-description">创建您的第一个项目并进入审计流程</p>
                  </div>
                )}
              </div>
            </div>

            <div className="rounded-[28px] border border-[#dbe7df] bg-white p-5 shadow-[0_18px_48px_rgba(84,110,93,0.08)]">
              <div className="section-header">
                <Clock className="h-5 w-5 text-emerald-600" />
                <h3 className="section-title">最近任务</h3>
                <Link to="/audit-tasks" className="ml-auto">
                  <Button variant="ghost" size="sm" className="rounded-full text-muted-foreground hover:text-foreground">
                    查看全部 <ArrowUpRight className="ml-1 h-3 w-3" />
                  </Button>
                </Link>
              </div>
              <div className="space-y-3">
                {recentTasks.length > 0 ? (
                  recentTasks.slice(0, 6).map((unified) => {
                    const isAgent = unified.kind === "agent";
                    const task = unified.task;
                    const taskLink = isAgent ? `/agent-audit/${task.id}` : `/tasks/${task.id}`;
                    const taskName = isAgent
                      ? (task as AgentTask).name || "未知项目"
                      : (task as AuditTask).project?.name || "未知项目";
                    const score = task.quality_score?.toFixed(1) || "0.0";
                    const isRunning = runningStatuses.has(task.status);
                    const isCompleted = task.status === "completed";

                    return (
                      <Link
                        key={`${unified.kind}-${task.id}`}
                        to={taskLink}
                        className="group grid gap-3 rounded-[22px] border border-transparent bg-[#f8fbf9] p-4 transition hover:border-[#b7d1c1] hover:bg-white md:grid-cols-[minmax(0,1fr)_auto]"
                      >
                        <div className="flex min-w-0 items-center gap-3">
                          <span
                            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl ${
                              isCompleted
                                ? "bg-emerald-100 text-emerald-700"
                                : isRunning
                                  ? "bg-sky-100 text-sky-700"
                                  : "bg-rose-100 text-rose-700"
                            }`}
                          >
                            {isAgent ? <Bot className="h-4 w-4" /> : isCompleted ? <CheckCircle2 className="h-4 w-4" /> : isRunning ? <Clock className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
                          </span>
                          <div className="min-w-0">
                            <p className="truncate text-base font-semibold text-slate-800 transition group-hover:text-[hsl(var(--primary))]">
                              {taskName}
                            </p>
                            <p className="mt-1 text-sm text-slate-500">
                              质量分: <span className="font-semibold text-slate-700">{score}</span>
                              {isAgent && <span className="ml-2 rounded-full bg-violet-50 px-2 py-0.5 text-xs font-semibold text-violet-600">Agent</span>}
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center justify-start md:justify-end">{getStatusBadge(task.status)}</div>
                      </Link>
                    );
                  })
                ) : (
                  <div className="empty-state py-12">
                    <Activity className="empty-state-icon" />
                    <p className="empty-state-title">暂无任务</p>
                  </div>
                )}
              </div>
            </div>
          </div>

          <aside className="space-y-5">
            <div className="rounded-[28px] border border-[#dbe7df] bg-white p-5 shadow-[0_18px_48px_rgba(84,110,93,0.08)]">
              <div className="section-header">
                <ShieldAlert className="h-5 w-5 text-rose-600" />
                <h3 className="section-title">漏洞风险概览</h3>
                <Link to="/vulnerabilities" className="ml-auto">
                  <Button variant="ghost" size="sm" className="rounded-full text-muted-foreground hover:text-foreground">
                    查看全部 <ArrowUpRight className="ml-1 h-3 w-3" />
                  </Button>
                </Link>
              </div>
              <div className="rounded-[24px] border border-[#ead5d5] bg-[linear-gradient(180deg,#fffafa,#fff)] p-4">
                <div className="flex items-end justify-between">
                  <div>
                    <p className="text-sm font-semibold text-slate-500">高风险待关注</p>
                    <p className="mt-2 text-4xl font-bold leading-none text-slate-950">{highRiskTotal}</p>
                  </div>
                  <Badge className="rounded-full border border-rose-200 bg-rose-50 text-rose-700">
                    共 {totalRisk} 项
                  </Badge>
                </div>
                <div className="mt-5 space-y-3">
                  <RiskBar label="Critical" value={riskCounts.critical} total={totalRisk} barClassName="bg-rose-600" />
                  <RiskBar label="High" value={riskCounts.high} total={totalRisk} barClassName="bg-orange-500" />
                  <RiskBar label="Medium" value={riskCounts.medium} total={totalRisk} barClassName="bg-amber-400" />
                  <RiskBar label="Low" value={riskCounts.low} total={totalRisk} barClassName="bg-emerald-500" />
                  <RiskBar label="Unclassified" value={unclassifiedRisk} total={totalRisk} barClassName="bg-slate-400" />
                </div>
              </div>
            </div>

            <div className="rounded-[28px] border border-[#dbe7df] bg-white p-5 shadow-[0_18px_48px_rgba(84,110,93,0.08)]">
              <div className="section-header">
                <Zap className="h-5 w-5 text-[hsl(var(--primary))]" />
                <h3 className="section-title">快速操作</h3>
              </div>
              <div className="space-y-2">
                <Link to="/projects" className="block">
                  <Button variant="outline" className="h-11 w-full justify-start rounded-full cyber-btn-outline">
                    <GitBranch className="mr-2 h-4 w-4" />
                    创建新项目
                  </Button>
                </Link>
                <Link to="/skills" className="block">
                  <Button variant="outline" className="h-11 w-full justify-start rounded-full cyber-btn-outline">
                    <Wrench className="mr-2 h-4 w-4" />
                    安装Skill
                  </Button>
                </Link>
                <Link to="/audit-tasks" className="block">
                  <Button variant="outline" className="h-11 w-full justify-start rounded-full cyber-btn-outline">
                    <Shield className="mr-2 h-4 w-4" />
                    查看审计任务
                  </Button>
                </Link>
                <Link to="/vulnerabilities" className="block">
                  <Button variant="outline" className="h-11 w-full justify-start rounded-full cyber-btn-outline">
                    <ShieldAlert className="mr-2 h-4 w-4" />
                    查看已发现漏洞
                  </Button>
                </Link>
              </div>
            </div>

            <div className="rounded-[28px] border border-[#dbe7df] bg-white p-5 shadow-[0_18px_48px_rgba(84,110,93,0.08)]">
              <div className="section-header">
                <Activity className="h-5 w-5 text-amber-600" />
                <h3 className="section-title">最新活动</h3>
              </div>
              <div className="space-y-2">
                {recentTasks.length > 0 ? (
                  recentTasks.slice(0, 3).map((unified) => {
                    const isAgent = unified.kind === "agent";
                    const task = unified.task;
                    const taskLink = isAgent ? `/agent-audit/${task.id}` : `/tasks/${task.id}`;
                    const isRunning = runningStatuses.has(task.status);
                    const isCompleted = task.status === "completed";
                    const isFailed = task.status === "failed";
                    const taskName = isAgent
                      ? (task as AgentTask).name || "未知项目"
                      : (task as AuditTask).project?.name || "未知项目";
                    const issuesCount = isAgent ? (task as AgentTask).findings_count : (task as AuditTask).issues_count;
                    const statusText = isAgent
                      ? isCompleted
                        ? "Agent任务完成"
                        : isRunning
                          ? "Agent任务运行中"
                          : isFailed
                            ? "Agent任务失败"
                            : "Agent任务待处理"
                      : isCompleted
                        ? "任务完成"
                        : isRunning
                          ? "任务运行中"
                          : isFailed
                            ? "任务失败"
                            : "任务待处理";

                    return (
                      <Link
                        key={`${unified.kind}-${task.id}`}
                        to={taskLink}
                        className={`block rounded-[20px] border p-3 transition ${
                          isCompleted
                            ? "border-emerald-200 bg-emerald-50 hover:border-emerald-300"
                            : isRunning
                              ? "border-sky-200 bg-sky-50 hover:border-sky-300"
                              : isFailed
                                ? "border-rose-200 bg-rose-50 hover:border-rose-300"
                                : "border-slate-200 bg-slate-50 hover:border-slate-300"
                        }`}
                      >
                        <p className="text-sm font-semibold text-slate-800">{statusText}</p>
                        <p className="mt-1 line-clamp-1 text-sm text-slate-500">
                          项目 "{taskName}"
                          {isCompleted && issuesCount > 0 ? ` - 发现 ${issuesCount} 个问题` : ""}
                        </p>
                        <p className="mt-1 text-xs text-slate-400">{formatTimeAgo(task.created_at)}</p>
                      </Link>
                    );
                  })
                ) : (
                  <div className="empty-state py-8">
                    <Clock className="mb-2 h-10 w-10 text-muted-foreground" />
                    <p className="text-base text-muted-foreground">暂无活动记录</p>
                  </div>
                )}
              </div>
            </div>
          </aside>
        </section>
      </div>
    </div>
  );
}

function StatusPill({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
  tone: string;
}) {
  return (
    <div className="rounded-2xl border border-[#dbe7df] bg-white px-3 py-3">
      <div className={`text-xl font-bold leading-none ${tone}`}>{value}</div>
      <div className="mt-1 text-xs font-semibold text-slate-500">{label}</div>
    </div>
  );
}

function RiskBar({
  label,
  value,
  total,
  barClassName,
}: {
  label: string;
  value: number;
  total: number;
  barClassName: string;
}) {
  const percent = total > 0 ? Math.max((value / total) * 100, value > 0 ? 8 : 0) : 0;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs font-semibold">
        <span className="text-slate-500">{label}</span>
        <span className="text-slate-900">{value}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
        <div className={`h-full rounded-full ${barClassName}`} style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function buildRiskCounts(vulnerabilities: ManagedVulnerability[]) {
  return vulnerabilities.reduce(
    (acc, item) => {
      const severity = item.severity.toLowerCase();
      if (severity.includes("critical")) acc.critical += 1;
      else if (severity.includes("high")) acc.high += 1;
      else if (severity.includes("medium")) acc.medium += 1;
      else if (severity.includes("low")) acc.low += 1;
      else acc.other += 1;
      return acc;
    },
    { critical: 0, high: 0, medium: 0, low: 0, other: 0 }
  );
}

function formatTimeAgo(createdAt: string) {
  const now = new Date();
  const taskDate = new Date(createdAt);
  const diffMs = now.getTime() - taskDate.getTime();
  const diffMins = Math.max(Math.floor(diffMs / 60000), 0);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 60) return `${diffMins}分钟前`;
  if (diffHours < 24) return `${diffHours}小时前`;
  return `${diffDays}天前`;
}
