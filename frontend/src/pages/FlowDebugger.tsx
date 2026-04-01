import { useEffect, useMemo, useState } from 'react';
import { Activity, ArrowRight, FileJson, RefreshCcw, Search, Wrench } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  DebugHandoff,
  DebugTaskListItem,
  DebugTimelineEvent,
  DebugTraceResponse,
  getDebugTasks,
  getDebugTrace,
} from '@/shared/api/flowDebugger';
import { cn } from '@/shared/utils/utils';

type EventMeta = {
  label: string;
  badge: string;
  border: string;
};

const EVENT_META: Record<string, EventMeta> = {
  prompt_system: { label: '系统提示词', badge: 'bg-sky-100 text-sky-700', border: 'border-sky-200' },
  prompt_user: { label: '用户提示词', badge: 'bg-indigo-100 text-indigo-700', border: 'border-indigo-200' },
  model_response_raw: { label: '模型原始响应', badge: 'bg-violet-100 text-violet-700', border: 'border-violet-200' },
  llm_thought: { label: '模型思考', badge: 'bg-fuchsia-100 text-fuchsia-700', border: 'border-fuchsia-200' },
  react_thought: { label: 'ReAct 思考', badge: 'bg-pink-100 text-pink-700', border: 'border-pink-200' },
  react_action: { label: 'ReAct 动作', badge: 'bg-amber-100 text-amber-700', border: 'border-amber-200' },
  react_observation: { label: 'ReAct 观察', badge: 'bg-emerald-100 text-emerald-700', border: 'border-emerald-200' },
  tool_call: { label: '工具调用', badge: 'bg-orange-100 text-orange-700', border: 'border-orange-200' },
  tool_result: { label: '工具结果', badge: 'bg-lime-100 text-lime-700', border: 'border-lime-200' },
  handoff_out: { label: '发起交接', badge: 'bg-cyan-100 text-cyan-700', border: 'border-cyan-200' },
  handoff_in: { label: '接收交接', badge: 'bg-teal-100 text-teal-700', border: 'border-teal-200' },
  agent_start: { label: 'Agent 启动', badge: 'bg-slate-100 text-slate-700', border: 'border-slate-200' },
  agent_complete: { label: 'Agent 完成', badge: 'bg-green-100 text-green-700', border: 'border-green-200' },
  error: { label: '错误', badge: 'bg-red-100 text-red-700', border: 'border-red-200' },
};

const DEFAULT_META: EventMeta = {
  label: '事件',
  badge: 'bg-muted text-muted-foreground',
  border: 'border-border',
};

function metaOf(eventType?: string | null) {
  return (eventType && EVENT_META[eventType]) || DEFAULT_META;
}

function taskTitle(task: DebugTaskListItem) {
  return task.name || task.id;
}

function toText(value: unknown) {
  if (typeof value === 'string') return value;
  if (value == null) return '';
  return JSON.stringify(value, null, 2);
}

function formatWhen(value?: string | null) {
  if (!value) return '暂无';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function eventMessage(event: DebugTimelineEvent) {
  return (
    event.message ||
    (typeof event.payload?.message === 'string' ? event.payload.message : '') ||
    (typeof event.payload?.content === 'string' ? event.payload.content : '') ||
    (typeof event.payload?.response === 'string' ? event.payload.response : '') ||
    (typeof event.payload?.thought === 'string' ? event.payload.thought : '') ||
    ''
  );
}

function promptSections(event: DebugTimelineEvent) {
  const sections: Array<{ title: string; body: string }> = [];
  if (typeof event.payload?.system_prompt === 'string') {
    sections.push({ title: '系统提示词', body: event.payload.system_prompt });
  }
  if (typeof event.payload?.user_prompt === 'string') {
    sections.push({ title: '用户提示词', body: event.payload.user_prompt });
  }
  if (typeof event.payload?.prompt === 'string') {
    sections.push({ title: '提示词', body: event.payload.prompt });
  }
  if (typeof event.payload?.response === 'string') {
    sections.push({ title: '模型响应', body: event.payload.response });
  }
  if (!sections.length && eventMessage(event)) {
    sections.push({ title: '事件内容', body: eventMessage(event) });
  }
  return sections;
}

function handoffSummary(handoff: DebugHandoff) {
  return (
    handoff.summary ||
    (typeof handoff.payload?.summary === 'string' ? handoff.payload.summary : '') ||
    (typeof handoff.payload?.message === 'string' ? handoff.payload.message : '') ||
    '无摘要'
  );
}

export default function FlowDebugger() {
  const [tasks, setTasks] = useState<DebugTaskListItem[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [trace, setTrace] = useState<DebugTraceResponse | null>(null);
  const [selectedEventId, setSelectedEventId] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [agentFilter, setAgentFilter] = useState('all');
  const [phaseFilter, setPhaseFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [loadingTasks, setLoadingTasks] = useState(false);
  const [loadingTrace, setLoadingTrace] = useState(false);

  useEffect(() => {
    void loadTasks();
  }, [statusFilter]);

  useEffect(() => {
    if (!selectedTaskId) {
      setTrace(null);
      setSelectedEventId('');
      return;
    }
    void loadTrace(selectedTaskId);
  }, [selectedTaskId]);

  async function loadTasks() {
    setLoadingTasks(true);
    try {
      const data = await getDebugTasks(statusFilter === 'all' ? undefined : { status: statusFilter });
      setTasks(data);
      setSelectedTaskId((current) => {
        if (!data.length) return '';
        if (current && data.some((task) => task.id === current)) return current;
        return data[0].id;
      });
    } catch (error) {
      console.error(error);
      toast.error('加载调试任务失败');
    } finally {
      setLoadingTasks(false);
    }
  }

  async function loadTrace(taskId: string) {
    setLoadingTrace(true);
    try {
      const data = await getDebugTrace(taskId);
      setTrace(data);
      setSelectedEventId((current) => {
        if (current && data.timeline.some((event) => event.id === current)) return current;
        return data.timeline[0]?.id || '';
      });
    } catch (error) {
      console.error(error);
      toast.error('加载调试轨迹失败');
    } finally {
      setLoadingTrace(false);
    }
  }

  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === selectedTaskId) || null,
    [selectedTaskId, tasks],
  );

  const agents = useMemo(() => {
    if (!trace) return [];
    return Array.from(
      new Set(trace.timeline.map((event) => event.agent_type || event.agent_name).filter(Boolean)),
    ) as string[];
  }, [trace]);

  const phases = useMemo(() => {
    if (!trace) return [];
    return Array.from(new Set(trace.timeline.map((event) => event.phase).filter(Boolean))) as string[];
  }, [trace]);

  const timeline = useMemo(() => {
    if (!trace) return [];
    const keyword = query.trim().toLowerCase();
    return trace.timeline.filter((event) => {
      const agent = event.agent_type || event.agent_name || 'unknown';
      const phase = event.phase || 'unknown';
      if (agentFilter !== 'all' && agent !== agentFilter) return false;
      if (phaseFilter !== 'all' && phase !== phaseFilter) return false;
      if (!keyword) return true;
      return [
        event.event_type,
        event.agent_name,
        event.agent_type,
        event.phase,
        event.tool_name,
        eventMessage(event),
        toText(event.payload),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(keyword);
    });
  }, [trace, agentFilter, phaseFilter, query]);

  const selectedEvent = useMemo(() => {
    if (!trace) return null;
    return (
      timeline.find((event) => event.id === selectedEventId) ||
      trace.timeline.find((event) => event.id === selectedEventId) ||
      timeline[0] ||
      trace.timeline[0] ||
      null
    );
  }, [trace, timeline, selectedEventId]);

  const selectedMeta = selectedEvent ? metaOf(selectedEvent.event_type) : DEFAULT_META;

  return (
    <div className="min-h-[calc(100vh-5rem)] px-5 py-5 lg:px-8">
      <div className="mx-auto flex max-w-[1800px] flex-col gap-5">
        <section className="rounded-[28px] border border-border/70 bg-background/95 px-7 py-6 shadow-[0_18px_55px_rgba(15,23,42,0.06)]">
          <div className="flex flex-col gap-5 xl:flex-row xl:items-center xl:justify-between">
            <div className="space-y-2">
              <div className="inline-flex items-center gap-2 rounded-full border border-border/80 bg-background px-4 py-2 text-xs font-medium tracking-[0.18em] text-muted-foreground">
                <Activity className="h-4 w-4 text-primary" />
                流程调试
              </div>
              <h1 className="text-4xl font-semibold tracking-tight text-foreground">流程调试</h1>
              <p className="max-w-4xl text-sm leading-7 text-muted-foreground">
                选择任务后，可以查看完整的模型提示词、思考过程、工具调用和 Agent 交接链路。
              </p>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row">
              <Select value={statusFilter} onValueChange={setStatusFilter}>
                <SelectTrigger className="min-w-[170px] font-sans">
                  <SelectValue placeholder="全部状态" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部状态</SelectItem>
                  <SelectItem value="running">运行中</SelectItem>
                  <SelectItem value="completed">已完成</SelectItem>
                  <SelectItem value="failed">失败</SelectItem>
                  <SelectItem value="cancelled">已取消</SelectItem>
                </SelectContent>
              </Select>
              <Button variant="outline" onClick={() => void loadTasks()} disabled={loadingTasks} className="font-sans">
                <RefreshCcw className={cn('h-4 w-4', loadingTasks && 'animate-spin')} />
                刷新任务
              </Button>
            </div>
          </div>
        </section>

        <div className="grid gap-5 xl:grid-cols-[220px_minmax(0,1fr)]">
          <section className="rounded-[28px] border border-border/70 bg-background/95 p-5 shadow-[0_18px_55px_rgba(15,23,42,0.06)]">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <p className="text-xs font-medium tracking-[0.18em] text-muted-foreground">TASKS</p>
                <h2 className="mt-2 text-2xl font-semibold tracking-tight text-foreground">任务列表</h2>
              </div>
              <div className="inline-flex h-10 min-w-10 items-center justify-center rounded-full border border-primary/20 bg-primary/8 px-3 text-sm font-semibold text-primary">
                {tasks.length}
              </div>
            </div>

            <ScrollArea className="h-[calc(100vh-18rem)] pr-2">
              <div className="space-y-2.5">
                {!tasks.length && (
                  <div className="rounded-3xl border border-dashed border-border bg-muted/20 px-4 py-5 text-sm leading-7 text-muted-foreground">
                    还没有可调试的任务。先运行一次 Agent 审计，再回来查看完整轨迹。
                  </div>
                )}

                {tasks.map((task) => (
                  <button
                    key={task.id}
                    type="button"
                    onClick={() => setSelectedTaskId(task.id)}
                    className={cn(
                      'w-full overflow-hidden rounded-xl px-3 py-3 text-left transition-all',
                      task.id === selectedTaskId
                        ? 'bg-primary/8 text-primary'
                        : 'bg-transparent hover:bg-muted/35',
                    )}
                  >
                    <div
                      className={cn(
                        'truncate whitespace-nowrap text-base font-semibold',
                        task.id === selectedTaskId ? 'text-primary' : 'text-foreground',
                      )}
                      title={taskTitle(task)}
                    >
                      {taskTitle(task)}
                    </div>
                  </button>
                ))}
              </div>
            </ScrollArea>
          </section>

          <section className="min-w-0 space-y-5">
            <section className="rounded-[28px] border border-border/70 bg-background/95 p-5 shadow-[0_18px_55px_rgba(15,23,42,0.06)]">
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_120px_120px_120px]">
                <div className="space-y-2 rounded-3xl border border-border/70 bg-muted/10 px-4 py-3">
                  <p className="text-xs font-medium tracking-[0.18em] text-muted-foreground">当前任务</p>
                  <h2 className="text-2xl font-semibold tracking-tight text-foreground">
                    {selectedTask ? taskTitle(selectedTask) : '请选择任务'}
                  </h2>
                  <p className="text-sm text-muted-foreground">
                    状态：{trace?.task.status || selectedTask?.status || '暂无'}
                  </p>
                </div>
                <div className="rounded-3xl border border-border/70 bg-muted/10 px-4 py-3">
                  <div className="text-[11px] text-muted-foreground">事件总数</div>
                  <div className="mt-1 text-2xl font-semibold text-foreground">{trace?.summary.event_count ?? 0}</div>
                </div>
                <div className="rounded-3xl border border-border/70 bg-muted/10 px-4 py-3">
                  <div className="text-[11px] text-muted-foreground">工具调用</div>
                  <div className="mt-1 text-2xl font-semibold text-foreground">{trace?.summary.tool_calls ?? 0}</div>
                </div>
                <div className="rounded-3xl border border-border/70 bg-muted/10 px-4 py-3">
                  <div className="text-[11px] text-muted-foreground">交接次数</div>
                  <div className="mt-1 text-2xl font-semibold text-foreground">{trace?.summary.handoff_count ?? 0}</div>
                </div>
              </div>
            </section>

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.08fr)_minmax(0,0.92fr)]">
              <section className="min-w-0 rounded-[28px] border border-border/70 bg-background/95 p-5 shadow-[0_18px_55px_rgba(15,23,42,0.06)]">
                <div className="space-y-4">
                  <div className="space-y-3">
                    <div>
                      <p className="text-xs font-medium tracking-[0.18em] text-muted-foreground">时间线</p>
                      <h3 className="mt-2 text-2xl font-semibold tracking-tight text-foreground">完整流程</h3>
                    </div>

                    <div className="flex flex-wrap gap-3">
                      <Select value={agentFilter} onValueChange={setAgentFilter}>
                        <SelectTrigger className="w-[160px] font-sans">
                          <SelectValue placeholder="全部 Agent" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">全部 Agent</SelectItem>
                          {agents.map((agent) => (
                            <SelectItem key={agent} value={agent}>
                              {agent}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>

                      <Select value={phaseFilter} onValueChange={setPhaseFilter}>
                        <SelectTrigger className="w-[160px] font-sans">
                          <SelectValue placeholder="全部阶段" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">全部阶段</SelectItem>
                          {phases.map((phase) => (
                            <SelectItem key={phase} value={phase}>
                              {phase}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>

                      <div className="relative min-w-[240px] flex-1">
                        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                        <Input
                          value={query}
                          onChange={(event) => setQuery(event.target.value)}
                          placeholder="搜索事件、Agent、工具或内容"
                          className="pl-10 font-sans"
                        />
                      </div>
                    </div>
                  </div>

                    <ScrollArea className="h-[calc(100vh-31rem)] pr-2">
                    <div className="space-y-3">
                      {!timeline.length && (
                        <div className="rounded-3xl border border-dashed border-border bg-muted/20 px-5 py-6 text-sm leading-7 text-muted-foreground">
                          当前筛选条件下没有匹配的事件。
                        </div>
                      )}

                      {timeline.map((event) => {
                        const meta = metaOf(event.event_type);
                        return (
                          <button
                            key={event.id}
                            type="button"
                            onClick={() => setSelectedEventId(event.id)}
                            className={cn(
                              'w-full rounded-[20px] border bg-background px-4 py-4 text-left transition-all',
                              meta.border,
                              selectedEvent?.id === event.id && 'ring-2 ring-primary/30 shadow-[0_16px_28px_rgba(15,23,42,0.08)]',
                            )}
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                  <span>#{event.sequence}</span>
                                  <span>{event.agent_name || event.agent_type || 'unknown'}</span>
                                  <span>{formatWhen(event.timestamp)}</span>
                                </div>
                                <div className="mt-2 text-lg font-semibold text-foreground">{meta.label}</div>
                                <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-sm leading-7 text-foreground/90">
                                  {eventMessage(event) || '该事件没有可读文本。'}
                                </p>
                              </div>
                              <div className="flex shrink-0 flex-col items-end gap-2">
                                <span className={cn('rounded-full px-2.5 py-1 text-[11px] font-medium', meta.badge)}>
                                  {event.event_type}
                                </span>
                                {event.tool_name && (
                                  <span className="rounded-full border border-border bg-background px-2.5 py-1 text-[11px] text-muted-foreground">
                                    {event.tool_name}
                                  </span>
                                )}
                              </div>
                            </div>

                            <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                              <span>阶段：{event.phase || '-'}</span>
                              <span>Agent：{event.agent_type || event.agent_name || '-'}</span>
                              {event.provider && <span>Provider：{event.provider}</span>}
                              {event.model && <span>Model：{event.model}</span>}
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  </ScrollArea>
                </div>
              </section>

              <section className="min-w-0 rounded-[28px] border border-border/70 bg-background/95 p-5 shadow-[0_18px_55px_rgba(15,23,42,0.06)]">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-xs font-medium tracking-[0.18em] text-muted-foreground">事件阅读器</p>
                    <h3 className="mt-2 text-2xl font-semibold tracking-tight text-foreground">
                      {selectedEvent ? selectedMeta.label : '请选择事件'}
                    </h3>
                    <p className="mt-2 text-sm leading-7 text-muted-foreground">
                      可在这里切换查看摘要、提示词、详情和原始 JSON。
                    </p>
                  </div>
                  {selectedEvent && (
                    <span className={cn('rounded-full px-3 py-1.5 text-xs font-medium', selectedMeta.badge)}>
                      {selectedEvent.agent_name || selectedEvent.agent_type || 'unknown'}
                    </span>
                  )}
                </div>

                <Tabs defaultValue="readable" className="mt-5">
                  <TabsList className="grid h-auto w-full grid-cols-4 rounded-2xl border border-border bg-muted/20 p-1">
                    <TabsTrigger value="readable" className="h-10 rounded-xl font-sans text-sm normal-case tracking-normal">
                      摘要
                    </TabsTrigger>
                    <TabsTrigger value="prompt" className="h-10 rounded-xl font-sans text-sm normal-case tracking-normal">
                      提示词
                    </TabsTrigger>
                    <TabsTrigger value="details" className="h-10 rounded-xl font-sans text-sm normal-case tracking-normal">
                      详情
                    </TabsTrigger>
                    <TabsTrigger value="json" className="h-10 rounded-xl font-sans text-sm normal-case tracking-normal">
                      <FileJson className="h-4 w-4" />
                      JSON
                    </TabsTrigger>
                  </TabsList>

                  <TabsContent value="readable" className="mt-4">
                    <ScrollArea className="h-[calc(100vh-31rem)] rounded-[24px] border border-border/70 bg-muted/10 p-4">
                      <div className="space-y-4">
                        <div className="rounded-3xl border border-border/60 bg-background px-5 py-4">
                          <div className="text-xs text-muted-foreground">可读内容</div>
                          <div className="mt-3 whitespace-pre-wrap text-sm leading-8 text-foreground">
                            {selectedEvent ? eventMessage(selectedEvent) || '该事件没有可读文本。' : '请选择事件。'}
                          </div>
                        </div>
                        {selectedEvent?.tool_input && (
                          <div className="rounded-3xl border border-border/60 bg-background px-5 py-4">
                            <div className="text-xs text-muted-foreground">工具输入</div>
                            <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-sm leading-7 text-foreground">
                              {toText(selectedEvent.tool_input)}
                            </pre>
                          </div>
                        )}
                        {selectedEvent?.tool_output && (
                          <div className="rounded-3xl border border-border/60 bg-background px-5 py-4">
                            <div className="text-xs text-muted-foreground">工具输出</div>
                            <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-sm leading-7 text-foreground">
                              {toText(selectedEvent.tool_output)}
                            </pre>
                          </div>
                        )}
                      </div>
                    </ScrollArea>
                  </TabsContent>

                  <TabsContent value="prompt" className="mt-4">
                    <ScrollArea className="h-[calc(100vh-31rem)] rounded-[24px] border border-border/70 bg-muted/10 p-4">
                      <div className="space-y-4">
                        {selectedEvent && promptSections(selectedEvent).length ? (
                          promptSections(selectedEvent).map((section) => (
                            <div key={section.title} className="rounded-3xl border border-border/60 bg-background px-5 py-4">
                              <div className="text-xs text-muted-foreground">{section.title}</div>
                              <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-sm leading-7 text-foreground">
                                {section.body}
                              </pre>
                            </div>
                          ))
                        ) : (
                          <div className="rounded-3xl border border-dashed border-border bg-background px-5 py-5 text-sm text-muted-foreground">
                            当前事件没有提示词内容。
                          </div>
                        )}
                      </div>
                    </ScrollArea>
                  </TabsContent>

                  <TabsContent value="details" className="mt-4">
                    <ScrollArea className="h-[calc(100vh-31rem)] rounded-[24px] border border-border/70 bg-muted/10 p-4">
                      <div className="space-y-3">
                        {selectedEvent
                          ? [
                              ['事件类型', selectedEvent.event_type],
                              ['序号', String(selectedEvent.sequence)],
                              ['Agent', selectedEvent.agent_name || selectedEvent.agent_type || '-'],
                              ['阶段', selectedEvent.phase || '-'],
                              ['轮次', selectedEvent.iteration != null ? String(selectedEvent.iteration) : '-'],
                              ['Provider', selectedEvent.provider || '-'],
                              ['Model', selectedEvent.model || '-'],
                              ['工具', selectedEvent.tool_name || '-'],
                              ['时间', formatWhen(selectedEvent.timestamp)],
                            ].map(([label, value]) => (
                              <div
                                key={label}
                                className="grid grid-cols-[96px_minmax(0,1fr)] gap-4 rounded-2xl border border-border/60 bg-background px-4 py-3"
                              >
                                <div className="text-xs text-muted-foreground">{label}</div>
                                <div className="break-all text-sm text-foreground">{value}</div>
                              </div>
                            ))
                          : null}
                      </div>
                    </ScrollArea>
                  </TabsContent>

                  <TabsContent value="json" className="mt-4">
                    <ScrollArea className="h-[calc(100vh-31rem)] rounded-[24px] border border-slate-800 bg-slate-950 p-4">
                      <pre className="overflow-x-auto whitespace-pre-wrap text-sm leading-7 text-slate-100">
                        {selectedEvent ? toText(selectedEvent) : '请选择事件。'}
                      </pre>
                    </ScrollArea>
                  </TabsContent>
                </Tabs>
              </section>
            </div>

            <section className="rounded-[28px] border border-border/70 bg-background/95 p-5 shadow-[0_18px_55px_rgba(15,23,42,0.06)]">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                  <p className="text-xs font-medium tracking-[0.18em] text-muted-foreground">Agent 交接</p>
                  <h3 className="mt-2 text-2xl font-semibold tracking-tight text-foreground">通信与传递</h3>
                  <p className="mt-2 text-sm leading-7 text-muted-foreground">
                    这里展示 Agent 之间的交接内容，便于追踪任务如何在各阶段传递。
                  </p>
                </div>
                <div className="rounded-full border border-border bg-background px-4 py-2 text-sm text-muted-foreground">
                  {(trace?.handoffs || []).length} 次交接
                </div>
              </div>

              <ScrollArea className="mt-5 h-[240px] pr-2">
                <div className="grid gap-4 xl:grid-cols-2">
                  {(trace?.handoffs || []).map((handoff) => (
                    <button
                      key={handoff.event_id}
                      type="button"
                      onClick={() => setSelectedEventId(handoff.event_id)}
                      className="rounded-[20px] border border-border/70 bg-background px-5 py-4 text-left transition-all hover:border-primary/25 hover:bg-muted/15"
                    >
                      <div className="flex items-center gap-3 text-base font-semibold text-foreground">
                        <span>{handoff.from_agent || 'unknown'}</span>
                        <ArrowRight className="h-4 w-4 text-primary" />
                        <span>{handoff.to_agent || 'unknown'}</span>
                      </div>
                      <div className="mt-2 text-xs text-muted-foreground">{formatWhen(handoff.timestamp)}</div>
                      <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-foreground/90">
                        {handoffSummary(handoff)}
                      </p>
                    </button>
                  ))}
                  {trace && !trace.handoffs.length && (
                    <div className="rounded-3xl border border-dashed border-border bg-muted/20 px-5 py-6 text-sm leading-7 text-muted-foreground">
                      当前任务没有 Agent 交接事件。
                    </div>
                  )}
                </div>
              </ScrollArea>
            </section>
          </section>
        </div>
      </div>
    </div>
  );
}
