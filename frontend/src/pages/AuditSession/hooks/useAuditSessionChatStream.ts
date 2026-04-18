import { useCallback, useEffect, useRef, useState } from "react";

import { streamAuditSessionMessage, type AuditSessionMessage, type AuditSessionStreamEvent } from "@/shared/api/auditSessions";

function upsertMessage(messages: AuditSessionMessage[], nextMessage: AuditSessionMessage): AuditSessionMessage[] {
  const index = messages.findIndex((message) => message.id === nextMessage.id);
  if (index === -1) {
    return [...messages, nextMessage].sort((left, right) => left.sequence - right.sequence);
  }
  const clone = [...messages];
  clone[index] = nextMessage;
  return clone;
}

export function useAuditSessionChatStream({
  sessionId,
  setMessages,
  refresh,
  streamMessage = streamAuditSessionMessage,
}: {
  sessionId?: string;
  setMessages: React.Dispatch<React.SetStateAction<AuditSessionMessage[]>>;
  refresh: (options?: { silent?: boolean }) => Promise<void>;
  streamMessage?: (
    sessionId: string,
    content: string,
    handlers?: {
      onEvent?: (event: AuditSessionStreamEvent) => void;
      signal?: AbortSignal;
    },
  ) => Promise<void>;
}) {
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const streamingAssistantIdRef = useRef<string | null>(null);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  const handleEvent = useCallback((event: AuditSessionStreamEvent) => {
    if (event.type === "user_message" && event.message) {
      setMessages((previous) => upsertMessage(previous, event.message!));
      return;
    }

    if (event.type === "assistant_start" && event.message) {
      streamingAssistantIdRef.current = event.message.id;
      setMessages((previous) => upsertMessage(previous, event.message!));
      return;
    }

    if (event.type === "token") {
      const streamingAssistantId = streamingAssistantIdRef.current;
      if (!streamingAssistantId) {
        return;
      }
      setMessages((previous) =>
        previous.map((message) =>
          message.id === streamingAssistantId
            ? {
                ...message,
                content: event.accumulated ?? `${message.content}${event.content ?? ""}`,
              }
            : message,
        ),
      );
      return;
    }

    if (event.type === "done" && event.message) {
      setMessages((previous) => {
        const withoutPlaceholder = previous.filter((message) => message.id !== streamingAssistantIdRef.current);
        return upsertMessage(withoutPlaceholder, event.message!);
      });
      streamingAssistantIdRef.current = null;
      return;
    }

    if (event.type === "error") {
      setStreamError(event.message_text || "Streaming failed");
    }
  }, [setMessages]);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
    void refresh({ silent: true });
  }, [refresh]);

  const runStreamRequest = useCallback(async (
    runner: (handlers: { onEvent?: (event: AuditSessionStreamEvent) => void; signal?: AbortSignal }) => Promise<void>,
  ) => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    streamingAssistantIdRef.current = null;
    setIsStreaming(true);
    setStreamError(null);

    try {
      await runner({
        signal: abortRef.current.signal,
        onEvent: handleEvent,
      });
      await refresh({ silent: true });
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setStreamError(error instanceof Error ? error.message : "Streaming failed");
        await refresh({ silent: true });
        throw error;
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [handleEvent, refresh]);

  const sendMessage = useCallback(async (content: string) => {
    if (!sessionId) {
      throw new Error("Missing session id");
    }
    await runStreamRequest((handlers) => streamMessage(sessionId, content, handlers));
  }, [runStreamRequest, sessionId, streamMessage]);

  return {
    isStreaming,
    streamError,
    runStreamRequest,
    sendMessage,
    stopStreaming,
    streamingAssistantId: streamingAssistantIdRef.current,
  };
}
