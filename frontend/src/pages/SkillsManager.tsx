import { useEffect, useMemo, useState } from 'react';
import {
  ArrowUpRight,
  BookOpen,
  FolderOpen,
  Github,
  Link2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Wand2,
} from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import {
  createSkill,
  createSkillBinding,
  deleteSkill,
  deleteSkillBinding,
  getSkill,
  getSkills,
  importGithubSkill,
  resyncSkills,
  updateSkill,
  updateSkillBinding,
  type AgentSkillBinding,
  type Skill,
  type SkillMetadata,
  type SkillPayload,
} from '@/shared/api/skills';
import { syncLocalLibraries } from '@/shared/api/modelConfig';

const AGENT_OPTIONS = [
  { value: 'orchestrator', label: 'orchestrator Agent' },
  { value: 'recon', label: 'recon Agent' },
  { value: 'scan', label: 'scan Agent' },
  { value: 'triage', label: 'triage Agent' },
  { value: 'finding', label: 'finding Agent' },
  { value: 'verification', label: 'verification Agent' },
] as const;

const HOST_PROJECT_ROOT = (import.meta.env.VITE_HOST_PROJECT_ROOT as string | undefined) || '';
const DEFAULT_IMPORT_URL = 'https://github.com/EastSword/dfyx_skills_lab/tree/main/skill-dfyx_code_security_review';
const EMPTY_FORM: SkillPayload = {
  name: '',
  slug: '',
  description: '',
  source_type: 'manual',
  source_url: '',
  content: '',
  tags: [],
  is_active: true,
  bindings: [],
};

function errorMessage(error: unknown, fallback: string) {
  if (typeof error === 'object' && error && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: string } } }).response?.data?.detail;
    if (detail) return detail;
  }
  return error instanceof Error ? error.message : fallback;
}

function buildAbsolutePath(relativePath: string) {
  if (!relativePath) return '';
  return HOST_PROJECT_ROOT
    ? `${HOST_PROJECT_ROOT.replace(/[\\/]+$/, '')}/${relativePath}`.replace(/\//g, '\\')
    : relativePath;
}

function toFileHref(path: string) {
  return path ? `file:///${path.replace(/\\/g, '/')}` : '#';
}

function copyText(value: string, message: string) {
  if (!value) return;
  navigator.clipboard.writeText(value).then(() => toast.success(message)).catch(() => toast.error('复制失败'));
}

function metadataString(skill: SkillMetadata | Skill, key: string) {
  const value = skill.metadata_json?.[key];
  return typeof value === 'string' ? value : '';
}

function bindingFor(skill: SkillMetadata, agent: string): AgentSkillBinding | undefined {
  return skill.bindings.find((binding) => binding.agent_type === agent);
}

function skillFolderPath(skill: SkillMetadata | Skill) {
  return buildAbsolutePath(metadataString(skill, 'workspace_relative_path'));
}

function skillFilePath(skill: SkillMetadata | Skill) {
  return buildAbsolutePath(metadataString(skill, 'workspace_skill_file') || metadataString(skill, 'workspace_file_path') || `${metadataString(skill, 'workspace_relative_path')}/SKILL.md`);
}

function agentBindingCount(skills: SkillMetadata[], agent: string) {
  return skills.filter((skill) => bindingFor(skill, agent)?.enabled).length;
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-[rgba(228,214,192,.92)] bg-white/90 px-4 py-3">
      <div className="text-[11px] uppercase tracking-[0.18em] text-[#9a7e60]">{label}</div>
      <div className="mt-2 break-all text-sm leading-6 text-[#554434]">{value || '暂无'}</div>
    </div>
  );
}

export default function SkillsManager() {
  const [skills, setSkills] = useState<SkillMetadata[]>([]);
  const [search, setSearch] = useState('');
  const [selectedAgent, setSelectedAgent] = useState('finding');
  const [loading, setLoading] = useState(true);
  const [detailSkill, setDetailSkill] = useState<Skill | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<SkillMetadata | null>(null);
  const [skillForm, setSkillForm] = useState<SkillPayload>(EMPTY_FORM);
  const [importUrl, setImportUrl] = useState(DEFAULT_IMPORT_URL);
  const [importAgent, setImportAgent] = useState('finding');
  const [importKeywords, setImportKeywords] = useState('auth,idor,access-control,ssrf,business-logic');
  const [syncing, setSyncing] = useState(false);

  const rootPath = useMemo(() => buildAbsolutePath('skill_library'), []);
  const filteredSkills = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return skills;
    return skills.filter((skill) => [skill.name, skill.slug, skill.description, (skill.tags || []).join(' ')].join(' ').toLowerCase().includes(keyword));
  }, [search, skills]);

  const loadSkillsPage = async (autoSyncIfEmpty = false) => {
    try {
      setLoading(true);
      const response = await getSkills();
      setSkills(response.items);
      if (autoSyncIfEmpty && response.items.length === 0) {
        await syncLocalLibraries();
        await resyncSkills();
        const refreshed = await getSkills();
        setSkills(refreshed.items);
      }
    } catch (error) {
      toast.error(errorMessage(error, '加载 Skills 失败'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSkillsPage(true);
  }, []);

  const openCreate = () => {
    setEditingSkill(null);
    setSkillForm(EMPTY_FORM);
    setEditorOpen(true);
  };

  const openEdit = (skill: SkillMetadata) => {
    setEditingSkill(skill);
    setSkillForm({
      ...EMPTY_FORM,
      name: skill.name,
      slug: skill.slug,
      description: skill.description,
      source_type: skill.source_type,
      source_url: skill.source_url,
      tags: skill.tags || [],
      is_active: skill.is_active,
      metadata_json: skill.metadata_json || {},
      content: '',
    });
    setEditorOpen(true);
  };

  const openDetail = async (skill: SkillMetadata) => {
    try {
      const full = await getSkill(skill.id);
      setDetailSkill(full);
      setDetailOpen(true);
    } catch (error) {
      toast.error(errorMessage(error, '加载 Skill 详情失败'));
    }
  };

  const saveSkill = async () => {
    const payload: SkillPayload = {
      ...skillForm,
      slug: (skillForm.slug || skillForm.name)
        .trim()
        .toLowerCase()
        .replace(/\s+/g, '-')
        .replace(/[^a-z0-9._-]+/g, '-'),
      tags: Array.isArray(skillForm.tags)
        ? skillForm.tags
        : String(skillForm.tags || '')
            .split(',')
            .map((item) => item.trim())
            .filter(Boolean),
    };

    try {
      if (editingSkill) {
        await updateSkill(editingSkill.id, payload);
        toast.success('Skill 已更新');
      } else {
        await createSkill(payload);
        toast.success('Skill 已创建');
      }
      setEditorOpen(false);
      await loadSkillsPage();
    } catch (error) {
      toast.error(errorMessage(error, '保存 Skill 失败'));
    }
  };

  const removeSkill = async (skill: SkillMetadata) => {
    try {
      await deleteSkill(skill.id);
      toast.success('Skill 已删除');
      await loadSkillsPage();
    } catch (error) {
      toast.error(errorMessage(error, '删除 Skill 失败'));
    }
  };

  const importSkill = async () => {
    try {
      await importGithubSkill({
        repo_url: importUrl,
        agent_type: importAgent,
        bind_to_agent: true,
        enabled: true,
        always_include: importAgent === 'finding',
        match_keywords: importKeywords.split(',').map((item) => item.trim()).filter(Boolean),
      });
      await syncLocalLibraries();
      await resyncSkills();
      toast.success('GitHub Skill 导入成功');
      setImportOpen(false);
      await loadSkillsPage();
    } catch (error) {
      toast.error(errorMessage(error, 'GitHub Skill 导入失败'));
    }
  };

  const toggleBinding = async (skill: SkillMetadata, enabled: boolean) => {
    const binding = bindingFor(skill, selectedAgent);
    try {
      if (!binding && enabled) {
        await createSkillBinding(skill.id, {
          agent_type: selectedAgent,
          enabled: true,
          always_include: selectedAgent === 'finding',
          sort_order: 0,
          match_keywords: [],
          match_config: {},
        });
      } else if (binding) {
        await updateSkillBinding(skill.id, binding.id, { enabled });
      }
      await syncLocalLibraries();
      await resyncSkills();
      await loadSkillsPage();
    } catch (error) {
      toast.error(errorMessage(error, '更新 Agent 绑定失败'));
    }
  };

  const removeBinding = async (skill: SkillMetadata) => {
    const binding = bindingFor(skill, selectedAgent);
    if (!binding) return;
    try {
      await deleteSkillBinding(skill.id, binding.id);
      await syncLocalLibraries();
      await resyncSkills();
      toast.success('已移除当前 Agent 绑定');
      await loadSkillsPage();
    } catch (error) {
      toast.error(errorMessage(error, '移除 Agent 绑定失败'));
    }
  };

  const syncSkillDirectory = async () => {
    try {
      setSyncing(true);
      await syncLocalLibraries();
      await resyncSkills();
      await loadSkillsPage();
      toast.success('Skill 目录已同步到本地文件夹');
    } catch (error) {
      toast.error(errorMessage(error, '同步 Skill 目录失败'));
    } finally {
      setSyncing(false);
    }
  };

  if (loading) {
    return <div className="gradient-bg min-h-screen flex items-center justify-center text-muted-foreground">正在加载技能库...</div>;
  }

  return (
    <div className="gradient-bg min-h-screen px-6 py-8">
      <div className="mx-auto max-w-7xl space-y-6">
        <section className="rounded-[34px] border border-[rgba(214,193,160,.78)] bg-[linear-gradient(135deg,rgba(255,255,255,.98),rgba(249,243,233,.95))] p-8 shadow-[0_28px_70px_rgba(120,96,57,.11)]">
          <div className="flex flex-col gap-6 xl:flex-row xl:items-start xl:justify-between">
            <div className="max-w-3xl space-y-3">
              <div className="inline-flex items-center gap-2 rounded-full border border-[rgba(198,167,122,.45)] bg-white/80 px-4 py-1 text-xs uppercase tracking-[0.22em] text-[#8c6540]">
                <BookOpen className="h-3.5 w-3.5" /> Skills Catalog
              </div>
              <h1 className="text-4xl font-black tracking-tight text-[#2d241a]">技能管理</h1>
              <p className="text-sm leading-7 text-[#705d4b]">
                `skill_library` 下每个 Skill 都是一个独立文件夹。你可以像查看本地插件一样查看详情、复制路径、打开目录，并为不同 Agent 单独启用不同的 Skills。
              </p>
            </div>
            <div className="flex flex-wrap gap-3">
              <Button variant="outline" className="h-11 rounded-full" onClick={syncSkillDirectory} disabled={syncing}>
                {syncing ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}同步本地目录
              </Button>
              <Button variant="outline" className="h-11 rounded-full" onClick={() => setImportOpen(true)}>
                <Github className="mr-2 h-4 w-4" /> 导入 GitHub Skill
              </Button>
              <Button className="h-11 rounded-full bg-[#d97745] text-white hover:bg-[#c96532]" onClick={openCreate}>
                <Plus className="mr-2 h-4 w-4" /> 新建 Skill
              </Button>
            </div>
          </div>
        </section>

        <section className="grid gap-4 xl:grid-cols-[1.05fr_.95fr]">
          <div className="rounded-[26px] border border-[rgba(222,208,184,.9)] bg-white/90 p-5 shadow-[0_18px_36px_rgba(95,76,48,.08)]">
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-2">
                <div className="text-xs uppercase tracking-[0.22em] text-[#9a7e60]">Skills 根目录</div>
                <div className="break-all text-sm leading-7 text-[#5b4838]">{rootPath || '未检测到根目录'}</div>
              </div>
              <FolderOpen className="h-6 w-6 text-[#d97745]" />
            </div>
            <div className="mt-4 flex flex-wrap gap-3">
              <Button variant="outline" size="sm" onClick={() => copyText(rootPath, '已复制 Skills 根目录')}>复制路径</Button>
              <Button asChild variant="outline" size="sm">
                <a href={toFileHref(rootPath)} target="_blank" rel="noreferrer">打开文件夹<ArrowUpRight className="ml-2 h-4 w-4" /></a>
              </Button>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="rounded-[26px] border border-[rgba(222,208,184,.9)] bg-white/90 p-5 shadow-[0_18px_36px_rgba(95,76,48,.08)]">
              <div className="text-xs uppercase tracking-[0.22em] text-[#9a7e60]">技能总数</div>
              <div className="mt-3 text-3xl font-black text-[#d97745]">{skills.length}</div>
            </div>
            <div className="rounded-[26px] border border-[rgba(222,208,184,.9)] bg-white/90 p-5 shadow-[0_18px_36px_rgba(95,76,48,.08)]">
              <div className="text-xs uppercase tracking-[0.22em] text-[#9a7e60]">当前 Agent 已启用</div>
              <div className="mt-3 text-3xl font-black text-[#d97745]">{agentBindingCount(skills, selectedAgent)}</div>
            </div>
            <div className="rounded-[26px] border border-[rgba(222,208,184,.9)] bg-white/90 p-5 shadow-[0_18px_36px_rgba(95,76,48,.08)]">
              <div className="text-xs uppercase tracking-[0.22em] text-[#9a7e60]">GitHub 来源</div>
              <div className="mt-3 text-3xl font-black text-[#d97745]">{skills.filter((item) => item.source_type === 'github').length}</div>
            </div>
          </div>
        </section>

        <section className="rounded-[28px] border border-[rgba(222,208,184,.9)] bg-white/88 p-5 shadow-[0_18px_36px_rgba(95,76,48,.08)]">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="text-xs uppercase tracking-[0.22em] text-[#9a7e60]">Agent 绑定视图</div>
              <div className="mt-2 text-sm leading-7 text-[#6f5b49]">先切换 Agent，再在下方卡片中启用或停用对应 Skill。</div>
            </div>
            <div className="flex flex-col gap-3 md:flex-row">
              <Select value={selectedAgent} onValueChange={setSelectedAgent}>
                <SelectTrigger className="min-w-[220px]"><SelectValue /></SelectTrigger>
                <SelectContent>{AGENT_OPTIONS.map((agent) => <SelectItem key={agent.value} value={agent.value}>{agent.label}</SelectItem>)}</SelectContent>
              </Select>
              <div className="relative min-w-[300px]">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#9c7f63]" />
                <Input className="pl-9" placeholder="搜索 Skill 名称、slug、标签" value={search} onChange={(event) => setSearch(event.target.value)} />
              </div>
            </div>
          </div>
        </section>

        <section className="grid gap-4 md:grid-cols-2">
          {filteredSkills.map((skill) => {
            const binding = bindingFor(skill, selectedAgent);
            const folderPath = skillFolderPath(skill);
            const filePath = skillFilePath(skill);
            return (
              <article key={skill.id} className="rounded-[28px] border border-[rgba(223,210,188,.92)] bg-[#fffdf9] p-5 shadow-[0_16px_30px_rgba(94,76,52,.06)] transition hover:-translate-y-1 hover:shadow-[0_20px_40px_rgba(94,76,52,.12)]">
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-3">
                    <div className="flex items-center gap-3">
                      <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[#f6efe6] text-[#d97745]"><BookOpen className="h-5 w-5" /></div>
                      <div>
                        <h3 className="text-xl font-bold text-[#2f2318]">{skill.name}</h3>
                        <p className="text-sm text-[#7f6956]">{skill.slug}</p>
                      </div>
                    </div>
                    <p className="line-clamp-3 text-sm leading-7 text-[#6b5846]">{skill.description || '暂无描述'}</p>
                    <div className="flex flex-wrap gap-2">
                      <Badge className="bg-[#f6efe6] text-[#8c6540]">{skill.source_type}</Badge>
                      {binding?.enabled && <Badge className="bg-[#e7f6ed] text-[#2c8c59]">{selectedAgent} 已启用</Badge>}
                      {(skill.tags || []).slice(0, 4).map((tag) => <Badge key={tag} variant="outline">{tag}</Badge>)}
                    </div>
                  </div>
                  <Switch checked={Boolean(binding?.enabled)} onCheckedChange={(checked) => toggleBinding(skill, checked)} />
                </div>

                <div className="mt-4 grid gap-3">
                  <DetailRow label="Skill 目录" value={folderPath} />
                  <DetailRow label="SKILL.md 文件" value={filePath} />
                </div>

                <div className="mt-5 flex flex-wrap gap-3">
                  <Button size="sm" className="bg-[#d97745] text-white hover:bg-[#c96532]" onClick={() => openDetail(skill)}>查看详情</Button>
                  <Button asChild variant="outline" size="sm">
                    <a href={toFileHref(folderPath)} target="_blank" rel="noreferrer">打开文件夹<ArrowUpRight className="ml-2 h-4 w-4" /></a>
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => copyText(folderPath, '已复制 Skill 路径')}>复制路径</Button>
                  <Button variant="outline" size="sm" onClick={() => openEdit(skill)}><Pencil className="mr-2 h-4 w-4" /> 编辑</Button>
                  {binding && <Button variant="outline" size="sm" onClick={() => removeBinding(skill)}>移除当前 Agent 绑定</Button>}
                  {!skill.is_system && <Button variant="outline" size="sm" onClick={() => removeSkill(skill)}><Trash2 className="mr-2 h-4 w-4" /> 删除</Button>}
                </div>
              </article>
            );
          })}
        </section>

        <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
          <DialogContent className="max-w-6xl border-[rgba(215,194,161,.8)] bg-[linear-gradient(180deg,rgba(255,255,255,.99),rgba(249,243,233,.98))] sm:max-h-[88vh] overflow-hidden">
            <DialogHeader>
              <DialogTitle className="text-2xl font-black text-[#2f2418]">Skill 详情</DialogTitle>
            </DialogHeader>
            {detailSkill && (
              <div className="grid gap-5 lg:grid-cols-[0.92fr_1.08fr]">
                <div className="space-y-4 rounded-[24px] border border-[rgba(223,210,188,.92)] bg-[#fffdfa] p-5">
                  <div>
                    <h3 className="text-2xl font-bold text-[#2f2318]">{detailSkill.name}</h3>
                    <p className="mt-2 text-sm leading-7 text-[#6d5847]">{detailSkill.description || '暂无描述'}</p>
                  </div>
                  <DetailRow label="Slug" value={detailSkill.slug} />
                  <DetailRow label="Skill 目录" value={skillFolderPath(detailSkill)} />
                  <DetailRow label="SKILL.md 文件" value={skillFilePath(detailSkill)} />
                  <div className="flex flex-wrap gap-2">
                    {(detailSkill.tags || []).map((tag) => <Badge key={tag} variant="outline">{tag}</Badge>)}
                  </div>
                  <div className="flex flex-wrap gap-3">
                    <Button asChild variant="outline" size="sm">
                      <a href={toFileHref(skillFolderPath(detailSkill))} target="_blank" rel="noreferrer">打开文件夹<ArrowUpRight className="ml-2 h-4 w-4" /></a>
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => copyText(skillFolderPath(detailSkill), '已复制 Skill 文件夹路径')}>复制路径</Button>
                  </div>
                </div>
                <div className="rounded-[24px] border border-[rgba(223,210,188,.92)] bg-[#fffdfa] p-5">
                  <div className="flex items-center justify-between">
                    <h4 className="text-lg font-bold text-[#2f2318]">SKILL.md 内容</h4>
                    <Button variant="outline" size="sm" onClick={() => copyText(detailSkill.content || '', '已复制 SKILL.md 内容')}>复制内容</Button>
                  </div>
                  <ScrollArea className="mt-4 h-[58vh] rounded-2xl border border-[rgba(232,220,201,.9)] bg-white p-4">
                    <pre className="whitespace-pre-wrap text-sm leading-7 text-[#584736]">{detailSkill.content || '暂无内容'}</pre>
                  </ScrollArea>
                </div>
              </div>
            )}
          </DialogContent>
        </Dialog>

        <Dialog open={editorOpen} onOpenChange={setEditorOpen}>
          <DialogContent className="max-w-6xl border-[rgba(215,194,161,.8)] bg-[linear-gradient(180deg,rgba(255,255,255,.99),rgba(249,243,233,.98))] sm:max-h-[90vh] overflow-hidden">
            <DialogHeader>
              <DialogTitle className="text-2xl font-black text-[#2f2418]">{editingSkill ? '编辑 Skill' : '新建 Skill'}</DialogTitle>
            </DialogHeader>
            <div className="grid gap-5 lg:grid-cols-[0.88fr_1.12fr]">
              <div className="space-y-4 rounded-[24px] border border-[rgba(223,210,188,.92)] bg-[#fffdfa] p-5">
                <div className="rounded-[22px] border border-[rgba(228,214,192,.92)] bg-[linear-gradient(180deg,#fffdfa,#fbf3e7)] p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-[#9a7e60]">配置面板</div>
                  <div className="mt-2 text-sm leading-7 text-[#6d5847]">先填写名称、来源和标签，再在右侧编写完整的 SKILL.md 内容。</div>
                </div>
                <div className="space-y-2"><Label>名称</Label><Input value={skillForm.name} onChange={(event) => setSkillForm((prev) => ({ ...prev, name: event.target.value }))} placeholder="例如：代码授权审查" /></div>
                <div className="space-y-2"><Label>Slug</Label><Input value={skillForm.slug || ''} onChange={(event) => setSkillForm((prev) => ({ ...prev, slug: event.target.value }))} placeholder="留空将根据名称自动生成" /></div>
                <div className="space-y-2"><Label>描述</Label><Textarea rows={5} value={skillForm.description} onChange={(event) => setSkillForm((prev) => ({ ...prev, description: event.target.value }))} placeholder="一句话说明这个 Skill 解决什么问题。" /></div>
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2"><Label>来源类型</Label><Select value={skillForm.source_type || 'manual'} onValueChange={(value) => setSkillForm((prev) => ({ ...prev, source_type: value }))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="manual">manual</SelectItem><SelectItem value="github">github</SelectItem><SelectItem value="local">local</SelectItem></SelectContent></Select></div>
                  <div className="space-y-2"><Label>标签</Label><Input value={Array.isArray(skillForm.tags) ? skillForm.tags.join(', ') : ''} onChange={(event) => setSkillForm((prev) => ({ ...prev, tags: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) }))} placeholder="auth, idor, review" /></div>
                </div>
                <div className="space-y-2"><Label>来源 URL</Label><Input value={skillForm.source_url || ''} onChange={(event) => setSkillForm((prev) => ({ ...prev, source_url: event.target.value }))} placeholder="GitHub 或文档链接，可选" /></div>
              </div>
              <div className="rounded-[24px] border border-[rgba(223,210,188,.92)] bg-[#fffdfa] p-5">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h4 className="text-lg font-bold text-[#2f2318]">SKILL.md 内容</h4>
                    <p className="mt-1 text-sm text-[#6d5847]">这里写完整指令、约束、示例和扩展资源说明。</p>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => setSkillForm((prev) => ({
                    ...prev,
                    content: [
                      '# Skill Overview',
                      '',
                      '## Purpose',
                      '- Explain what this skill is for.',
                      '',
                      '## Workflow',
                      '- Step 1',
                      '- Step 2',
                    ].join('\n'),
                  }))}><Wand2 className="mr-2 h-4 w-4" /> 填充示例</Button>
                </div>
                <Textarea className="mt-4 min-h-[56vh]" value={skillForm.content || ''} onChange={(event) => setSkillForm((prev) => ({ ...prev, content: event.target.value }))} placeholder="# Skill Overview\n\n## Purpose\n- ..." />
                <div className="mt-4 flex justify-end gap-3"><Button variant="outline" onClick={() => setEditorOpen(false)}>取消</Button><Button className="bg-[#d97745] text-white hover:bg-[#c96532]" onClick={saveSkill}>保存 Skill</Button></div>
              </div>
            </div>
          </DialogContent>
        </Dialog>

        <Dialog open={importOpen} onOpenChange={setImportOpen}>
          <DialogContent className="max-w-5xl border-[rgba(215,194,161,.8)] bg-[linear-gradient(180deg,rgba(255,255,255,.99),rgba(249,243,233,.98))] sm:max-h-[88vh] overflow-hidden">
            <DialogHeader>
              <DialogTitle className="text-2xl font-black text-[#2f2418]">导入 GitHub Skill</DialogTitle>
            </DialogHeader>
            <div className="grid gap-5 lg:grid-cols-[0.92fr_1.08fr]">
              <div className="space-y-4 rounded-[24px] border border-[rgba(223,210,188,.92)] bg-[#fffdfa] p-5">
                <div className="rounded-[22px] border border-[rgba(228,214,192,.92)] bg-[linear-gradient(180deg,#fffdfa,#fbf3e7)] p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-[#9a7e60]">导入参数</div>
                  <div className="mt-2 text-sm leading-7 text-[#6d5847]">支持直接导入 GitHub 仓库或子目录形式的 Skill，并同步到本地 `skill_library`。</div>
                </div>
                <div className="space-y-2"><Label>GitHub 仓库 URL</Label><Input value={importUrl} onChange={(event) => setImportUrl(event.target.value)} placeholder="https://github.com/.../tree/main/skill-folder" /></div>
                <div className="space-y-2"><Label>默认绑定 Agent</Label><Select value={importAgent} onValueChange={setImportAgent}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{AGENT_OPTIONS.map((agent) => <SelectItem key={agent.value} value={agent.value}>{agent.label}</SelectItem>)}</SelectContent></Select></div>
                <div className="space-y-2"><Label>匹配关键词</Label><Textarea rows={6} value={importKeywords} onChange={(event) => setImportKeywords(event.target.value)} placeholder="auth, idor, access-control" /></div>
              </div>
              <div className="space-y-4 rounded-[24px] border border-[rgba(223,210,188,.92)] bg-[#fffdfa] p-5">
                <div className="flex items-center gap-2 text-lg font-bold text-[#2f2318]"><Link2 className="h-5 w-5 text-[#d97745]" /> 导入效果</div>
                <div className="rounded-2xl border border-[rgba(228,214,192,.92)] bg-white/90 p-4 text-sm leading-7 text-[#6b5846]">
                  <ul className="list-disc space-y-2 pl-5">
                    <li>导入成功后会在 <code>skill_library/&lt;skill-folder&gt;</code> 下生成独立文件夹。</li>
                    <li>如果选择绑定 Agent，会同步创建 <code>skill_library/agents/&lt;agent&gt;/&lt;skill-folder&gt;</code>。</li>
                    <li>导入完成后可以立即在当前页面查看内容、复制路径或打开文件夹。</li>
                  </ul>
                </div>
                <div className="rounded-2xl border border-[rgba(228,214,192,.92)] bg-white/90 p-4 text-sm leading-7 text-[#6b5846]">
                  <div className="font-semibold text-[#564433]">推荐测试地址</div>
                  <div className="mt-2 break-all">{DEFAULT_IMPORT_URL}</div>
                </div>
                <div className="flex justify-end gap-3 pt-2"><Button variant="outline" onClick={() => setImportOpen(false)}>取消</Button><Button className="bg-[#d97745] text-white hover:bg-[#c96532]" onClick={importSkill}>导入 Skill</Button></div>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
