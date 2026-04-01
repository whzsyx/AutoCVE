import { useState, FormEvent, useEffect } from "react";
import { useNavigate, useLocation, Link } from "react-router-dom";
import { useAuth } from "@/shared/context/AuthContext";
import { apiClient } from "@/shared/api/serverClient";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { ArrowRight, CheckCircle2, Lock, Mail, ShieldCheck, Sparkles } from "lucide-react";
import { version } from "../../package.json";

const highlights = [
  {
    title: "\u7edf\u4e00\u5ba1\u8ba1\u5165\u53e3",
    description: "\u9879\u76ee\u3001Agent \u5ba1\u8ba1\u3001\u62a5\u544a\u5bfc\u51fa\u4e0e\u89c4\u5219\u7ba1\u7406\u4fdd\u6301\u5728\u540c\u4e00\u5de5\u4f5c\u533a\u91cc\u5b8c\u6210\u3002",
    icon: ShieldCheck,
  },
  {
    title: "\u8f7b\u91cf\u5de5\u4f5c\u6d41",
    description: "\u66f4\u5e72\u51c0\u7684\u754c\u9762\u5c42\u7ea7\u548c\u66f4\u7a33\u5b9a\u7684\u64cd\u4f5c\u8def\u5f84\uff0c\u9002\u5408\u65e5\u5e38\u9ad8\u9891\u4f7f\u7528\u3002",
    icon: Sparkles,
  },
];

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(false);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { login, isAuthenticated } = useAuth();

  const from = location.state?.from?.pathname || "/";

  useEffect(() => {
    const savedEmail = localStorage.getItem("remembered_email");
    if (savedEmail) {
      setEmail(savedEmail);
      setRememberMe(true);
    }
  }, []);

  useEffect(() => {
    if (isAuthenticated && !loading) {
      navigate(from, { replace: true });
    }
  }, [isAuthenticated, navigate, from, loading]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const formData = new URLSearchParams();
      formData.append("username", email);
      formData.append("password", password);

      const response = await apiClient.post("/auth/login", formData, {
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
        },
      });

      if (rememberMe) {
        localStorage.setItem("remembered_email", email);
      } else {
        localStorage.removeItem("remembered_email");
      }

      await login(response.data.access_token, rememberMe);
      toast.success("\u767b\u5f55\u6210\u529f");
    } catch (error: any) {
      const detail = error.response?.data?.detail;
      if (Array.isArray(detail)) {
        const messages = detail.map((err: any) => err.msg || err.message || JSON.stringify(err)).join("; ");
        toast.error(messages || "\u767b\u5f55\u5931\u8d25");
      } else if (typeof detail === "object") {
        toast.error(detail.msg || detail.message || JSON.stringify(detail));
      } else {
        toast.error(detail || "\u767b\u5f55\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u90ae\u7bb1\u548c\u5bc6\u7801");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen gradient-bg p-4 md:p-6">
      <div className="mx-auto grid min-h-[calc(100vh-2rem)] max-w-6xl overflow-hidden rounded-[40px] border border-white/70 bg-white/55 lg:grid-cols-[1.08fr_0.92fr] shadow-[0_28px_80px_rgba(88,97,110,0.12)] backdrop-blur-xl">
        <section className="relative hidden border-r border-slate-200/70 bg-[linear-gradient(180deg,rgba(249,250,249,0.92),rgba(242,245,244,0.92))] p-10 lg:flex lg:flex-col xl:p-12">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,0.68),transparent_32%),radial-gradient(circle_at_bottom_right,rgba(200,216,208,0.24),transparent_28%)]" />
          <div className="relative flex h-full flex-col justify-between">
            <div className="space-y-10">
              <div className="flex items-center gap-4">
                <div className="flex h-16 w-16 items-center justify-center rounded-[22px] border border-slate-200/80 bg-white/85 shadow-[0_14px_36px_rgba(92,104,120,0.08)]">
                  <img src="/auditai_icon.svg" alt="AuditAI" className="h-11 w-11 object-contain" />
                </div>
                <div>
                  <div className="text-3xl font-semibold tracking-[-0.05em] text-slate-900">AuditAI</div>
                  <p className="mt-1 text-sm text-slate-500">Security Review Workspace</p>
                </div>
              </div>

              <div className="max-w-xl space-y-5">
                <p className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">Sign In</p>
                <h1 className="text-5xl leading-[1.06] tracking-[-0.06em] text-slate-900">
                  {"\u91cd\u65b0\u8fdb\u5165\u4f60\u7684"}
                  <br />
                  {"\u5ba1\u8ba1\u5de5\u4f5c\u53f0"}
                </h1>
                <p className="max-w-lg text-base text-slate-500">
                  {"\u4fdd\u6301\u9879\u76ee\u3001\u5ba1\u8ba1\u4efb\u52a1\u4e0e\u62a5\u544a\u6d41\u8f6c\u5728\u540c\u4e00\u5957\u5de5\u4f5c\u6d41\u4e2d\uff0c\u51cf\u5c11\u6765\u56de\u8df3\u8f6c\u548c\u4fe1\u606f\u65ad\u5c42\u3002"}
                </p>
              </div>

              <div className="grid gap-4 xl:grid-cols-2">
                {highlights.map(({ title, description, icon: Icon }) => (
                  <div key={title} className="auth-showcase-card p-6">
                    <div className="relative flex items-start gap-4">
                      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/90 text-[hsl(var(--primary))] shadow-[0_10px_22px_rgba(111,167,132,0.12)]">
                        <Icon className="h-5 w-5" />
                      </div>
                      <div>
                        <div className="text-base font-semibold text-slate-900">{title}</div>
                        <p className="mt-2 text-sm leading-7 text-slate-500">{description}</p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="relative flex items-center justify-between text-sm text-slate-400">
              <span>v{version}</span>
              <span>{new Date().toLocaleDateString("zh-CN")}</span>
            </div>
          </div>
        </section>

        <section className="flex items-center justify-center p-6 sm:p-10 xl:p-12">
          <div className="w-full max-w-[520px]">
            <div className="mb-8 text-center lg:hidden">
              <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-[24px] border border-slate-200/80 bg-white/90 shadow-[0_14px_36px_rgba(92,104,120,0.08)]">
                <img src="/auditai_icon.svg" alt="AuditAI" className="h-11 w-11 object-contain" />
              </div>
              <h1 className="text-3xl font-semibold tracking-[-0.05em] text-slate-900">AuditAI</h1>
              <p className="mt-2 text-sm text-slate-500">{"\u767b\u5f55\u5e76\u7ee7\u7eed\u4f60\u7684\u5ba1\u8ba1\u6d41\u7a0b"}</p>
            </div>

            <div className="cyber-card p-7 sm:p-8">
              <div className="mb-8 space-y-3">
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">Welcome Back</p>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-[2rem] font-semibold tracking-[-0.05em] text-slate-900">{"\u767b\u5f55 AuditAI"}</h2>
                    <p className="mt-2 text-sm text-slate-500">{"\u8f93\u5165\u8d26\u53f7\u4fe1\u606f\uff0c\u7ee7\u7eed\u5904\u7406\u5ba1\u8ba1\u4efb\u52a1\u548c\u9879\u76ee\u534f\u4f5c\u3002"}</p>
                  </div>
                  <div className="hidden h-12 w-12 items-center justify-center rounded-2xl bg-[rgba(223,235,225,0.8)] text-[hsl(var(--primary))] sm:flex">
                    <CheckCircle2 className="h-5 w-5" />
                  </div>
                </div>
              </div>

              <form onSubmit={handleSubmit} className="space-y-5">
                <div className="space-y-2.5">
                  <Label htmlFor="email" className="cyber-label">{"\u90ae\u7bb1\u5730\u5740"}</Label>
                  <div className="auth-input-shell">
                    <span className="auth-input-icon">
                      <Mail className="h-4 w-4" />
                    </span>
                    <Input
                      id="email"
                      type="email"
                      placeholder="your@email.com"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      required
                      className="auth-input-field"
                    />
                  </div>
                </div>

                <div className="space-y-2.5">
                  <Label htmlFor="password" className="cyber-label">{"\u5bc6\u7801"}</Label>
                  <div className="auth-input-shell">
                    <span className="auth-input-icon">
                      <Lock className="h-4 w-4" />
                    </span>
                    <Input
                      id="password"
                      type="password"
                      placeholder={"\u8f93\u5165\u5bc6\u7801"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required
                      className="auth-input-field"
                    />
                  </div>
                </div>

                <div className="flex items-center justify-between gap-4 pt-1 text-sm">
                  <label className="flex items-center gap-3 text-slate-500">
                    <Checkbox
                      checked={rememberMe}
                      onCheckedChange={(checked) => setRememberMe(Boolean(checked))}
                    />
                    {"\u8bb0\u4f4f\u90ae\u7bb1"}
                  </label>
                  <span className="text-slate-400">{"\u5b89\u5168\u767b\u5f55"}</span>
                </div>

                <Button type="submit" disabled={loading} className="cyber-btn-primary h-14 w-full rounded-[20px] text-base">
                  {loading ? "\u767b\u5f55\u4e2d..." : "\u767b\u5f55\u5e76\u8fdb\u5165\u5de5\u4f5c\u53f0"}
                </Button>
              </form>

              <div className="mt-7 flex items-center justify-between rounded-[22px] bg-slate-100/80 px-5 py-4 text-sm">
                <span className="text-slate-500">{"\u8fd8\u6ca1\u6709\u8d26\u53f7\uff1f"}</span>
                <Link to="/register" className="inline-flex items-center gap-1.5 font-semibold text-[hsl(var(--primary))]">
                  {"\u521b\u5efa\u65b0\u8d26\u53f7"}
                  <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
