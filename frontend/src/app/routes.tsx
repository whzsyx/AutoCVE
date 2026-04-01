import type { ReactNode } from 'react';

import Account from '@/pages/Account';
import AdminDashboard from '@/pages/AdminDashboard';
import AgentAudit from '@/pages/AgentAudit';
import AuditRules from '@/pages/AuditRules';
import AuditTasks from '@/pages/AuditTasks';
import Dashboard from '@/pages/Dashboard';
import FlowDebugger from '@/pages/FlowDebugger';
import InstantAnalysis from '@/pages/InstantAnalysis';
import ProjectDetail from '@/pages/ProjectDetail';
import Projects from '@/pages/Projects';
import PromptManager from '@/pages/PromptManager';
import RecycleBin from '@/pages/RecycleBin';
import ReportTemplatesPage from '@/pages/ReportTemplatesPage';
import SkillsManager from '@/pages/SkillsManager';
import TaskDetail from '@/pages/TaskDetail';

export interface RouteConfig {
  name: string;
  path: string;
  element: ReactNode;
  visible?: boolean;
}

const routes: RouteConfig[] = [
  { name: '\u0041\u0067\u0065\u006e\u0074\u5ba1\u8ba1', path: '/', element: <AgentAudit />, visible: true },
  { name: '\u0041\u0067\u0065\u006e\u0074\u5ba1\u8ba1\u8be6\u60c5', path: '/agent-audit/:taskId', element: <AgentAudit />, visible: false },
  { name: '\u4eea\u8868\u76d8', path: '/dashboard', element: <Dashboard />, visible: true },
  { name: '\u9879\u76ee\u7ba1\u7406', path: '/projects', element: <Projects />, visible: true },
  { name: '\u9879\u76ee\u8be6\u60c5', path: '/projects/:id', element: <ProjectDetail />, visible: false },
  { name: '\u5373\u65f6\u5206\u6790', path: '/instant-analysis', element: <InstantAnalysis />, visible: true },
  { name: '\u5ba1\u8ba1\u4efb\u52a1', path: '/audit-tasks', element: <AuditTasks />, visible: true },
  { name: '\u4efb\u52a1\u8be6\u60c5', path: '/tasks/:id', element: <TaskDetail />, visible: false },
  { name: '\u5ba1\u8ba1\u89c4\u5219', path: '/audit-rules', element: <AuditRules />, visible: true },
  { name: '\u63d0\u793a\u8bcd\u7ba1\u7406', path: '/prompts', element: <PromptManager />, visible: true },
  { name: 'Skills\u7ba1\u7406', path: '/skills', element: <SkillsManager />, visible: true },
  { name: '\u62a5\u544a\u6a21\u677f', path: '/report-templates', element: <ReportTemplatesPage />, visible: true },
  { name: '\u6d41\u7a0b\u8c03\u8bd5', path: '/flow-debugger', element: <FlowDebugger />, visible: true },
  { name: '\u6a21\u578b\u7ba1\u7406', path: '/admin', element: <AdminDashboard />, visible: true },
  { name: '\u56de\u6536\u7ad9', path: '/recycle-bin', element: <RecycleBin />, visible: true },
  { name: '\u8d26\u53f7\u7ba1\u7406', path: '/account', element: <Account />, visible: false },
];

export default routes;
