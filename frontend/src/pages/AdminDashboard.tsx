import { Database, GitBranchPlus, SlidersHorizontal } from 'lucide-react';
import { DatabaseManager } from '@/components/database/DatabaseManager';
import { SystemConfig } from '@/components/system/SystemConfig';
import { WorkflowManager } from '@/components/system/WorkflowManager';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

export default function AdminDashboard() {
  return (
    <div className="gradient-bg min-h-screen p-6">
      <div className="mx-auto max-w-7xl space-y-6">
        <section className="rounded-[30px] border border-[rgba(213,192,158,.78)] bg-[linear-gradient(135deg,rgba(255,255,255,.98),rgba(250,245,236,.94))] p-8 shadow-[0_24px_60px_rgba(123,95,61,.12)]">
          <div className="max-w-3xl space-y-3">
            <div className="inline-flex items-center gap-2 rounded-full border border-[rgba(198,167,122,.45)] bg-white/80 px-4 py-1 text-xs uppercase tracking-[0.22em] text-[#8c6540]">
              <SlidersHorizontal className="h-3.5 w-3.5" />
              Model Control Center
            </div>
            <h1 className="text-4xl font-black tracking-tight text-[#2d241a]">模型管理</h1>
            <p className="text-sm leading-7 text-[#705d4b]">
              在这里统一管理默认模型、每个 Agent 的独立模型覆盖，以及数据库与工作流等平台级运维配置。
            </p>
          </div>
        </section>

        <Tabs defaultValue="models" className="space-y-5">
          <TabsList className="grid h-auto w-full grid-cols-3 gap-2 rounded-[20px] border border-[rgba(220,206,182,.86)] bg-white/88 p-2 shadow-[0_12px_28px_rgba(92,76,49,.05)]">
            <TabsTrigger value="models" className="rounded-[14px] py-3 text-sm font-semibold data-[state=active]:bg-[#d97745] data-[state=active]:text-white">
              <SlidersHorizontal className="mr-2 h-4 w-4" /> 模型配置
            </TabsTrigger>
            <TabsTrigger value="workflow" className="rounded-[14px] py-3 text-sm font-semibold data-[state=active]:bg-[#d97745] data-[state=active]:text-white">
              <GitBranchPlus className="mr-2 h-4 w-4" /> 工作流管理
            </TabsTrigger>
            <TabsTrigger value="database" className="rounded-[14px] py-3 text-sm font-semibold data-[state=active]:bg-[#d97745] data-[state=active]:text-white">
              <Database className="mr-2 h-4 w-4" /> 数据库管理
            </TabsTrigger>
          </TabsList>
          <TabsContent value="models"><SystemConfig /></TabsContent>
          <TabsContent value="workflow"><WorkflowManager /></TabsContent>
          <TabsContent value="database"><DatabaseManager /></TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
