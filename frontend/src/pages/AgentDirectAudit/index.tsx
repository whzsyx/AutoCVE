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
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { useSearchParams } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
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
  listDirectAuditSessions,
  streamApproveDirectAuditToolCall,
  streamCreateDirectAuditSession,
  streamDirectAuditSessionMessage,
  updateDirectAuditGuardrails,
} from "@/shared/api/agentDirectAudit";
import type { AuditSessionMessage, AuditSessionStreamEvent } from "@/shared/api/auditSessions";
import { api } from "@/shared/config/database";
import type { Project, ProjectFileContent } from "@/shared/types";
import { isLocalDirectoryProject } from "@/shared/utils/projectUtils";

type FileEntry = { path: string; size: number };

type TreeNode = {
  name: string;
  path: string;
  type: "directory" | "file";
  children: TreeNode[];
};

const LEFT_COLLAPSED_STORAGE_KEY = "agent-direct-audit:left-collapsed";

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
            ? "bg-[rgba(224,238,228,.92)] text-slate-900 shadow-[0_10px_24px_rgba(111,167,132,.12)]"
            : "text-slate-600 hover:bg-white/80"
        }`}
        style={{ paddingLeft: `${depth * 14 + 12}px` }}
      >
        <FileCode2 className={`h-4 w-4 ${isSelected ? "text-[hsl(var(--primary))]" : "text-slate-400"}`} />
        <span className="truncate">{node.name}</span>
      </button>
    );
  }

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm font-medium text-slate-700 hover:bg-white/80"
        style={{ paddingLeft: `${depth * 14 + 12}px` }}
      >
        {open ? <FolderOpen className="h-4 w-4 text-[hsl(var(--primary))]" /> : <Folder className="h-4 w-4 text-slate-400" />}
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
  const createAbortRef = useRef<AbortController | null>(null);
  const createStreamingAssistantIdRef = useRef<string | null>(null);

  const selectedProject = projects.find((project) => project.id === selectedProjectId) || null;
  const queryProjectId = searchParams.get("projectId") || "";
  const querySessionId = searchParams.get("sessionId") || "";
  const queryFilePath = searchParams.get("file") || "";
  const { session, messages, setMessages, toolCalls, skills, skillInvocations, memories, handoffs, loading, error, refresh } =
    useAuditSession(selectedSessionId || undefined);
  const { isStreaming, streamError, runStreamRequest, sendMessage, stopStreaming, streamingAssistantId } = useAuditSessionChatStream({
    sessionId: selectedSessionId || undefined,
    setMessages,
    refresh,
    streamMessage: streamDirectAuditSessionMessage,
  });

  const fileTree = useMemo(() => buildFileTree(projectFiles), [projectFiles]);
  const previewLines = useMemo(() => buildPreviewLines(filePreview), [filePreview]);
  const guardrailsEnabled = selectedSessionId ? Boolean(session?.guardrails_enabled) : createGuardrailsEnabled;

  useAuditSessionStream(
    () => refresh({ silent: true }),
    Boolean(selectedSessionId && session?.state === "running" && !isStreaming),
  );

  useEffect(() => {
    const saved = window.localStorage.getItem(LEFT_COLLAPSED_STORAGE_KEY);
    if (saved === "true") {
      setLeftCollapsed(true);
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(LEFT_COLLAPSED_STORAGE_KEY, String(leftCollapsed));
  }, [leftCollapsed]);

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
          toast.error(workspaceError instanceof Error ? workspaceError.message : "加载直审工作台失败");
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

  async function handleCreateSession() {
    if (!selectedProjectId) {
      toast.error("请先选择项目");
      return;
    }

    const content = starterPrompt.trim();
    if (!content) {
      toast.error("请输入要让 finding agent 执行的审计请求");
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
              setMessages((previous) => upsertMessage(previous, event.message!));
              return;
            }

            if (event.type === "assistant_start" && event.message) {
              createStreamingAssistantIdRef.current = event.message.id;
              setMessages((previous) => upsertMessage(previous, event.message!));
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
                return upsertMessage(withoutPlaceholder, event.message!);
              });
              createStreamingAssistantIdRef.current = null;
              return;
            }

            if (event.type === "error") {
              setCreateStreamError(event.message_text || "Streaming failed");
            }
          },
        },
      );

      setStarterPrompt("");
      await reloadSessions(createdSessionId || undefined);
      if (createdSessionId) {
        toast.success("Agent 直审会话已启动");
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
    await sendMessage(content);
    await reloadSessions(selectedSessionId);
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

  async function handleApproveToolCall(toolCall: (typeof toolCalls)[number]) {
    if (!selectedSessionId) {
      toast.error("当前没有可批准的直审会话");
      return;
    }
    setApprovalToolCallId(toolCall.id);
    try {
      await runStreamRequest((handlers) => streamApproveDirectAuditToolCall(selectedSessionId, toolCall.id, handlers));
      await reloadSessions(selectedSessionId);
      toast.success("已批准该工具调用，finding agent 正在继续执行");
    } catch (approvalError) {
      toast.error(approvalError instanceof Error ? approvalError.message : "批准工具调用失败");
    } finally {
      setApprovalToolCallId(null);
    }
  }

  const timelineStreaming = creatingSession || isStreaming;
  const timelineError = createStreamError || streamError || error;
  const timelineStreamingAssistantId = createStreamingAssistantIdRef.current || streamingAssistantId;

  function handleStopTimelineStreaming() {
    if (createAbortRef.current) {
      createAbortRef.current.abort();
      return;
    }
    stopStreaming();
  }

  return (
    <div className="min-h-screen space-y-6 bg-[radial-gradient(circle_at_top_left,rgba(225,236,230,.85),transparent_35%),linear-gradient(180deg,rgba(248,250,247,.96),rgba(240,246,242,.98))] p-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight text-slate-900">Agent直审</h1>
          <p className="mt-2 text-sm text-slate-500">
            直接打开项目目录，让 finding agent 像持续会话一样实时审计、追查问题并生成结果。
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => setLeftCollapsed((value) => !value)}
          className="rounded-full border-[rgba(179,197,186,.8)] bg-white/80 shadow-sm backdrop-blur"
        >
          {leftCollapsed ? <PanelLeftOpen className="mr-2 h-4 w-4" /> : <PanelLeftClose className="mr-2 h-4 w-4" />}
          {leftCollapsed ? "展开左栏" : "收起左栏"}
        </Button>
      </div>

      <div
        className={`grid gap-6 ${
          leftCollapsed
            ? "xl:grid-cols-[minmax(0,1.55fr)_minmax(340px,0.85fr)]"
            : "xl:grid-cols-[320px_minmax(0,1.45fr)_minmax(340px,0.85fr)]"
        }`}
      >
        {!leftCollapsed ? (
          <div className="space-y-6">
            <Card className="overflow-hidden rounded-[28px] border border-[rgba(191,208,198,.72)] bg-[linear-gradient(180deg,rgba(255,255,255,.96),rgba(246,250,247,.96))] shadow-[0_18px_58px_rgba(84,110,93,.10)]">
              <CardHeader className="border-b border-[rgba(186,203,193,.42)] pb-4">
                <CardTitle className="flex items-center gap-3 text-base font-semibold text-slate-900">
                  <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[rgba(233,244,235,.9)] text-[hsl(var(--primary))]">
                    <Sparkles className="h-5 w-5" />
                  </span>
                  项目与上下文
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 p-4">
                <div className="space-y-2">
                  <p className="text-xs font-medium uppercase tracking-[0.14em] text-slate-400">当前项目</p>
                  <Select value={selectedProjectId} onValueChange={setSelectedProjectId} disabled={projectsLoading || projects.length === 0}>
                    <SelectTrigger className="rounded-2xl border-[rgba(179,197,186,.6)] bg-white/90">
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
                {selectedProject ? (
                  <div className="rounded-[22px] border border-[rgba(210,220,214,.85)] bg-white/90 p-4 shadow-[0_10px_24px_rgba(103,120,109,.06)]">
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-slate-900">{selectedProject.name}</p>
                        <p className="mt-1 truncate text-xs text-slate-500">
                          {selectedProject.local_path || selectedProject.repository_url || "ZIP 项目工作区"}
                        </p>
                      </div>
                      <Badge className="rounded-full bg-[rgba(219,233,223,.95)] px-3 py-1 text-[11px] text-slate-700 shadow-sm">
                        {selectedProject.source_type}
                      </Badge>
                    </div>
                  </div>
                ) : null}
                <div className="rounded-[22px] border border-[rgba(210,220,214,.85)] bg-[rgba(248,250,248,.92)] p-4 shadow-[0_10px_24px_rgba(103,120,109,.04)]">
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1">
                      <p className="text-sm font-semibold text-slate-900">Guardrails</p>
                      <p className="text-xs leading-5 text-slate-500">
                        默认关闭，避免影响工作流自动化。开启后，源码写入和可变更 shell 命令会进入显式批准流程。
                      </p>
                    </div>
                    <Switch checked={guardrailsEnabled} onCheckedChange={handleGuardrailsChange} disabled={guardrailUpdating || creatingSession} />
                  </div>
                  <div className="mt-3">
                    <Badge variant="outline" className="rounded-full text-[11px]">
                      {guardrailsEnabled ? "当前已开启" : selectedSessionId ? "当前已关闭" : "新会话默认关闭"}
                    </Badge>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card className="overflow-hidden rounded-[28px] border border-[rgba(191,208,198,.72)] bg-[linear-gradient(180deg,rgba(255,255,255,.96),rgba(246,250,247,.96))] shadow-[0_18px_58px_rgba(84,110,93,.10)]">
              <CardHeader className="flex flex-row items-center justify-between border-b border-[rgba(186,203,193,.42)] pb-4">
                <CardTitle className="flex items-center gap-3 text-base font-semibold text-slate-900">
                  <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[rgba(255,244,227,.92)] text-orange-700">
                    <MessageSquareText className="h-5 w-5" />
                  </span>
                  直审会话
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
                      <div className="rounded-[20px] border border-dashed border-[rgba(191,208,198,.72)] bg-[rgba(248,251,249,.9)] p-4 text-sm text-slate-500">
                        还没有直审会话。可以在中间输入首条审计请求来启动 finding agent。
                      </div>
                    ) : (
                      sessionIds.map((item, index) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => setSelectedSessionId(item.id)}
                          className={`flex w-full items-start justify-between gap-3 rounded-[20px] border px-4 py-3 text-left transition ${
                            selectedSessionId === item.id
                              ? "border-[rgba(111,167,132,.28)] bg-[rgba(224,238,228,.92)] shadow-[0_12px_28px_rgba(111,167,132,.12)]"
                              : "border-[rgba(210,220,214,.78)] bg-white/88 hover:bg-white"
                          }`}
                        >
                          <div className="min-w-0">
                            <p className="text-sm font-semibold text-slate-900">会话 {sessionIds.length - index}</p>
                            <p className="mt-1 text-xs text-slate-500">{formatSessionTime(item.updated_at)}</p>
                          </div>
                          <Badge variant="outline" className="rounded-full text-[11px]">
                            {item.state}
                          </Badge>
                        </button>
                      ))
                    )}
                  </div>
                </ScrollArea>
              </CardContent>
            </Card>

            <Card className="overflow-hidden rounded-[28px] border border-[rgba(191,208,198,.72)] bg-[linear-gradient(180deg,rgba(255,255,255,.96),rgba(246,250,247,.96))] shadow-[0_18px_58px_rgba(84,110,93,.10)]">
              <CardHeader className="border-b border-[rgba(186,203,193,.42)] pb-4">
                <CardTitle className="flex items-center gap-3 text-base font-semibold text-slate-900">
                  <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[rgba(235,242,255,.95)] text-sky-700">
                    <FolderOpen className="h-5 w-5" />
                  </span>
                  项目目录树
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <ScrollArea className="h-[420px]">
                  <div className="p-3">
                    {fileTree.length === 0 ? (
                      <div className="rounded-[20px] border border-dashed border-[rgba(191,208,198,.72)] bg-[rgba(248,251,249,.9)] p-4 text-sm text-slate-500">
                        还没有加载到文件列表。
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
          </div>
        ) : null}

        <div className="space-y-6">
          <Card className="overflow-hidden rounded-[28px] border border-[rgba(191,208,198,.72)] bg-[linear-gradient(180deg,rgba(255,255,255,.98),rgba(245,249,246,.96))] shadow-[0_18px_58px_rgba(84,110,93,.10)]">
            <CardHeader className="border-b border-[rgba(186,203,193,.42)] pb-4">
              <CardTitle className="flex items-center gap-3 text-base font-semibold text-slate-900">
                <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[rgba(232,239,255,.92)] text-indigo-700">
                  <FileSearch className="h-5 w-5" />
                </span>
                文件预览
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {!selectedFilePath ? (
                <div className="flex h-[320px] items-center justify-center px-6 text-center text-sm text-slate-500">
                  从左侧目录树选择一个文件，就能在这里直接查看源码内容。
                </div>
              ) : filePreviewLoading ? (
                <div className="flex h-[320px] items-center justify-center gap-3 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在加载文件内容...
                </div>
              ) : filePreviewError ? (
                <div className="flex h-[320px] items-center justify-center px-6 text-center text-sm text-rose-600">
                  {filePreviewError}
                </div>
              ) : filePreview ? (
                <>
                  <div className="flex items-center justify-between gap-3 border-b border-[rgba(186,203,193,.36)] px-4 py-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-slate-900">{filePreview.path}</p>
                      <p className="mt-1 text-xs text-slate-500">{previewLines.length} lines</p>
                    </div>
                    <div className="flex items-center gap-2">
                      {filePreview.truncated ? <Badge variant="outline">已截断</Badge> : null}
                      <Badge variant="secondary">{Math.max(filePreview.size, 0)} bytes</Badge>
                    </div>
                  </div>
                  <ScrollArea className="h-[320px]">
                    <div className="bg-[rgba(246,249,247,.98)] px-0 py-2 font-mono text-[12px] leading-6 text-slate-800">
                      {previewLines.map((line) => (
                        <div key={`${filePreview.path}:${line.number}`} className="grid grid-cols-[64px_minmax(0,1fr)] gap-0 px-4 hover:bg-white/70">
                          <span className="select-none pr-4 text-right text-slate-400">{line.number}</span>
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
            <Card className="overflow-hidden rounded-[30px] border border-[rgba(191,208,198,.72)] bg-[linear-gradient(180deg,rgba(255,255,255,.98),rgba(244,249,246,.96))] shadow-[0_28px_90px_rgba(84,110,93,.12)]">
              <CardHeader className="border-b border-[rgba(186,203,193,.45)] bg-[radial-gradient(circle_at_top_left,rgba(214,234,220,.9),rgba(255,255,255,.72)_55%)] pb-5">
                <CardTitle className="flex items-center gap-3 text-2xl font-semibold tracking-tight text-slate-900">
                  <Bot className="h-6 w-6 text-[hsl(var(--primary))]" />
                  启动 Agent直审
                </CardTitle>
                <p className="text-sm text-muted-foreground">
                  选中项目后，直接告诉 finding agent 你想审什么，比如“帮我看看有没有高危漏洞”“检查 SSRF 和鉴权绕过”或“使用 cve report writer 生成报告”。
                </p>
              </CardHeader>
              <CardContent className="space-y-5 p-6">
                <div className="rounded-[24px] border border-[rgba(154,180,163,.35)] bg-[linear-gradient(180deg,rgba(255,255,255,.96),rgba(241,247,243,.92))] p-3 shadow-[0_20px_60px_rgba(118,146,126,.08)]">
                  <Textarea
                    className="min-h-[180px] resize-none rounded-[18px] border-0 bg-transparent px-3 py-3 text-[15px] leading-7 shadow-none focus-visible:ring-0"
                    placeholder="例如：帮我看看这个项目有没有安全漏洞，并优先关注认证、文件上传和命令执行风险。"
                    value={starterPrompt}
                    onChange={(event) => setStarterPrompt(event.target.value)}
                    disabled={creatingSession}
                  />
                  <div className="mt-3 flex items-center justify-between gap-3 border-t border-[rgba(154,180,163,.2)] px-2 pt-3 text-xs text-muted-foreground">
                    <span>{selectedProject ? `当前项目：${selectedProject.name}` : "请先选择项目"}</span>
                    <Button
                      type="button"
                      disabled={!selectedProjectId || creatingSession || !starterPrompt.trim()}
                      className="h-11 rounded-full bg-[linear-gradient(135deg,#89A98D,#5E7A63)] px-5 text-white shadow-[0_16px_35px_rgba(94,122,99,.22)] hover:opacity-95"
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
              footer={<FollowUpComposer disabled={loading || timelineStreaming} onSubmit={handleFollowUp} />}
            />
          )}
        </div>

        <div className="space-y-6">
          <FindingsSidebar session={session} />
          <HandoffTracePanel handoffs={handoffs} />
          <ToolTracePanel
            toolCalls={toolCalls}
            onApproveToolCall={handleApproveToolCall}
            approvalLoadingToolCallId={approvalToolCallId}
          />
          <SkillTracePanel skills={skills} skillInvocations={skillInvocations} />
          <MemoryTracePanel memories={memories} />
          {selectedProject ? (
            <Card className="overflow-hidden rounded-[26px] border border-[rgba(191,208,198,.72)] bg-[linear-gradient(180deg,rgba(255,255,255,.98),rgba(247,250,248,.96))] shadow-[0_18px_48px_rgba(84,110,93,.08)]">
              <CardHeader className="border-b border-[rgba(186,203,193,.4)] pb-4">
                <CardTitle className="text-base font-semibold text-slate-900">项目摘要</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 p-4 text-sm text-muted-foreground">
                <div className="flex items-center justify-between">
                  <span>来源类型</span>
                  <Badge variant="outline">{selectedProject.source_type}</Badge>
                </div>
                <Separator />
                <div className="flex items-center justify-between">
                  <span>会话数量</span>
                  <span className="font-medium text-slate-700">{sessionIds.length}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span>文件数量</span>
                  <span className="font-medium text-slate-700">{projectFiles.length}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span>当前文件</span>
                  <span className="max-w-[180px] truncate font-medium text-slate-700">{selectedFilePath || "未选择"}</span>
                </div>
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}
