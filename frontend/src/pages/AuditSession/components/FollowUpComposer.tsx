import { FormEvent, KeyboardEvent, useCallback, useState } from "react";
import { Loader2, SendHorizonal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function FollowUpComposer({
  disabled,
  onSubmit,
}: {
  disabled?: boolean;
  onSubmit: (content: string) => Promise<void>;
}) {
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submitContent = useCallback(async () => {
    const trimmed = content.trim();
    if (!trimmed || disabled || submitting) {
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit(trimmed);
      setContent("");
    } catch (error) {
      console.error("[FollowUpComposer] submit failed", error);
    } finally {
      setSubmitting(false);
    }
  }, [content, disabled, onSubmit, submitting]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await submitContent();
  }

  async function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    await submitContent();
  }

  return (
    <form className="space-y-3" onSubmit={handleSubmit}>
      <div className="rounded-[24px] border border-[rgba(154,180,163,.35)] bg-[linear-gradient(180deg,rgba(255,255,255,.96),rgba(241,247,243,.92))] p-3 shadow-[0_20px_60px_rgba(118,146,126,.08)]">
        <Textarea
          className="min-h-[120px] resize-none rounded-[18px] border-0 bg-transparent px-3 py-3 text-[15px] leading-7 shadow-none focus-visible:ring-0"
          placeholder="继续追问利用链、修复方案、验证步骤或证据来源……"
          value={content}
          onChange={(event) => setContent(event.target.value)}
          onKeyDown={(event) => void handleKeyDown(event)}
          disabled={disabled || submitting}
        />
        <div className="mt-3 flex items-center justify-between gap-3 border-t border-[rgba(154,180,163,.2)] px-2 pt-3 text-xs text-muted-foreground">
          <span>Enter 发送，Shift + Enter 换行</span>
          <Button
            type="submit"
            disabled={disabled || submitting || !content.trim()}
            className="h-11 rounded-full bg-[linear-gradient(135deg,#89A98D,#5E7A63)] px-5 text-white shadow-[0_16px_35px_rgba(94,122,99,.22)] hover:opacity-95"
          >
            {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <SendHorizonal className="mr-2 h-4 w-4" />}
            {submitting ? "发送中..." : "发送追问"}
          </Button>
        </div>
      </div>
    </form>
  );
}
