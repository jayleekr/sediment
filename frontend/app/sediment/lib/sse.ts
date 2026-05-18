/* SSE client using @microsoft/fetch-event-source pattern, but written with raw fetch
 * to avoid adding a new dependency to the existing web/ project.
 */

import { LANGGRAPH_BASE, getToken } from "./api";

export type SSEHandlers = {
  onStatus?: (msg: string, meta: any) => void;
  onCitation?: (cit: any) => void;
  onDelta?: (token: string) => void;
  onAnswerEnd?: () => void;
  onError?: (err: Error) => void;
  onDone?: () => void;
};

export async function streamCurator(
  conv_id: string,
  query: string,
  h: SSEHandlers,
  signal?: AbortSignal
): Promise<void> {
  const token = getToken();
  const res = await fetch(`${LANGGRAPH_BASE}/v1/sediment/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ conv_id, query }),
    signal,
  });
  if (!res.ok || !res.body) {
    h.onError?.(new Error(`stream: ${res.status} ${res.statusText}`));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "message";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let evt = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) evt = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
      }
      currentEvent = evt;
      const dataStr = dataLines.join("\n");
      if (dataStr === "[DONE]") {
        h.onDone?.();
        return;
      }
      let payload: any;
      try {
        payload = JSON.parse(dataStr);
      } catch {
        continue;
      }
      if (evt === "delta") h.onDelta?.(payload.v ?? "");
      else if (evt === "citation") h.onCitation?.(payload.v);
      else if (evt === "message") {
        const tag = payload?.metadata?.tag;
        if (tag === "answer_end") h.onAnswerEnd?.();
        else if (tag === "error") h.onError?.(new Error(payload.v));
        else h.onStatus?.(payload.v, payload.metadata);
      }
    }
  }
  h.onDone?.();
}
