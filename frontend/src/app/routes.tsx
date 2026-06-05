import type { ReactNode } from 'react';

import Account from '@/pages/Account';
import AdminDashboard from '@/pages/AdminDashboard';
import AgentAudit from '@/pages/AgentAudit';
import AuditSession from '@/pages/AuditSession';
import AuditTasks from '@/pages/AuditTasks';
import CheckmarxScan from '@/pages/CheckmarxScan';
import Dashboard from '@/pages/Dashboard';
import ProjectDetail from '@/pages/ProjectDetail';
import Projects from '@/pages/Projects';
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

const enableCheckmarxScan = import.meta.env.VITE_ENABLE_CHECKMARX_SCAN === 'true';

const routes: RouteConfig[] = [
  { name: '首页', path: '/', element: <AgentAudit />, visible: true },
  { name: 'Agent审计详情', path: '/agent-audit/:taskId', element: <AgentAudit />, visible: false },
  { name: '审计会话', path: '/audit-sessions/:sessionId', element: <AuditSession />, visible: false },
  { name: '仪表盘', path: '/dashboard', element: <Dashboard />, visible: true },
  { name: '项目管理', path: '/projects', element: <Projects />, visible: true },
  { name: '项目详情', path: '/projects/:id', element: <ProjectDetail />, visible: false },
  { name: '审计任务', path: '/audit-tasks', element: <AuditTasks />, visible: true },
  { name: '任务详情', path: '/tasks/:id', element: <TaskDetail />, visible: false },
  { name: 'Skills管理', path: '/skills', element: <SkillsManager />, visible: true },
  { name: '报告模板', path: '/report-templates', element: <ReportTemplatesPage />, visible: false },
  { name: '漏洞管理', path: '/vulnerabilities', element: <VulnerabilityManagement />, visible: true },
  ...(enableCheckmarxScan
    ? [{ name: 'Checkmarx扫描', path: '/checkmarx-scan', element: <CheckmarxScan />, visible: true }]
    : []),
  { name: '系统设置', path: '/admin', element: <AdminDashboard />, visible: true },
  { name: '账号管理', path: '/account', element: <Account />, visible: false },
];

export default routes;
