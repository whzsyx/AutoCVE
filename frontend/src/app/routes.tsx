import type { ReactNode } from 'react';

import Account from '@/pages/Account';
import AdminDashboard from '@/pages/AdminDashboard';
import AgentAudit from '@/pages/AgentAudit';
import AgentDirectAudit from '@/pages/AgentDirectAudit';
import AuditRules from '@/pages/AuditRules';
import AuditSession from '@/pages/AuditSession';
import AuditTasks from '@/pages/AuditTasks';
import Dashboard from '@/pages/Dashboard';
import FlowDebugger from '@/pages/FlowDebugger';
import ProjectDetail from '@/pages/ProjectDetail';
import Projects from '@/pages/Projects';
import PromptManager from '@/pages/PromptManager';
import RecycleBin from '@/pages/RecycleBin';
import ReportTemplatesPage from '@/pages/ReportTemplatesPage';
import SkillsManager from '@/pages/SkillsManager';
import TaskDetail from '@/pages/TaskDetail';
import VulnerabilityManagement from '@/pages/VulnerabilityManagement';

export interface RouteConfig {
  name: string;
  path: string;
  element: ReactNode;
  visible?: boolean;
}

const routes: RouteConfig[] = [
  { name: 'Agent审计', path: '/', element: <AgentAudit />, visible: true },
  { name: 'Agent审计详情', path: '/agent-audit/:taskId', element: <AgentAudit />, visible: false },
  { name: '审计会话', path: '/audit-sessions/:sessionId', element: <AuditSession />, visible: false },
  { name: '仪表盘', path: '/dashboard', element: <Dashboard />, visible: true },
  { name: '项目管理', path: '/projects', element: <Projects />, visible: true },
  { name: '项目详情', path: '/projects/:id', element: <ProjectDetail />, visible: false },
  { name: 'Agent直审', path: '/agent-direct-audit', element: <AgentDirectAudit />, visible: true },
  { name: '审计任务', path: '/audit-tasks', element: <AuditTasks />, visible: true },
  { name: '任务详情', path: '/tasks/:id', element: <TaskDetail />, visible: false },
  { name: '审计规则', path: '/audit-rules', element: <AuditRules />, visible: true },
  { name: '提示词管理', path: '/prompts', element: <PromptManager />, visible: true },
  { name: 'Skills管理', path: '/skills', element: <SkillsManager />, visible: true },
  { name: '报告模板', path: '/report-templates', element: <ReportTemplatesPage />, visible: true },
  { name: '漏洞管理', path: '/vulnerabilities', element: <VulnerabilityManagement />, visible: true },
  { name: '流程调试', path: '/flow-debugger', element: <FlowDebugger />, visible: true },
  { name: '模型管理', path: '/admin', element: <AdminDashboard />, visible: true },
  { name: '回收站', path: '/recycle-bin', element: <RecycleBin />, visible: true },
  { name: '账号管理', path: '/account', element: <Account />, visible: false },
];

export default routes;
