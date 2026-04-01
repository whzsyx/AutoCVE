import { useEffect, useState } from 'react';
import {
  Activity,
  Cpu,
  GitBranchPlus,
  Radar,
  RefreshCw,
  Route,
  Save,
  SearchCheck,
  ShieldCheck,
  ShieldOff,
  Sparkles,
  Zap,
} from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import {
  type WorkflowAgentKey,
  type WorkflowConfig,
  getModelConfig,
  saveModelConfig,
} from '@/shared/api/modelConfig';
import { cn } from '@/shared/utils/utils';

type GraphNodeState = 'active' | 'disabled' | 'blocked';

const NODE_WIDTH = 172;
const NODE_HEIGHT = 92;
const NODE_OUTER_WIDTH = 188;
const NODE_OUTER_HEIGHT = 106;
const NODE_GLOW_WIDTH = 180;
const NODE_GLOW_HEIGHT = 98;

const WORKFLOW_ORDER: WorkflowAgentKey[] = [
  'orchestrator',
  'recon',
  'scan',
  'triage',
  'finding',
  'verification',
];

const ALL_EDGES: Array<{ source: WorkflowAgentKey; target: WorkflowAgentKey }> = [
  { source: 'orchestrator', target: 'recon' },
  { source: 'recon', target: 'scan' },
  { source: 'scan', target: 'triage' },
  { source: 'recon', target: 'finding' },
  { source: 'triage', target: 'verification' },
  { source: 'finding', target: 'verification' },
];

const AGENT_META: Record<
  WorkflowAgentKey,
  {
    title: string;
    label: string;
    description: string;
    icon: typeof GitBranchPlus;
    x: number;
    y: number;
  }
> = {
  orchestrator: {
    title: 'Orchestrator',
    label: '调度核心',
    description: '统一拆解任务、分发上下文，并决定后续路由分支。',
    icon: GitBranchPlus,
    x: 132,
    y: 220,
  },
  recon: {
    title: 'Recon',
    label: '信息收集',
    description: '采集目标特征、资产线索与先验信息，给后续节点提供基础输入。',
    icon: Radar,
    x: 360,
    y: 220,
  },
  scan: {
    title: 'Scan',
    label: '工具扫描',
    description: '进行自动化探测、快速枚举和工具侧信号收集。',
    icon: SearchCheck,
    x: 610,
    y: 116,
  },
  triage: {
    title: 'Triage',
    label: '误报过滤',
    description: '过滤扫描结果中的低价值噪音，保留更可信的漏洞候选。',
    icon: ShieldCheck,
    x: 840,
    y: 116,
  },
  finding: {
    title: 'Finding',
    label: '深度挖掘',
    description: '沿着侦察线索聚焦漏洞成因、触发路径与利用价值。',
    icon: Sparkles,
    x: 702,
    y: 324,
  },
  verification: {
    title: 'Verification',
    label: '最终验证',
    description: '对上游发现结果进行复核和确认，输出更稳定的结论。',
    icon: ShieldOff,
    x: 1074,
    y: 220,
  },
};

function createDefaultWorkflowConfig(): WorkflowConfig {
  return {
    agentStates: {
      orchestrator: { enabled: true, locked: true },
      recon: { enabled: true, locked: true },
      scan: { enabled: true, locked: false },
      triage: { enabled: true, locked: false },
      finding: { enabled: true, locked: false },
      verification: { enabled: true, locked: false },
    },
  };
}

function normalizeWorkflowConfig(input?: Partial<WorkflowConfig> | null): WorkflowConfig {
  const defaults = createDefaultWorkflowConfig();
  const normalized: WorkflowConfig = {
    agentStates: { ...defaults.agentStates },
  };

  const incomingStates = input?.agentStates;
  for (const agent of WORKFLOW_ORDER) {
    const state = incomingStates?.[agent];
    normalized.agentStates[agent] = {
      enabled: state?.enabled ?? defaults.agentStates[agent].enabled,
      locked: defaults.agentStates[agent].locked,
    };
  }

  normalized.agentStates.orchestrator.enabled = true;
  normalized.agentStates.recon.enabled = true;

  return normalized;
}

function computeEffectiveWorkflow(workflowConfig: WorkflowConfig) {
  const configured = normalizeWorkflowConfig(workflowConfig).agentStates;
  const effective: Record<WorkflowAgentKey, boolean> = {
    orchestrator: true,
    recon: true,
    scan: configured.scan.enabled,
    triage: configured.scan.enabled && configured.triage.enabled,
    finding: configured.finding.enabled,
    verification:
      configured.verification.enabled &&
      (configured.finding.enabled || (configured.scan.enabled && configured.triage.enabled)),
  };

  const activeEdgeKeys = new Set<string>(['orchestrator->recon']);
  if (effective.scan) activeEdgeKeys.add('recon->scan');
  if (effective.triage) activeEdgeKeys.add('scan->triage');
  if (effective.finding) activeEdgeKeys.add('recon->finding');
  if (effective.verification && effective.triage) activeEdgeKeys.add('triage->verification');
  if (effective.verification && effective.finding) activeEdgeKeys.add('finding->verification');

  const graphNodes = WORKFLOW_ORDER.map((agent) => {
    const configuredEnabled = configured[agent].enabled;
    const effectiveEnabled = effective[agent];

    let state: GraphNodeState = 'active';
    if (!configuredEnabled) {
      state = 'disabled';
    } else if (!effectiveEnabled) {
      state = 'blocked';
    }

    return {
      agent,
      ...AGENT_META[agent],
      configuredEnabled,
      effectiveEnabled,
      isLocked: Boolean(configured[agent].locked),
      state,
    };
  });

  return {
    graphNodes,
    activeEdges: ALL_EDGES.filter((edge) => activeEdgeKeys.has(`${edge.source}->${edge.target}`)),
    enabledCount: graphNodes.filter((node) => node.configuredEnabled).length,
    blockedCount: graphNodes.filter((node) => node.state === 'blocked').length,
    branchCount: Number(effective.triage) + Number(effective.finding),
  };
}

function getNodeTone(state: GraphNodeState) {
  if (state === 'active') {
    return {
      shell: '#cfe6db',
      stroke: '#93cbb5',
      fill: 'rgba(248,253,251,.96)',
      title: '#223a31',
      subtitle: '#648276',
      glow: 'rgba(150,215,188,.32)',
      glyph: '#4d7a69',
    };
  }

  if (state === 'blocked') {
    return {
      shell: '#dce7e2',
      stroke: '#bccfc7',
      fill: 'rgba(248,251,250,.92)',
      title: '#557368',
      subtitle: '#7d958c',
      glow: 'rgba(191,210,202,.22)',
      glyph: '#789187',
    };
  }

  return {
    shell: '#e7ece9',
    stroke: '#d6ddd9',
    fill: 'rgba(243,246,244,.92)',
    title: '#8a9691',
    subtitle: '#a1aba7',
    glow: 'rgba(222,227,224,.16)',
    glyph: '#9da8a4',
  };
}

function buildEdgePath(source: WorkflowAgentKey, target: WorkflowAgentKey) {
  const start = AGENT_META[source];
  const end = AGENT_META[target];
  const startX = start.x + NODE_OUTER_WIDTH / 2;
  const endX = end.x - NODE_OUTER_WIDTH / 2;
  const startY = start.y;
  const endY = end.y;

  if (source === 'orchestrator' && target === 'recon') {
    return `M ${startX} ${startY} L ${endX} ${endY}`;
  }

  if (source === 'recon' && target === 'scan') {
    return `M ${startX} ${startY} C ${startX + 64} ${startY}, ${endX - 78} ${endY}, ${endX} ${endY}`;
  }

  if (source === 'scan' && target === 'triage') {
    return `M ${startX} ${startY} L ${endX} ${endY}`;
  }

  if (source === 'recon' && target === 'finding') {
    return `M ${startX} ${startY} C ${startX + 74} ${startY}, ${endX - 86} ${endY}, ${endX} ${endY}`;
  }

  if (source === 'triage' && target === 'verification') {
    return `M ${startX} ${startY} C ${startX + 62} ${startY}, ${endX - 74} ${endY}, ${endX} ${endY}`;
  }

  if (source === 'finding' && target === 'verification') {
    return `M ${startX} ${startY} C ${startX + 72} ${startY}, ${endX - 82} ${endY}, ${endX} ${endY}`;
  }

  return `M ${startX} ${startY} L ${endX} ${endY}`;
}

function getStateLabel(state: GraphNodeState) {
  if (state === 'active') return 'ACTIVE';
  if (state === 'blocked') return 'SKIPPED';
  return 'OFFLINE';
}

function getStateDescription(state: GraphNodeState) {
  if (state === 'active') return '当前节点会进入真实执行链路。';
  if (state === 'blocked') return '节点本身开启，但会因上游依赖关闭而被自动跳过。';
  return '节点被手动关闭，后续任务不会进入该步骤。';
}

function getCardTone(state: GraphNodeState) {
  if (state === 'active') {
    return 'border-[rgba(182,221,206,.96)] bg-[linear-gradient(180deg,rgba(252,255,253,.98),rgba(243,250,247,.98))] shadow-[0_20px_38px_rgba(144,202,177,.12)]';
  }

  if (state === 'blocked') {
    return 'border-[rgba(210,222,216,.96)] bg-[linear-gradient(180deg,rgba(250,252,251,.98),rgba(244,247,246,.98))] shadow-[0_14px_28px_rgba(120,142,132,.06)]';
  }

  return 'border-[rgba(223,228,225,.96)] bg-[linear-gradient(180deg,rgba(246,248,247,.96),rgba(241,243,242,.98))] shadow-none';
}

export function WorkflowManager() {
  const [workflowConfig, setWorkflowConfig] = useState<WorkflowConfig>(createDefaultWorkflowConfig());
  const [initialSnapshot, setInitialSnapshot] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const effectiveWorkflow = computeEffectiveWorkflow(workflowConfig);
  const isDirty = JSON.stringify(workflowConfig) !== initialSnapshot;

  const loadWorkflow = async () => {
    try {
      setLoading(true);
      const response = await getModelConfig();
      const nextWorkflow = normalizeWorkflowConfig(response.otherConfig?.workflowConfig);
      setWorkflowConfig(nextWorkflow);
      setInitialSnapshot(JSON.stringify(nextWorkflow));
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || error?.message || '读取工作流配置失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadWorkflow();
  }, []);

  const persistWorkflow = async (
    nextWorkflow: WorkflowConfig,
    options?: { successMessage?: string; revertWorkflow?: WorkflowConfig },
  ) => {
    try {
      setSaving(true);
      const normalized = normalizeWorkflowConfig(nextWorkflow);
      await saveModelConfig({
        otherConfig: {
          workflowConfig: normalized,
        },
      });
      setWorkflowConfig(normalized);
      setInitialSnapshot(JSON.stringify(normalized));
      if (options?.successMessage) {
        toast.success(options.successMessage);
      }
    } catch (error: any) {
      if (options?.revertWorkflow) {
        setWorkflowConfig(options.revertWorkflow);
      }
      toast.error(error?.response?.data?.detail || error?.message || '保存工作流失败');
    } finally {
      setSaving(false);
    }
  };

  const updateAgentState = (agent: WorkflowAgentKey, enabled: boolean) => {
    const previous = workflowConfig;
    const nextWorkflow: WorkflowConfig = {
      agentStates: {
        ...previous.agentStates,
        [agent]: {
          ...previous.agentStates[agent],
          enabled,
        },
      },
    };
    setWorkflowConfig(nextWorkflow);
    void persistWorkflow(nextWorkflow, {
      successMessage: `${AGENT_META[agent].title} workflow updated`,
      revertWorkflow: previous,
    });
  };

  const handleReset = () => {
    const previous = workflowConfig;
    const nextWorkflow = createDefaultWorkflowConfig();
    setWorkflowConfig(nextWorkflow);
    void persistWorkflow(nextWorkflow, {
      successMessage: 'Workflow reset to defaults',
      revertWorkflow: previous,
    });
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      const normalized = normalizeWorkflowConfig(workflowConfig);
      await saveModelConfig({
        otherConfig: {
          workflowConfig: normalized,
        },
      });
      setWorkflowConfig(normalized);
      setInitialSnapshot(JSON.stringify(normalized));
      toast.success('默认工作流已保存');
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || error?.message || '保存工作流失败');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-[360px] items-center justify-center text-[#6f8379]">
        正在载入工作流配置...
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <section className="relative overflow-hidden rounded-[32px] border border-[rgba(214,223,218,.95)] bg-[linear-gradient(135deg,rgba(252,253,252,.98),rgba(244,248,246,.98)_55%,rgba(248,250,249,.98))] shadow-[0_24px_60px_rgba(108,131,119,.1)]">
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(rgba(151,198,179,.08)_1px,transparent_1px),linear-gradient(90deg,rgba(151,198,179,.08)_1px,transparent_1px)] bg-[size:30px_30px] opacity-40" />
        <div className="pointer-events-none absolute inset-y-0 right-0 w-[28rem] bg-[radial-gradient(circle_at_top_right,rgba(164,225,198,.22),transparent_68%)]" />

        <div className="relative space-y-6 p-7">
          <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
            <div className="max-w-4xl space-y-4">
              <div className="inline-flex items-center gap-2 rounded-full border border-[rgba(153,187,174,.42)] bg-white/82 px-4 py-1 text-xs uppercase tracking-[0.24em] text-[#6a877c]">
                <Route className="h-3.5 w-3.5" />
                Workflow Control Deck
              </div>

              <div className="space-y-3">
                <h2 className="text-4xl font-black tracking-tight text-[#20352d]">工作流管理</h2>
                <p className="max-w-4xl text-sm leading-8 text-[#577166]">
                  用可视化方式直接控制漏洞挖掘路径。关闭某个调试节点后，后端新任务会自动跳过它，并根据剩余有效分支重新拼接执行链路，避免无效消耗 token。
                </p>
              </div>
            </div>

            <div className="flex flex-wrap gap-3 xl:justify-end">
              <Button
                variant="outline"
                className="h-11 rounded-full border-[#d9e4de] bg-white/85"
                onClick={() => void loadWorkflow()}
                disabled={loading || saving}
              >
                <RefreshCw className="mr-2 h-4 w-4" />
                刷新状态
              </Button>
              <Button
                variant="outline"
                className="h-11 rounded-full border-[#d9e4de] bg-white/85"
                onClick={handleReset}
                disabled={saving}
              >
                全部恢复默认
              </Button>
              <Button
                className="h-11 rounded-full border border-[#9ed0bb] bg-[linear-gradient(135deg,#8dcab1,#a8dcc8)] text-[#173128] shadow-[0_18px_32px_rgba(143,205,180,.28)] hover:bg-[linear-gradient(135deg,#84c4aa,#9dd5c0)]"
                onClick={() => void handleSave()}
                disabled={saving || !isDirty}
              >
                <Save className="mr-2 h-4 w-4" />
                {saving ? '保存中...' : '保存为默认工作流'}
              </Button>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <div className="rounded-[24px] border border-[rgba(208,223,216,.96)] bg-white/76 p-5 shadow-[0_16px_32px_rgba(110,129,120,.08)] backdrop-blur-sm">
              <div className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-[#7d9a8f]">
                <Cpu className="h-4 w-4" />
                已启用节点
              </div>
              <div className="mt-3 text-5xl font-black text-[#22352d]">{effectiveWorkflow.enabledCount}</div>
              <div className="mt-2 text-sm text-[#668177]">当前配置中处于开启状态的节点数量</div>
            </div>

            <div className="rounded-[24px] border border-[rgba(208,223,216,.96)] bg-white/76 p-5 shadow-[0_16px_32px_rgba(110,129,120,.08)] backdrop-blur-sm">
              <div className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-[#7d9a8f]">
                <Activity className="h-4 w-4" />
                依赖跳过
              </div>
              <div className="mt-3 text-5xl font-black text-[#22352d]">{effectiveWorkflow.blockedCount}</div>
              <div className="mt-2 text-sm text-[#668177]">节点开启但因上游关闭而被自动跳过</div>
            </div>

            <div className="rounded-[24px] border border-[rgba(208,223,216,.96)] bg-white/76 p-5 shadow-[0_16px_32px_rgba(110,129,120,.08)] backdrop-blur-sm">
              <div className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-[#7d9a8f]">
                <Zap className="h-4 w-4" />
                活跃分支
              </div>
              <div className="mt-3 text-5xl font-black text-[#22352d]">{effectiveWorkflow.branchCount}</div>
              <div className="mt-2 text-sm text-[#668177]">本轮真正参与执行的漏洞挖掘分支数量</div>
            </div>
          </div>
        </div>
      </section>

      <section className="overflow-hidden rounded-[32px] border border-[rgba(215,223,218,.96)] bg-[linear-gradient(180deg,rgba(249,252,251,.98),rgba(241,246,244,.98))] shadow-[0_24px_58px_rgba(102,126,115,.11)]">
        <div className="flex flex-col gap-4 border-b border-[rgba(214,225,220,.92)] px-6 py-5 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-3xl space-y-2">
            <h3 className="text-3xl font-black text-[#24372f]">动态图谱</h3>
            <p className="text-sm leading-7 text-[#617d71]">
              亮色链路表示后端本次会真实执行的有效路径。浅灰节点表示手动关闭，雾灰节点表示当前仍开启，但会因为依赖断开而在运行时被自动跳过。
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            <Badge
              variant="outline"
              className="rounded-full border-[#9dd0bc] bg-[#effaf5] px-3 normal-case tracking-normal text-[#4c7b6b]"
            >
              亮色 = 实际执行
            </Badge>
            <Badge
              variant="outline"
              className="rounded-full border-[#c9d9d2] bg-[#f8fbfa] px-3 normal-case tracking-normal text-[#7b948a]"
            >
              雾灰 = 依赖跳过
            </Badge>
            <Badge
              variant="outline"
              className="rounded-full border-[#d8e1dd] bg-[#f2f5f4] px-3 normal-case tracking-normal text-[#8f9f99]"
            >
              浅灰 = 手动关闭
            </Badge>
          </div>
        </div>

        <div className="relative overflow-hidden px-3 py-4 sm:px-6 sm:py-6">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_0%,rgba(164,226,200,.16),transparent_38%),radial-gradient(circle_at_100%_52%,rgba(177,228,208,.14),transparent_32%)]" />
          <div className="pointer-events-none absolute inset-4 rounded-[28px] border border-[rgba(195,218,208,.42)] bg-[linear-gradient(180deg,rgba(255,255,255,.46),rgba(255,255,255,.12))] shadow-[inset_0_1px_0_rgba(255,255,255,.75)]" />

          <svg viewBox="0 0 1220 440" className="relative z-10 w-full">
            <defs>
              <pattern id="workflow-grid" width="28" height="28" patternUnits="userSpaceOnUse">
                <path
                  d="M 28 0 L 0 0 0 28"
                  fill="none"
                  stroke="rgba(153,191,176,.22)"
                  strokeWidth="1"
                />
              </pattern>
              <filter id="workflow-glow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="10" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
              <marker
                id="workflow-arrow-active"
                viewBox="0 0 10 10"
                refX="8"
                refY="5"
                markerWidth="7"
                markerHeight="7"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#92d6bb" />
              </marker>
              <marker
                id="workflow-arrow-inactive"
                viewBox="0 0 10 10"
                refX="8"
                refY="5"
                markerWidth="7"
                markerHeight="7"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#cfd8d4" />
              </marker>
            </defs>

            <rect x="18" y="18" width="1184" height="404" rx="28" fill="url(#workflow-grid)" opacity="0.9" />

            {ALL_EDGES.map((edge) => {
              const path = buildEdgePath(edge.source, edge.target);
              const isActive = effectiveWorkflow.activeEdges.some(
                (item) => item.source === edge.source && item.target === edge.target,
              );

              return (
                <g key={`${edge.source}-${edge.target}`}>
                  <path
                    d={path}
                    fill="none"
                    stroke={isActive ? 'rgba(146,214,187,.2)' : 'rgba(204,214,210,.18)'}
                    strokeWidth={isActive ? 10 : 6}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    filter={isActive ? 'url(#workflow-glow)' : undefined}
                  />
                  <path
                    d={path}
                    fill="none"
                    stroke={isActive ? '#92d6bb' : '#cfd8d4'}
                    strokeWidth={isActive ? 3.4 : 2.2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeDasharray={isActive ? '12 7' : '4 8'}
                    opacity={isActive ? 1 : 0.84}
                    markerEnd={isActive ? 'url(#workflow-arrow-active)' : 'url(#workflow-arrow-inactive)'}
                  >
                    {isActive ? (
                      <animate
                        attributeName="stroke-dashoffset"
                        values="0;-38"
                        dur="1.35s"
                        repeatCount="indefinite"
                      />
                    ) : null}
                  </path>
                </g>
              );
            })}

            {effectiveWorkflow.graphNodes.map((node) => {
              const tone = getNodeTone(node.state);

              return (
                <g key={node.agent}>
                  <ellipse cx={node.x} cy={node.y + 52} rx="86" ry="14" fill={tone.glow} opacity={0.56} />
                  <rect
                    x={node.x - NODE_GLOW_WIDTH / 2}
                    y={node.y - NODE_GLOW_HEIGHT / 2}
                    rx="26"
                    width={NODE_GLOW_WIDTH}
                    height={NODE_GLOW_HEIGHT}
                    fill={tone.glow}
                    opacity={0.5}
                  />
                  <rect
                    x={node.x - NODE_OUTER_WIDTH / 2}
                    y={node.y - NODE_OUTER_HEIGHT / 2}
                    rx="24"
                    width={NODE_OUTER_WIDTH}
                    height={NODE_OUTER_HEIGHT}
                    fill={tone.fill}
                    stroke={tone.shell}
                    strokeWidth="1.2"
                  />
                  <rect
                    x={node.x - NODE_WIDTH / 2}
                    y={node.y - NODE_HEIGHT / 2}
                    rx="22"
                    width={NODE_WIDTH}
                    height={NODE_HEIGHT}
                    fill="rgba(255,255,255,.52)"
                    stroke={tone.stroke}
                    strokeWidth={node.state === 'active' ? 2 : 1.5}
                    strokeDasharray={node.state === 'blocked' ? '5 7' : undefined}
                  />
                  <path
                    d={`M ${node.x - 54} ${node.y - 18} L ${node.x - 16} ${node.y - 18}`}
                    stroke={tone.stroke}
                    strokeWidth="1.8"
                    strokeLinecap="round"
                  />
                  <path
                    d={`M ${node.x + 16} ${node.y - 18} L ${node.x + 54} ${node.y - 18}`}
                    stroke={tone.stroke}
                    strokeWidth="1.8"
                    strokeLinecap="round"
                  />
                  {node.state === 'active' ? (
                    <>
                      <circle cx={node.x - 50} cy={node.y - 19} r="4" fill="#92d6bb">
                        <animate attributeName="opacity" values="1;.35;1" dur="1.2s" repeatCount="indefinite" />
                      </circle>
                      <circle cx={node.x + 50} cy={node.y - 19} r="4" fill="#92d6bb">
                        <animate attributeName="opacity" values=".35;1;.35" dur="1.2s" repeatCount="indefinite" />
                      </circle>
                    </>
                  ) : null}
                  <text x={node.x} y={node.y - 2} textAnchor="middle" fill={tone.title} fontSize="20" fontWeight="700">
                    {node.title}
                  </text>
                  <text
                    x={node.x}
                    y={node.y + 16}
                    textAnchor="middle"
                    fill={tone.subtitle}
                    fontSize="11"
                    letterSpacing="2.6"
                  >
                    {getStateLabel(node.state)}
                  </text>
                  <text x={node.x} y={node.y + 34} textAnchor="middle" fill={tone.glyph} fontSize="12">
                    {node.label}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-2">
        {effectiveWorkflow.graphNodes.map((node) => {
          const Icon = node.icon;

          return (
            <article
              key={node.agent}
              className={cn(
                'relative overflow-hidden rounded-[28px] border p-6 transition-all duration-200',
                getCardTone(node.state),
              )}
            >
              <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(154,221,196,.14),transparent_44%)]" />

              <div className="relative flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                <div className="space-y-3">
                  <div className="flex items-center gap-3">
                    <div
                      className={cn(
                        'flex h-12 w-12 items-center justify-center rounded-2xl border',
                        node.state === 'active'
                          ? 'border-[#b9dfcf] bg-[#effaf5] text-[#4b7a6a]'
                          : node.state === 'blocked'
                            ? 'border-[#d4e1db] bg-[#f8fbfa] text-[#7e958c]'
                            : 'border-[#e0e5e3] bg-white/70 text-[#9ca8a3]',
                      )}
                    >
                      <Icon className="h-5 w-5" />
                    </div>

                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-2xl font-bold text-[#24372f]">{node.title}</h3>
                        {node.isLocked ? (
                          <Badge
                            variant="outline"
                            className="rounded-full border-[#c7d8d1] bg-[#f1f8f5] px-3 normal-case tracking-normal text-[#648375]"
                          >
                            核心常开
                          </Badge>
                        ) : null}
                      </div>
                      <p className="mt-1 text-sm text-[#647e73]">{node.description}</p>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <Badge
                      variant="outline"
                      className="rounded-full border-[#d0ddd7] bg-white px-3 normal-case tracking-normal text-[#688277]"
                    >
                      配置状态：{node.configuredEnabled ? '开启' : '关闭'}
                    </Badge>
                    <Badge
                      variant="outline"
                      className={cn(
                        'rounded-full px-3 normal-case tracking-normal',
                        node.state === 'active'
                          ? 'border-[#a8d8c5] bg-[#eef9f4] text-[#497465]'
                          : node.state === 'blocked'
                            ? 'border-[#d0ddd7] bg-[#f7faf9] text-[#7d948b]'
                            : 'border-[#dde3e0] bg-[#f2f5f4] text-[#98a4a0]',
                      )}
                    >
                      运行状态：{getStateLabel(node.state)}
                    </Badge>
                  </div>
                </div>

                <div className="flex items-center gap-3 rounded-full border border-[rgba(210,222,216,.95)] bg-white/82 px-4 py-2">
                  <span className="text-sm font-medium text-[#5d766c]">节点开关</span>
                  <Switch
                    checked={node.configuredEnabled}
                    onCheckedChange={(checked) => updateAgentState(node.agent, checked)}
                    disabled={node.isLocked || saving}
                  />
                </div>
              </div>

              <div className="relative mt-5 rounded-2xl border border-[rgba(214,223,218,.96)] bg-white/72 px-4 py-3 text-sm leading-7 text-[#738880]">
                {getStateDescription(node.state)}
              </div>
            </article>
          );
        })}
      </section>
    </div>
  );
}
