import { memo } from "react";
import { AlertTriangle, CheckCircle2, FileCode, Link2, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { AgentFinding, AgentTask } from "@/shared/api/agentTasks";

type FinalReportPanelProps = {
  task: AgentTask | null;
  findings: AgentFinding[];
};

const statusRank: Record<string, number> = {
  confirmed: 0,
  candidate: 1,
  false_positive: 2,
};

const severityRank: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

function normalizeStatus(finding: AgentFinding): string {
  return finding.report_status || finding.verdict || (finding.is_verified ? "confirmed" : "candidate");
}

function statusBadgeClass(status: string): string {
  if (status === "confirmed") {
    return "bg-emerald-500/15 text-emerald-700 border border-emerald-500/30";
  }
  if (status === "false_positive") {
    return "bg-slate-500/15 text-slate-700 border border-slate-500/30";
  }
  return "bg-amber-500/15 text-amber-700 border border-amber-500/30";
}

export const FinalReportPanel = memo(function FinalReportPanel({ task, findings }: FinalReportPanelProps) {
  if (!task || !findings?.length) {
    return null;
  }

  const orderedFindings = [...findings].sort((a, b) => {
    const aStatus = statusRank[normalizeStatus(a)] ?? 9;
    const bStatus = statusRank[normalizeStatus(b)] ?? 9;
    if (aStatus !== bStatus) return aStatus - bStatus;
    const aSeverity = severityRank[a.severity || "info"] ?? 9;
    const bSeverity = severityRank[b.severity || "info"] ?? 9;
    return aSeverity - bSeverity;
  });

  return (
    <section className="mt-3 rounded-[22px] border border-border/70 bg-white/72 shadow-[0_24px_60px_rgba(125,104,75,0.12)] backdrop-blur-xl overflow-hidden">
      <div className="border-b border-border/70 px-6 py-4 bg-card/80">
        <div className="flex items-center gap-3">
          <div className="rounded-xl border border-primary/30 bg-primary/10 p-2">
            <ShieldAlert className="h-5 w-5 text-primary" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-foreground">最终漏洞报告</h2>
            <p className="text-sm text-muted-foreground">
              汇总 Triage 与 Finding 的统一漏洞结论。当前可直接用于复核高价值候选漏洞与已确认漏洞。
            </p>
          </div>
        </div>
      </div>

      <div className="space-y-4 p-5">
        {orderedFindings.map((finding) => {
          const reportStatus = normalizeStatus(finding);
          return (
            <article key={finding.id} className="rounded-2xl border border-border/60 bg-card/80 p-4 shadow-sm">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-base font-semibold text-foreground">{finding.title}</h3>
                    <Badge className={statusBadgeClass(reportStatus)}>{reportStatus}</Badge>
                    <Badge variant="outline" className="font-mono uppercase">{finding.severity}</Badge>
                    <Badge variant="outline" className="font-mono">{finding.vulnerability_type}</Badge>
                  </div>
                  {(finding.file_path || finding.line_start) && (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground break-all">
                      <FileCode className="h-4 w-4" />
                      <span>
                        {finding.file_path}
                        {finding.line_start ? `:${finding.line_start}` : ""}
                        {finding.line_end && finding.line_end !== finding.line_start ? `-${finding.line_end}` : ""}
                      </span>
                    </div>
                  )}
                </div>

                <div className="text-right text-xs text-muted-foreground">
                  <div>Confidence: {(finding.ai_confidence ?? finding.confidence ?? 0).toFixed?.(2) ?? finding.confidence}</div>
                  {finding.origin && <div>Origin: {finding.origin}</div>}
                </div>
              </div>

              {finding.description && (
                <p className="mt-3 text-sm leading-6 text-foreground/90">{finding.description}</p>
              )}

              {(finding.source || finding.sink) && (
                <div className="mt-3 grid gap-2 md:grid-cols-2">
                  {finding.source && (
                    <div className="rounded-xl border border-border/50 bg-muted/35 p-3 text-sm">
                      <div className="font-medium text-foreground">Source</div>
                      <div className="mt-1 break-all text-muted-foreground">{finding.source}</div>
                    </div>
                  )}
                  {finding.sink && (
                    <div className="rounded-xl border border-border/50 bg-muted/35 p-3 text-sm">
                      <div className="font-medium text-foreground">Sink</div>
                      <div className="mt-1 break-all text-muted-foreground">{finding.sink}</div>
                    </div>
                  )}
                </div>
              )}

              {finding.impact && (
                <div className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/5 p-3 text-sm">
                  <div className="font-medium text-foreground">漏洞价值 / Impact</div>
                  <div className="mt-1 text-muted-foreground">{finding.impact}</div>
                </div>
              )}

              {finding.cve_justification && (
                <div className="mt-3 rounded-xl border border-primary/20 bg-primary/5 p-3 text-sm">
                  <div className="font-medium text-foreground">CVE / 赏金价值说明</div>
                  <div className="mt-1 text-muted-foreground">{finding.cve_justification}</div>
                </div>
              )}

              {finding.exploit_chain && finding.exploit_chain.length > 0 && (
                <div className="mt-3 rounded-xl border border-border/50 bg-muted/30 p-3">
                  <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                    <Link2 className="h-4 w-4" />
                    利用链 / Exploit Chain
                  </div>
                  <div className="mt-2 space-y-2">
                    {finding.exploit_chain.map((step, index) => (
                      <div key={`${finding.id}-chain-${index}`} className="rounded-lg border border-border/40 bg-white/60 p-2.5 text-sm">
                        <div className="font-medium text-foreground">Step {step.step ?? index + 1}</div>
                        {step.location && <div className="mt-1 break-all text-muted-foreground">{step.location}</div>}
                        {step.description && <div className="mt-1 text-muted-foreground">{step.description}</div>}
                        {step.data_state && <div className="mt-1 text-muted-foreground">Data: {step.data_state}</div>}
                        {step.bypass_reason && <div className="mt-1 text-muted-foreground">Bypass: {step.bypass_reason}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {finding.poc && (
                <div className="mt-3 rounded-xl border border-rose-500/20 bg-rose-500/5 p-3">
                  <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                    <AlertTriangle className="h-4 w-4 text-rose-500" />
                    PoC / 利用方式
                  </div>
                  {finding.poc.description && <div className="mt-2 text-sm text-muted-foreground">{finding.poc.description}</div>}
                  {finding.poc.payload && (
                    <pre className="mt-2 overflow-x-auto rounded-lg bg-black/85 p-3 text-xs text-green-300">{finding.poc.payload}</pre>
                  )}
                  {finding.poc.steps && finding.poc.steps.length > 0 && (
                    <div className="mt-2 space-y-2">
                      {finding.poc.steps.map((step, index) => (
                        <div key={`${finding.id}-poc-${index}`} className="rounded-lg border border-border/40 bg-white/60 p-2.5 text-sm">
                          <div className="font-medium text-foreground">Step {step.step ?? index + 1}</div>
                          {step.action && <div className="mt-1 text-muted-foreground">Action: {step.action}</div>}
                          {step.request && <div className="mt-1 break-all text-muted-foreground">Request: {step.request}</div>}
                          {step.expected_response && <div className="mt-1 text-muted-foreground">Expected: {step.expected_response}</div>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {(finding.verification_notes || finding.evidence_gaps?.length) && (
                <div className="mt-3 grid gap-3 md:grid-cols-2">
                  {finding.verification_notes && (
                    <div className="rounded-xl border border-border/50 bg-muted/30 p-3 text-sm">
                      <div className="flex items-center gap-2 font-medium text-foreground">
                        <CheckCircle2 className="h-4 w-4" />
                        Verification Notes
                      </div>
                      <div className="mt-1 text-muted-foreground">{finding.verification_notes}</div>
                    </div>
                  )}
                  {finding.evidence_gaps && finding.evidence_gaps.length > 0 && (
                    <div className="rounded-xl border border-border/50 bg-muted/30 p-3 text-sm">
                      <div className="font-medium text-foreground">Evidence Gaps</div>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {finding.evidence_gaps.map((gap) => (
                          <Badge key={`${finding.id}-${gap}`} variant="outline" className="font-mono text-[11px]">
                            {gap}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
});

export default FinalReportPanel;
