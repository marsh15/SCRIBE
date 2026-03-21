import type { UIMessage } from "@ai-sdk/react";

type ToolInvocationLike = {
  toolName?: string;
  toolCallId?: string;
  args?: unknown;
  result?: unknown;
  state?: string;
};

type MessagePartLike = {
  type?: string;
  toolName?: string;
  toolCallId?: string;
  args?: unknown;
  input?: unknown;
  result?: unknown;
  output?: unknown;
  state?: string;
};

export function getToolInvocations(message: UIMessage): ToolInvocationLike[] {
  const legacyToolInvocations = Array.isArray((message as { toolInvocations?: unknown }).toolInvocations)
    ? (((message as { toolInvocations?: unknown }).toolInvocations as ToolInvocationLike[]) ?? [])
    : [];

  const partToolInvocations = (message.parts ?? []).flatMap((part) => {
    const toolPart = part as MessagePartLike;

    if (toolPart.type === "tool-invocation") {
      return [
        {
          toolName: toolPart.toolName,
          toolCallId: toolPart.toolCallId,
          args: toolPart.args,
          result: toolPart.result,
          state: toolPart.state,
        },
      ];
    }

    if (typeof toolPart.type === "string" && toolPart.type.startsWith("tool-")) {
      return [
        {
          toolName: toolPart.type.slice(5),
          toolCallId: toolPart.toolCallId,
          args: toolPart.input ?? toolPart.args,
          result:
            toolPart.state === "output-available"
              ? toolPart.output
              : toolPart.result,
          state: toolPart.state,
        },
      ];
    }

    return [];
  });

  return [...legacyToolInvocations, ...partToolInvocations];
}

export function messageUsesKnowledgeBase(message: UIMessage) {
  return getToolInvocations(message).some(
    (invocation) => invocation.toolName === "searchKnowledgeBase",
  );
}

export function countKnowledgeBaseInvocations(messages: UIMessage[]) {
  return messages
    .flatMap(getToolInvocations)
    .filter((invocation) => invocation.toolName === "searchKnowledgeBase")
    .length;
}

export function getLastKnowledgeBaseInvocation(messages: UIMessage[]) {
  return messages
    .flatMap(getToolInvocations)
    .filter((invocation) => invocation.toolName === "searchKnowledgeBase")
    .pop();
}
