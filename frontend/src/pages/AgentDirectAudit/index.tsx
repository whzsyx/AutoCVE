import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  FileCode2,
  FileSearch,
  Folder,
  FolderOpen,
  Loader2,
  MessageSquareText,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  SendHorizonal,
  ShieldAlert,
  Sparkles,
  Workflow,
  Wrench,
} from "lucide-react";
import { toast } from "sonner";
import { Link, useSearchParams } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { AuditTimeline } from "@/pages/AuditSession/components/AuditTimeline";
import { FindingsSidebar } from "@/pages/AuditSession/components/FindingsSidebar";
import { FollowUpComposer } from "@/pages/AuditSession/components/FollowUpComposer";
import { HandoffTracePanel } from "@/pages/AuditSession/components/HandoffTracePanel";
import { MemoryTracePanel } from "@/pages/AuditSession/components/MemoryTracePanel";
import { SkillTracePanel } from "@/pages/AuditSession/components/SkillTracePanel";
import { ToolTracePanel } from "@/pages/AuditSession/components/ToolTracePanel";
import { useAuditSession } from "@/pages/AuditSession/hooks/useAuditSession";
import { useAuditSessionChatStream } from "@/pages/AuditSession/hooks/useAuditSessionChatStream";
import { useAuditSessionStream } from "@/pages/AuditSession/hooks/useAuditSessionStream";
import {
  type DirectAuditApprovalScope,
  listDirectAuditManagedVulnerabilities,
  listDirectAuditSessions,
  streamApproveDirectAuditToolCall,
  streamCreateDirectAuditSession,
  streamDirectAuditSessionMessage,
  syncLatestDirectAuditManagedVulnerability,
  updateDirectAuditGuardrails,
} from "@/shared/api/agentDirectAudit";
import type { AuditSessionMessage, AuditSessionStreamEvent } from "@/shared/api/auditSessions";
import type { ManagedVulnerability } from "@/shared/api/vulnerabilities";
import { api } from "@/shared/config/database";
import type { Project, ProjectFileContent } from "@/shared/types";
import { getLatestDirectAuditReportMessage, getSyncedDirectAuditMessageIds } from "@/shared/utils/directAuditReports";
import { isLocalDirectoryProject } from "@/shared/utils/projectUtils";

type FileEntry = { path: string; size: number };

type TreeNode = {
  name: string;
  path: string;
  type: "directory" | "file";
  children: TreeNode[];
};

const LEFT_COLLAPSED_STORAGE_KEY = "agent-direct-audit:left-collapsed";
const AUTO_SYNC_REPORTS_STORAGE_KEY = "agent-direct-audit:auto-sync-managed-reports";

const panelClass =
  "overflow-hidden rounded-[24px] border border-[rgba(210,219,228,.85)] bg-white/92 shadow-[0_18px_44px_rgba(15,23,42,.06)] backdrop-blur";

function buildFileTree(files: FileEntry[]): TreeNode[] {
  const root: TreeNode[] = [];

  function upsertChild(children: TreeNode[], node: TreeNode): TreeNode {
    const existing = children.find((child) => child.name === node.name && child.type === node.type);
    if (existing) {
      return existing;
    }
    children.push(node);
    return node;
  }

  for (const file of files) {
    const segments = file.path.split("/").filter(Boolean);
    let currentChildren = root;
    let currentPath = "";

    segments.forEach((segment, index) => {
      currentPath = currentPath ? `${currentPath}/${segment}` : segment;
      const isLeaf = index === segments.length - 1;
      const node = upsertChild(currentChildren, {
        name: segment,
        path: currentPath,
        type: isLeaf ? "file" : "directory",
        children: [],
      });
      currentChildren = node.children;
    });
  }

  function sortNodes(nodes: TreeNode[]): TreeNode[] {
    return [...nodes]
      .map((node) => ({
        ...node,
        children: sortNodes(node.children),
      }))
      .sort((left, right) => {
        if (left.type !== right.type) {
          return left.type === "directory" ? -1 : 1;
        }
        return left.name.localeCompare(right.name);
      });
  }

  return sortNodes(root);
}

function upsertMessage(messages: AuditSessionMessage[], nextMessage: AuditSessionMessage): AuditSessionMessage[] {
  const index = messages.findIndex((message) => message.id === nextMessage.id);
  if (index === -1) {
    return [...messages, nextMessage].sort((left, right) => left.sequence - right.sequence);
  }
  const clone = [...messages];
  clone[index] = nextMessage;
  return clone;
}

function FileTreeNode({
  node,
  depth = 0,
  selectedPath,
  onSelect,
}: {
  node: TreeNode;
  depth?: number;
  selectedPath: string;
  onSelect: (path: string) => void;
}) {
  const [open, setOpen] = useState(depth < 2);

  if (node.type === "file") {
    const isSelected = node.path === selectedPath;
    return (
      <button
        type="button"
        onClick={() => onSelect(node.path)}
        className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm transition ${
          isSelected
            ? "bg-[rgba(15,23,42,.08)] text-slate-950"
            : "text-slate-600 hover:bg-[rgba(15,23,42,.04)]"
        }`}
        style={{ paddingLeft: `${depth * 14 + 12}px` }}
      >
        <FileCode2 className={`h-4 w-4 ${isSelected ? "text-slate-900" : "text-slate-400"}`} />
        <span className="truncate">{node.name}</span>
      </button>
    );
  }

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm font-medium text-slate-700 hover:bg-[rgba(15,23,42,.04)]"
        style={{ paddingLeft: `${depth * 14 + 12}px` }}
      >
        {open ? <FolderOpen className="h-4 w-4 text-slate-700" /> : <Folder className="h-4 w-4 text-slate-400" />}
        <span className="truncate">{node.name}</span>
      </button>
      {open ? node.children.map((child) => (
        <FileTreeNode key={child.path} node={child} depth={depth + 1} selectedPath={selectedPath} onSelect={onSelect} />
      )) : null}
    </div>
  );
}

function formatSessionTime(value?: string) {
  if (!value) {
    return "刚刚";
  }

  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function buildPreviewLines(filePreview: ProjectFileContent | null): Array<{ number: number; content: string }> {
  if (!filePreview) {
    return [];
  }

  return filePreview.content.split(/\r?\n/).map((line, index) => ({
    number: index + 1,
    content: line,
  }));
}

function formatManagedSeverity(value?: string | null) {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "critical") {
    return "严重";
  }
  if (normalized === "high") {
    return "高危";
  }
  if (normalized === "medium") {
    return "中危";
  }
  if (normalized === "low") {
    return "低危";
  }
  if (normalized === "info") {
    return "提示";
  }
  return value || "未知";
}

export default function AgentDirectAuditPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [sessionIds, setSessionIds] = useState<Array<{ id: string; updated_at: string; state: string }>>([]);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [projectFiles, setProjectFiles] = useState<FileEntry[]>([]);
  const [selectedFilePath, setSelectedFilePath] = useState("");
  const [filePreview, setFilePreview] = useState<ProjectFileContent | null>(null);
  const [filePreviewLoading, setFilePreviewLoading] = useState(false);
  const [filePreviewError, setFilePreviewError] = useState<string | null>(null);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [starterPrompt, setStarterPrompt] = useState("");
  const [createGuardrailsEnabled, setCreateGuardrailsEnabled] = useState(false);
  const [creatingSession, setCreatingSession] = useState(false);
  const [createStreamError, setCreateStreamError] = useState<string | null>(null);
  const [approvalToolCallId, setApprovalToolCallId] = useState<string | null>(null);
  const [guardrailUpdating, setGuardrailUpdating] = useState(false);
  const [managedVulnerabilities, setManagedVulnerabilities] = useState<ManagedVulnerability[]>([]);
  const [managedVulnerabilitiesLoading, setManagedVulnerabilitiesLoading] = useState(false);
  const [syncingManagedVulnerabilities, setSyncingManagedVulnerabilities] = useState(false);
  const [autoSyncManagedReports, setAutoSyncManagedReports] = useState(false);
  const createAbortRef = useRef<AbortController | null>(null);
  const createStreamingAssistantIdRef = useRef<string | null>(null);
  const lastAutoSyncKeyRef = useRef<string | null>(null);

  const selectedProject = projects.find((project) => project.id === selectedProjectId) || null;
  const queryProjectId = searchParams.get("projectId") || "";
  const querySessionId = searchParams.get("sessionId") || "";
  const queryFilePath = searchParams.get("file") || "";
  const {
    session,
    messages,
    setMessages,
    toolCalls,
    skills,
    skillInvocations,
    memories,
    handoffs,
    loading,
    error,
    refresh,
  } = useAuditSession(selectedSessionId || undefined);
  const { isStreaming, streamError, runStreamRequest, sendMessage, stopStreaming, streamingAssistantId } =
    useAuditSessionChatStream({
      sessionId: selectedSessionId || undefined,
      setMessages,
      refresh,
      streamMessage: streamDirectAuditSessionMessage,
    });

  const fileTree = useMemo(() => buildFileTree(projectFiles), [projectFiles]);
  const previewLines = useMemo(() => buildPreviewLines(filePreview), [filePreview]);
  const guardrailsEnabled = selectedSessionId ? Boolean(session?.guardrails_enabled) : createGuardrailsEnabled;
  const latestReportMatch = useMemo(() => getLatestDirectAuditReportMessage(messages), [messages]);
  const syncedDirectAuditMessageIds = useMemo(
    () => getSyncedDirectAuditMessageIds(managedVulnerabilities),
    [managedVulnerabilities],
  );
  const latestReportMessageId = latestReportMatch?.message.id || null;
  const latestReportSynced = latestReportMessageId ? syncedDirectAuditMessageIds.has(latestReportMessageId) : false;
  const hasLatestReport = Boolean(latestReportMessageId);
  const syncActionDisabled = syncingManagedVulnerabilities || !latestReportMessageId || latestReportSynced;
  const syncActionLabel = !hasLatestReport
    ? "等待报告"
    : latestReportSynced
      ? "最新报告已同步"
      : syncingManagedVulnerabilities
        ? "同步中..."
        : "同步最近报告";
  const timelineStreaming = creatingSession || isStreaming;
  const timelineError = createStreamError || streamError || error;
  const timelineStreamingAssistantId = createStreamingAssistantIdRef.current || streamingAssistantId;
  const sessionFailed = session?.state === "failed";

  useAuditSessionStream(
    () => refresh({ silent: true }),
    Boolean(selectedSessionId && session?.state === "running" && !isStreaming),
  );

  useEffect(() => {
    const saved = window.localStorage.getItem(LEFT_COLLAPSED_STORAGE_KEY);
    if (saved === "true") {
      setLeftCollapsed(true);
    }
    if (window.localStorage.getItem(AUTO_SYNC_REPORTS_STORAGE_KEY) === "true") {
      setAutoSyncManagedReports(true);
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(LEFT_COLLAPSED_STORAGE_KEY, String(leftCollapsed));
  }, [leftCollapsed]);

  useEffect(() => {
    window.localStorage.setItem(AUTO_SYNC_REPORTS_STORAGE_KEY, String(autoSyncManagedReports));
  }, [autoSyncManagedReports]);

  useEffect(() => {
    return () => {
      createAbortRef.current?.abort();
      createAbortRef.current = null;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadProjects() {
      setProjectsLoading(true);
      try {
        const data = await api.getProjects();
        if (cancelled) {
          return;
        }
        setProjects(data);
        if (!selectedProjectId && data.length > 0) {
          const preferred =
            data.find((project) => project.id === queryProjectId) ||
            data.find((project) => isLocalDirectoryProject(project)) ||
            data[0];
          setSelectedProjectId(preferred.id);
        }
      } catch (loadError) {
        if (!cancelled) {
          toast.error(loadError instanceof Error ? loadError.message : "加载项目失败");
        }
      } finally {
        if (!cancelled) {
          setProjectsLoading(false);
        }
      }
    }

    void loadProjects();
    return () => {
      cancelled = true;
    };
  }, [queryProjectId, selectedProjectId]);

  useEffect(() => {
    if (!selectedProjectId) {
      setSessionIds([]);
      setSelectedSessionId("");
      setProjectFiles([]);
      setSelectedFilePath("");
      setFilePreview(null);
      setFilePreviewError(null);
      return;
    }

    let cancelled = false;

    async function loadWorkspace() {
      try {
        const [sessions, files] = await Promise.all([
          listDirectAuditSessions(selectedProjectId),
          api.getProjectFiles(selectedProjectId),
        ]);
        if (cancelled) {
          return;
        }

        setSessionIds(
          sessions.map((item) => ({
            id: item.id,
            updated_at: item.updated_at,
            state: item.state,
          })),
        );
        setProjectFiles(files);

        if (!sessions.some((item) => item.id === selectedSessionId)) {
          const preferredSessionId = sessions.find((item) => item.id === querySessionId)?.id || sessions[0]?.id || "";
          setSelectedSessionId(preferredSessionId);
        }

        const nextFilePath = [selectedFilePath, queryFilePath].find((candidate) =>
          candidate ? files.some((file) => file.path === candidate) : false,
        );
        setSelectedFilePath(nextFilePath || "");
      } catch (workspaceError) {
        if (!cancelled) {
          toast.error(workspaceError instanceof Error ? workspaceError.message : "加载 Agent直审 工作台失败");
        }
      }
    }

    void loadWorkspace();
    return () => {
      cancelled = true;
    };
  }, [queryFilePath, querySessionId, selectedProjectId, selectedSessionId]);

  useEffect(() => {
    const nextParams = new URLSearchParams(searchParams);
    if (selectedProjectId) {
      nextParams.set("projectId", selectedProjectId);
    } else {
      nextParams.delete("projectId");
    }
    if (selectedSessionId) {
      nextParams.set("sessionId", selectedSessionId);
    } else {
      nextParams.delete("sessionId");
    }
    if (selectedFilePath) {
      nextParams.set("file", selectedFilePath);
    } else {
      nextParams.delete("file");
    }

    if (nextParams.toString() !== searchParams.toString()) {
      setSearchParams(nextParams, { replace: true });
    }
  }, [searchParams, selectedFilePath, selectedProjectId, selectedSessionId, setSearchParams]);

  useEffect(() => {
    if (!selectedProjectId || !selectedFilePath) {
      setFilePreview(null);
      setFilePreviewError(null);
      setFilePreviewLoading(false);
      return;
    }

    let cancelled = false;

    async function loadFilePreview() {
      setFilePreviewLoading(true);
      setFilePreviewError(null);
      try {
        const preview = await api.getProjectFileContent(selectedProjectId, selectedFilePath);
        if (cancelled) {
          return;
        }
        setFilePreview(preview);
      } catch (previewError) {
        if (cancelled) {
          return;
        }
        const message = previewError instanceof Error ? previewError.message : "加载文件内容失败";
        setFilePreview(null);
        setFilePreviewError(message);
      } finally {
        if (!cancelled) {
          setFilePreviewLoading(false);
        }
      }
    }

    void loadFilePreview();
    return () => {
      cancelled = true;
    };
  }, [selectedFilePath, selectedProjectId]);

  useEffect(() => {
    if (!selectedSessionId) {
      setManagedVulnerabilities([]);
      setManagedVulnerabilitiesLoading(false);
      lastAutoSyncKeyRef.current = null;
      return;
    }

    let cancelled = false;
    setManagedVulnerabilitiesLoading(true);

    void listDirectAuditManagedVulnerabilities(selectedSessionId)
      .then((items) => {
        if (!cancelled) {
          setManagedVulnerabilities(items);
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setManagedVulnerabilities([]);
          toast.error(loadError instanceof Error ? loadError.message : "加载漏洞管理同步结果失败");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setManagedVulnerabilitiesLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [selectedSessionId]);

  useEffect(() => {
    if (
      !autoSyncManagedReports ||
      !selectedSessionId ||
      !latestReportMessageId ||
      latestReportSynced ||
      syncingManagedVulnerabilities
    ) {
      return;
    }

    const autoSyncKey = `${selectedSessionId}:${latestReportMessageId}`;
    if (lastAutoSyncKeyRef.current === autoSyncKey) {
      return;
    }

    lastAutoSyncKeyRef.current = autoSyncKey;
    void handleSyncLatestReport();
  }, [
    autoSyncManagedReports,
    latestReportMessageId,
    latestReportSynced,
    selectedSessionId,
    syncingManagedVulnerabilities,
  ]);

  async function reloadSessions(selectSessionId?: string) {
    if (!selectedProjectId) {
      return;
    }
    const sessions = await listDirectAuditSessions(selectedProjectId);
    setSessionIds(
      sessions.map((item) => ({
        id: item.id,
        updated_at: item.updated_at,
        state: item.state,
      })),
    );
    setSelectedSessionId(selectSessionId || sessions[0]?.id || "");
  }

  async function loadManagedVulnerabilities(sessionId: string) {
    setManagedVulnerabilitiesLoading(true);
    try {
      const items = await listDirectAuditManagedVulnerabilities(sessionId);
      setManagedVulnerabilities(items);
    } catch (loadError) {
      setManagedVulnerabilities([]);
      toast.error(loadError instanceof Error ? loadError.message : "加载漏洞管理同步结果失败");
    } finally {
      setManagedVulnerabilitiesLoading(false);
    }
  }

  async function handleCreateSession() {
    if (!selectedProjectId) {
      toast.error("请先选择项目");
      return;
    }

    const content = starterPrompt.trim();
    if (!content) {
      toast.error("请输入要让 finding agent 执行的直审请求");
      return;
    }

    createAbortRef.current?.abort();
    createAbortRef.current = new AbortController();
    createStreamingAssistantIdRef.current = null;
    setCreateStreamError(null);
    setCreatingSession(true);

    try {
      let createdSessionId = "";

      await streamCreateDirectAuditSession(
        {
          project_id: selectedProjectId,
          content,
          guardrails_enabled: createGuardrailsEnabled,
        },
        {
          signal: createAbortRef.current.signal,
          onEvent: (event: AuditSessionStreamEvent) => {
            if (event.type === "session_created" && event.session_id) {
              createdSessionId = event.session_id;
              setSelectedSessionId(event.session_id);
              setMessages([]);
              setSessionIds((previous) => {
                if (previous.some((item) => item.id === event.session_id)) {
                  return previous;
                }
                return [
                  {
                    id: event.session_id,
                    updated_at: new Date().toISOString(),
                    state: "running",
                  },
                  ...previous,
                ];
              });
              return;
            }

            if (event.type === "user_message" && event.message) {
              setMessages((previous) => upsertMessage(previous, event.message));
              return;
            }

            if (event.type === "assistant_start" && event.message) {
              createStreamingAssistantIdRef.current = event.message.id;
              setMessages((previous) => upsertMessage(previous, event.message));
              return;
            }

            if (event.type === "token") {
              const currentStreamingAssistantId = createStreamingAssistantIdRef.current;
              if (!currentStreamingAssistantId) {
                return;
              }
              setMessages((previous) =>
                previous.map((message) =>
                  message.id === currentStreamingAssistantId
                    ? {
                        ...message,
                        content: event.accumulated ?? `${message.content}${event.content ?? ""}`,
                      }
                    : message,
                ),
              );
              return;
            }

            if (event.type === "done" && event.message) {
              setMessages((previous) => {
                const withoutPlaceholder = previous.filter(
                  (message) => message.id !== createStreamingAssistantIdRef.current,
                );
                return upsertMessage(withoutPlaceholder, event.message);
              });
              createStreamingAssistantIdRef.current = null;
              return;
            }

            if (event.type === "error") {
              setCreateStreamError(event.message_text || "流式创建会话失败");
            }
          },
        },
      );

      setStarterPrompt("");
      await reloadSessions(createdSessionId || undefined);
      if (createdSessionId) {
        toast.success("Agent直审会话已启动");
      }
    } catch (createError) {
      if (!(createError instanceof DOMException && createError.name === "AbortError")) {
        const message = createError instanceof Error ? createError.message : "创建直审会话失败";
        setCreateStreamError(message);
        toast.error(message);
      }
    } finally {
      setCreatingSession(false);
      createAbortRef.current = null;
    }
  }

  async function handleFollowUp(content: string) {
    if (!selectedSessionId) {
      throw new Error("请先创建直审会话");
    }
    try {
      await sendMessage(content);
      await reloadSessions(selectedSessionId);
    } catch (followUpError) {
      const message = followUpError instanceof Error ? followUpError.message : "发送追问失败";
      toast.error(message);
      throw followUpError;
    }
  }

  async function handleGuardrailsChange(checked: boolean) {
    if (!selectedSessionId) {
      setCreateGuardrailsEnabled(checked);
      return;
    }
    setGuardrailUpdating(true);
    try {
      await updateDirectAuditGuardrails(selectedSessionId, checked);
      await refresh();
      toast.success(checked ? "已开启直审 guardrails" : "已关闭直审 guardrails");
    } catch (guardrailError) {
      toast.error(guardrailError instanceof Error ? guardrailError.message : "更新 guardrails 失败");
    } finally {
      setGuardrailUpdating(false);
    }
  }

  async function handleApproveToolCall(toolCall: (typeof toolCalls)[number], scope: DirectAuditApprovalScope) {
    if (!selectedSessionId) {
      toast.error("当前没有可批准的直审会话");
      return;
    }
    setApprovalToolCallId(toolCall.id);
    try {
      await runStreamRequest((handlers) =>
        streamApproveDirectAuditToolCall(selectedSessionId, toolCall.id, scope, handlers),
      );
      await reloadSessions(selectedSessionId);
      toast.success(
        scope === "session"
          ? "已按本会话范围批准，finding agent 正在继续执行"
          : "已批准本次操作，finding agent 正在继续执行",
      );
    } catch (approvalError) {
      toast.error(approvalError instanceof Error ? approvalError.message : "批准工具调用失败");
    } finally {
      setApprovalToolCallId(null);
    }
  }

  async function handleSyncLatestReport() {
    if (!selectedSessionId) {
      toast.error("请先选择直审会话");
      return;
    }

    setSyncingManagedVulnerabilities(true);
    try {
      const synced = await syncLatestDirectAuditManagedVulnerability(selectedSessionId);
      await loadManagedVulnerabilities(selectedSessionId);
      toast.success(`已同步到漏洞管理：${synced.vulnerability_name}`);
    } catch (syncError) {
      toast.error(syncError instanceof Error ? syncError.message : "同步漏洞报告失败");
    } finally {
      setSyncingManagedVulnerabilities(false);
    }
  }

  function handleStopTimelineStreaming() {
    if (createAbortRef.current) {
      createAbortRef.current.abort();
      return;
    }
    stopStreaming();
  }

  return (
    <div className="min-h-screen bg-[linear-gradient(180deg,#f3f6fb_0%,#eef3f7_100%)] p-4 md:p-6">
      <div className="mx-auto flex max-w-[1800px] items-center justify-between gap-4 pb-5">
        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-[11px] font-medium uppercase tracking-[0.24em] text-slate-500">
              Agent Direct Audit
            </span>
            {selectedSessionId ? (
              <Badge variant="outline" className="rounded-full bg-white/80">
                {session?.state || "running"}
              </Badge>
            ) : null}
          </div>
          <h1 className="text-4xl font-semibold tracking-tight text-slate-950">Agent直审</h1>
          <p className="max-w-3xl text-sm leading-6 text-slate-600">
            像 IDE 一样打开项目目录，像聊天一样持续追问 finding agent。左侧负责项目和目录，中间负责会话，右侧集中查看
            findings、工具轨迹和报告同步状态。
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => setLeftCollapsed((value) => !value)}
          className="rounded-full border-slate-300 bg-white/90 px-5 shadow-sm"
        >
          {leftCollapsed ? <PanelLeftOpen className="mr-2 h-4 w-4" /> : <PanelLeftClose className="mr-2 h-4 w-4" />}
          {leftCollapsed ? "展开左栏" : "收起左栏"}
        </Button>
      </div>

      <div
        className={`mx-auto grid max-w-[1800px] gap-4 ${
          leftCollapsed
            ? "xl:grid-cols-[minmax(0,1fr)_360px]"
            : "xl:grid-cols-[300px_minmax(0,1fr)_360px]"
        }`}
      >
        {!leftCollapsed ? (
          <aside className="space-y-4">
            <Card className={panelClass}>
              <CardHeader className="pb-4">
                <CardTitle className="flex items-center gap-2 text-base font-semibold text-slate-900">
                  <Sparkles className="h-4 w-4 text-slate-500" />
                  项目上下文
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <p className="text-xs font-medium uppercase tracking-[0.16em] text-slate-400">Project</p>
                  <Select
                    value={selectedProjectId}
                    onValueChange={setSelectedProjectId}
                    disabled={projectsLoading || projects.length === 0}
                  >
                    <SelectTrigger className="rounded-2xl border-slate-200 bg-slate-50/90">
                      <SelectValue placeholder={projectsLoading ? "加载项目中..." : "选择项目"} />
                    </SelectTrigger>
                    <SelectContent>
                      {projects.map((project) => (
                        <SelectItem key={project.id} value={project.id}>
                          {project.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-slate-900">
                        {selectedProject?.name || "尚未选择项目"}
                      </p>
                      <p className="mt-1 truncate text-xs text-slate-500">
                        {selectedProject?.local_path || selectedProject?.repository_url || "请选择一个项目开始直审"}
                      </p>
                    </div>
                    {selectedProject ? (
                      <Badge variant="outline" className="rounded-full">
                        {selectedProject.source_type}
                      </Badge>
                    ) : null}
                  </div>
                </div>

                <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1">
                      <p className="text-sm font-semibold text-slate-900">Guardrails</p>
                      <p className="text-xs leading-5 text-slate-500">
                        默认关闭，避免影响自动化。开启后，源码写入与可变更 shell 会进入审批链路。
                      </p>
                    </div>
                    <Switch
                      checked={guardrailsEnabled}
                      onCheckedChange={handleGuardrailsChange}
                      disabled={guardrailUpdating || creatingSession}
                    />
                  </div>
                  <Badge variant="outline" className="mt-3 rounded-full">
                    {guardrailsEnabled ? "当前已开启" : "当前已关闭"}
                  </Badge>
                </div>
              </CardContent>
            </Card>

            <Card className={panelClass}>
              <CardHeader className="flex flex-row items-center justify-between pb-4">
                <CardTitle className="flex items-center gap-2 text-base font-semibold text-slate-900">
                  <MessageSquareText className="h-4 w-4 text-slate-500" />
                  会话列表
                </CardTitle>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="rounded-full"
                  onClick={() => {
                    setSelectedSessionId("");
                    setMessages([]);
                    setCreateStreamError(null);
                  }}
                >
                  <Plus className="mr-2 h-4 w-4" />
                  新会话
                </Button>
              </CardHeader>
              <CardContent className="p-0">
                <ScrollArea className="h-[240px]">
                  <div className="space-y-2 p-4">
                    {sessionIds.length === 0 ? (
                      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 p-4 text-sm text-slate-500">
                        还没有直审会话。输入首条请求后会自动创建。
                      </div>
                    ) : (
                      sessionIds.map((item, index) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => setSelectedSessionId(item.id)}
                          className={`w-full rounded-2xl border px-4 py-3 text-left transition ${
                            selectedSessionId === item.id
                              ? "border-slate-900 bg-slate-900 text-white"
                              : "border-slate-200 bg-white hover:bg-slate-50"
                          }`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-sm font-semibold">会话 {sessionIds.length - index}</p>
                              <p
                                className={`mt-1 text-xs ${
                                  selectedSessionId === item.id ? "text-slate-300" : "text-slate-500"
                                }`}
                              >
                                {formatSessionTime(item.updated_at)}
                              </p>
                            </div>
                            <Badge
                              variant="outline"
                              className={`rounded-full ${
                                selectedSessionId === item.id
                                  ? "border-white/20 bg-white/10 text-white"
                                  : "border-slate-200"
                              }`}
                            >
                              {item.state}
                            </Badge>
                          </div>
                        </button>
                      ))
                    )}
                  </div>
                </ScrollArea>
              </CardContent>
            </Card>

            <Card className={`${panelClass} min-h-[420px]`}>
              <CardHeader className="pb-4">
                <CardTitle className="flex items-center gap-2 text-base font-semibold text-slate-900">
                  <Workflow className="h-4 w-4 text-slate-500" />
                  目录树
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <ScrollArea className="h-[420px]">
                  <div className="p-3">
                    {fileTree.length === 0 ? (
                      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 p-4 text-sm text-slate-500">
                        选择项目后，这里会展示可直审的目录结构。
                      </div>
                    ) : (
                      fileTree.map((node) => (
                        <FileTreeNode
                          key={node.path}
                          node={node}
                          selectedPath={selectedFilePath}
                          onSelect={setSelectedFilePath}
                        />
                      ))
                    )}
                  </div>
                </ScrollArea>
              </CardContent>
            </Card>
          </aside>
        ) : null}

        <main className="space-y-4">
          <Card className={`${panelClass} ${selectedFilePath ? "" : "border-dashed"}`}>
            <CardHeader className="pb-4">
              <div className="flex items-center justify-between gap-3">
                <CardTitle className="flex items-center gap-2 text-base font-semibold text-slate-900">
                  <FileSearch className="h-4 w-4 text-slate-500" />
                  代码预览
                </CardTitle>
                {selectedFilePath ? (
                  <Badge variant="outline" className="rounded-full">
                    {selectedFilePath}
                  </Badge>
                ) : null}
              </div>
            </CardHeader>
            <CardContent className="p-0">
              {!selectedFilePath ? (
                <div className="flex h-[220px] items-center justify-center px-6 text-center text-sm text-slate-500">
                  从左侧选择一个文件，这里会显示源码预览，方便你和 finding agent 对照查看。
                </div>
              ) : filePreviewLoading ? (
                <div className="flex h-[220px] items-center justify-center gap-3 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在加载文件内容...
                </div>
              ) : filePreviewError ? (
                <div className="flex h-[220px] items-center justify-center px-6 text-center text-sm text-rose-600">
                  {filePreviewError}
                </div>
              ) : filePreview ? (
                <>
                  <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-slate-900">{filePreview.path}</p>
                      <p className="mt-1 text-xs text-slate-500">{previewLines.length} lines</p>
                    </div>
                    <div className="flex items-center gap-2">
                      {filePreview.truncated ? <Badge variant="outline">已截断</Badge> : null}
                      <Badge variant="secondary">{Math.max(filePreview.size, 0)} bytes</Badge>
                    </div>
                  </div>
                  <ScrollArea className="h-[260px]">
                    <div className="bg-[rgb(15,23,42)] px-0 py-3 font-mono text-[12px] leading-6 text-slate-100">
                      {previewLines.map((line) => (
                        <div
                          key={`${filePreview.path}:${line.number}`}
                          className="grid grid-cols-[64px_minmax(0,1fr)] gap-0 px-4 hover:bg-white/5"
                        >
                          <span className="select-none pr-4 text-right text-slate-500">{line.number}</span>
                          <pre className="overflow-x-auto whitespace-pre-wrap break-words">{line.content || " "}</pre>
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                </>
              ) : null}
            </CardContent>
          </Card>

          {!selectedSessionId ? (
            <Card className={`${panelClass} min-h-[560px]`}>
              <CardHeader className="border-b border-slate-200 bg-[linear-gradient(135deg,rgba(15,23,42,.98),rgba(30,41,59,.94))] pb-5 text-white">
                <CardTitle className="flex items-center gap-3 text-2xl font-semibold tracking-tight">
                  <Bot className="h-6 w-6" />
                  启动 Agent直审
                </CardTitle>
                <p className="text-sm text-slate-300">
                  先选项目，再直接告诉 finding agent 你想审什么，比如“看看有没有高危漏洞”或“使用 cve report writer 生成报告”。
                </p>
              </CardHeader>
              <CardContent className="space-y-5 p-6">
                <div className="rounded-[24px] border border-slate-200 bg-slate-50/80 p-4">
                  <Textarea
                    className="min-h-[220px] resize-none rounded-[18px] border-0 bg-transparent px-2 py-2 text-[15px] leading-7 shadow-none focus-visible:ring-0"
                    placeholder="例如：帮我看看这个项目有没有安全漏洞，并优先关注认证、文件上传、命令执行、SSRF 和越权问题。"
                    value={starterPrompt}
                    onChange={(event) => setStarterPrompt(event.target.value)}
                    disabled={creatingSession}
                  />
                  <div className="mt-4 flex items-center justify-between gap-3 border-t border-slate-200 pt-4 text-sm text-slate-500">
                    <span>{selectedProject ? `当前项目：${selectedProject.name}` : "请先选择项目"}</span>
                    <Button
                      type="button"
                      disabled={!selectedProjectId || creatingSession || !starterPrompt.trim()}
                      className="h-11 rounded-full bg-slate-950 px-5 text-white hover:bg-slate-800"
                      onClick={() => void handleCreateSession()}
                    >
                      <SendHorizonal className="mr-2 h-4 w-4" />
                      {creatingSession ? "启动中..." : "启动直审"}
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ) : (
            <AuditTimeline
              messages={messages}
              isStreaming={timelineStreaming}
              streamError={timelineError}
              onStopStreaming={handleStopTimelineStreaming}
              activeStreamingMessageId={timelineStreamingAssistantId}
              footer={
                <div className="space-y-3">
                  {sessionFailed ? (
                    <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                      上一轮直审已中断。你可以直接继续追问重试；如果没有反应，请优先点击“发送追问”或按 `Enter` 发送。
                    </div>
                  ) : null}
                  <FollowUpComposer disabled={loading || timelineStreaming} onSubmit={handleFollowUp} />
                </div>
              }
            />
          )}
        </main>

        <aside className="space-y-4">
          <Card className={panelClass}>
            <CardHeader className="pb-4">
              <div className="flex items-center justify-between gap-3">
                <CardTitle className="flex items-center gap-2 text-base font-semibold text-slate-900">
                  <ShieldAlert className="h-4 w-4 text-slate-500" />
                  报告同步
                </CardTitle>
                <Button
                  type="button"
                  size="sm"
                  className="rounded-full"
                  disabled={syncActionDisabled}
                  onClick={() => void handleSyncLatestReport()}
                >
                  {syncActionLabel}
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                <div className="space-y-1">
                  <p className="text-sm font-medium text-slate-900">自动同步最近报告</p>
                  <p className="text-xs leading-5 text-slate-500">
                    默认关闭。开启后，检测到新的三语报告包时会自动同步一次。
                  </p>
                </div>
                <Switch checked={autoSyncManagedReports} onCheckedChange={setAutoSyncManagedReports} />
              </div>

              <div
                className={`rounded-2xl px-4 py-3 text-sm ${
                  !hasLatestReport
                    ? "border border-dashed border-slate-200 bg-slate-50/80 text-slate-500"
                    : latestReportSynced
                      ? "border border-emerald-200 bg-emerald-50 text-emerald-800"
                      : "border border-amber-200 bg-amber-50 text-amber-800"
                }`}
              >
                {!hasLatestReport
                  ? "当前会话还没有检测到 CVE report writer 生成的完整三语报告。"
                  : latestReportSynced
                    ? "最新报告已经同步到漏洞管理。"
                    : "检测到新的三语报告包，可以一键同步到漏洞管理。"}
              </div>

              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-500">当前会话已沉淀漏洞</span>
                <Link to="/vulnerabilities" className="font-medium text-slate-900 hover:underline">
                  打开漏洞管理
                </Link>
              </div>

              {managedVulnerabilitiesLoading ? (
                <div className="flex items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-4 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在加载同步结果...
                </div>
              ) : managedVulnerabilities.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 p-4 text-sm text-slate-500">
                  还没有同步到漏洞管理的报告。
                </div>
              ) : (
                <div className="space-y-3">
                  {managedVulnerabilities.map((item) => (
                    <div key={item.id} className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-slate-900">{item.vulnerability_name}</p>
                          <p className="mt-1 truncate text-xs text-slate-500">
                            {item.file_path || "未提取到文件位置"}
                            {item.line_start
                              ? `:${item.line_start}${item.line_end && item.line_end !== item.line_start ? `-${item.line_end}` : ""}`
                              : ""}
                          </p>
                        </div>
                        <Badge variant="outline" className="rounded-full">
                          {formatManagedSeverity(item.severity)}
                        </Badge>
                      </div>
                      <div className="mt-3 flex items-center justify-between text-xs text-slate-500">
                        <span>{item.reports?.length ? `报告 ${item.reports.length} 份` : "报告待补充"}</span>
                        <span>{formatSessionTime(item.updated_at || item.created_at)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card className={`${panelClass} min-h-[640px]`}>
            <CardHeader className="pb-4">
              <CardTitle className="flex items-center gap-2 text-base font-semibold text-slate-900">
                <Wrench className="h-4 w-4 text-slate-500" />
                Inspector
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Tabs defaultValue="findings" className="gap-4">
                <TabsList className="grid w-full grid-cols-3 rounded-2xl bg-slate-100">
                  <TabsTrigger value="findings">Findings</TabsTrigger>
                  <TabsTrigger value="tools">Tools</TabsTrigger>
                  <TabsTrigger value="signals">Signals</TabsTrigger>
                </TabsList>

                <TabsContent value="findings" className="space-y-4">
                  <FindingsSidebar session={session} />
                </TabsContent>

                <TabsContent value="tools" className="space-y-4">
                  <ToolTracePanel
                    toolCalls={toolCalls}
                    onApproveToolCall={handleApproveToolCall}
                    approvalLoadingToolCallId={approvalToolCallId}
                  />
                  <HandoffTracePanel handoffs={handoffs} />
                </TabsContent>

                <TabsContent value="signals" className="space-y-4">
                  <SkillTracePanel skills={skills} skillInvocations={skillInvocations} />
                  <MemoryTracePanel memories={memories} />
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
