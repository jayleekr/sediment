"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { api, citeExport, getFreshness, type Citation, type Freshness, type Message } from "../../lib/api";
import { streamCurator } from "../../lib/sse";

// Backend phrases emitted by lab_lib/grounding.no_evidence_answer when the
// retriever returned 0 candidates. Detected here to render a richer empty
// state with a deep-link to the Library search (which uses a different,
// more permissive retrieval path — see sediment#52).
const NO_EVIDENCE_PATTERNS = [
  "현재 vault에서 이 질문을 뒷받침할 근거를 찾지 못했습니다",
  "근거를 찾지 못했습니다",
  "no evidence",
];

function isNoEvidenceText(text: string | undefined): boolean {
  if (!text) return false;
  const t = text.toLowerCase();
  return NO_EVIDENCE_PATTERNS.some((p) => t.includes(p.toLowerCase()));
}

type StreamState = {
  status: string;
  citations: Citation[];
  buffer: string;
  done: boolean;
  error?: string;
};

export default function ConversationPage() {
  const { id } = useParams<{ id: string }>();
  const sp = useSearchParams();
  const autoAsk = sp.get("ask") === "1";

  const [conv, setConv] = useState<any>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [stream, setStream] = useState<StreamState>({ status: "", citations: [], buffer: "", done: true });
  // S3(a) signal: when on, queries are tagged task_tag='owned'. The concierge
  // sets this for the member's one owned task; persisted per browser.
  const [ownedMode, setOwnedMode] = useState(false);
  const aborter = useRef<AbortController | null>(null);

  useEffect(() => {
    setOwnedMode(localStorage.getItem("sediment.owned_task") === "1");
  }, []);

  async function load() {
    const data = await api<{ conversation: any; messages: Message[] }>(
      `/api/v1/conversations/${id}`
    );
    setConv(data.conversation);
    setMessages(data.messages);
    return data.messages;
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const msgs = await load();
      if (cancelled) return;
      if (autoAsk && msgs.length && msgs[msgs.length - 1].role === "user") {
        ask(msgs[msgs.length - 1].content, /*alreadySaved*/ true);
      }
    })();
    return () => {
      cancelled = true;
      aborter.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function ask(q: string, alreadySaved = false) {
    if (!q.trim()) return;
    if (!alreadySaved) {
      await api(`/api/v1/conversations/${id}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: q, role: "user" }),
      });
      setMessages((m) => [
        ...m,
        { id: "tmp-" + Date.now(), role: "user", content: q, citations: [], ts: new Date().toISOString() },
      ]);
    }
    setInput("");
    setStream({ status: "thinking…", citations: [], buffer: "", done: false });
    aborter.current = new AbortController();
    await streamCurator(
      id,
      q,
      {
        onStatus: (_msg, meta) => setStream((s) => ({ ...s, status: `${meta?.step ?? ""} ${meta?.intent ? `(${meta.intent})` : ""}`.trim() })),
        onCitation: (c) => setStream((s) => ({ ...s, citations: [...s.citations, c] })),
        onDelta: (t) => setStream((s) => ({ ...s, buffer: s.buffer + t })),
        onAnswerEnd: () => setStream((s) => ({ ...s, done: true, status: "done" })),
        onError: (e) => setStream((s) => ({ ...s, error: e.message, done: true })),
        onDone: async () => {
          setStream({ status: "done", citations: [], buffer: "", done: true });
          await load(); // re-pull messages so citations are persisted
        },
      },
      aborter.current.signal,
      ownedMode ? "owned" : undefined
    );
  }

  // For each assistant turn, look back for the most recent user turn so a
  // "no evidence" panel can echo the original question + deep-link Library
  // search. Mapping is computed once per render; messages list is small.
  const userQueryFor = (idx: number): string => {
    for (let i = idx - 1; i >= 0; i--) {
      if (messages[i].role === "user") return messages[i].content;
    }
    return "";
  };

  // For the live streaming bubble, the most recent user turn is the source.
  const lastUserQuery: string =
    [...messages].reverse().find((m) => m.role === "user")?.content || "";

  return (
    <div className="grid grid-cols-12 gap-6">
      <main className="col-span-12 md:col-span-8">
        <div className="rounded-xl border bg-white p-6 shadow-sm">
          <h2 className="mb-3 text-lg font-semibold">{conv?.title || "(untitled)"}</h2>
          <div className="space-y-4">
            {messages.map((m, i) => (
              <div key={m.id}>
                <Bubble role={m.role} content={m.content} citations={m.citations} />
                {m.role === "assistant" &&
                  (!m.citations || m.citations.length === 0) &&
                  isNoEvidenceText(m.content) && (
                    <NoEvidencePanel query={userQueryFor(i)} />
                  )}
              </div>
            ))}
            {!stream.done || stream.buffer ? (
              <>
                <Bubble role="assistant" content={stream.buffer || "thinking…"} citations={stream.citations} streaming />
                {stream.done &&
                  stream.citations.length === 0 &&
                  isNoEvidenceText(stream.buffer) && (
                    <NoEvidencePanel query={lastUserQuery} />
                  )}
              </>
            ) : null}
            {stream.error && <p className="text-sm text-red-600">error: {stream.error}</p>}
          </div>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(input);
          }}
          className="mt-4 flex gap-2"
        >
          <input
            className="flex-1 rounded border px-3 py-2"
            placeholder="ask the lab…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={!stream.done}
          />
          <button
            className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
            disabled={!stream.done}
          >
            Send
          </button>
        </form>
        <label className="mt-2 flex items-center gap-2 text-xs text-neutral-500">
          <input
            type="checkbox"
            checked={ownedMode}
            onChange={(e) => {
              setOwnedMode(e.target.checked);
              localStorage.setItem("sediment.owned_task", e.target.checked ? "1" : "0");
            }}
          />
          🎯 This is my owned-task lookup (replacing grep / Drive / Discord)
        </label>
      </main>

      <aside className="col-span-12 md:col-span-4">
        <div className="rounded-xl border bg-white p-4 shadow-sm">
          <h3 className="mb-2 text-sm font-semibold">Citations</h3>
          {stream.citations.length === 0 && messages.length === 0 ? (
            <p className="text-xs text-neutral-600">Will appear here as the agent searches.</p>
          ) : (
            <ul className="space-y-3 text-sm">
              {(stream.citations.length ? stream.citations : (messages.flatMap((m) => m.citations || []) as Citation[]))
                .slice(0, 10)
                .map((c, i) => (
                  <CitationCard key={i} index={i + 1} citation={c} />
                ))}
            </ul>
          )}
          <div className="mt-4 border-t pt-3 text-xs text-neutral-600">
            <div>status: {stream.status || "idle"}</div>
          </div>
        </div>
      </aside>
    </div>
  );
}

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Explicit element styling — Tailwind 4 here has no @tailwindcss/typography
// plugin, so `prose` is a no-op. Map each markdown node to concrete classes.
const MD_COMPONENTS = {
  h1: (p: any) => <h1 className="mb-3 mt-5 border-b pb-1 text-xl font-bold" {...p} />,
  h2: (p: any) => <h2 className="mb-2 mt-5 text-lg font-semibold" {...p} />,
  h3: (p: any) => <h3 className="mb-2 mt-4 text-base font-semibold" {...p} />,
  h4: (p: any) => <h4 className="mb-1 mt-3 text-sm font-semibold" {...p} />,
  p:  (p: any) => <p className="my-2 leading-relaxed" {...p} />,
  ul: (p: any) => <ul className="my-2 list-disc space-y-1 pl-5" {...p} />,
  ol: (p: any) => <ol className="my-2 list-decimal space-y-1 pl-5" {...p} />,
  li: (p: any) => <li className="leading-relaxed" {...p} />,
  a:  (p: any) => <a className="text-blue-600 underline" target="_blank" rel="noreferrer" {...p} />,
  blockquote: (p: any) => (
    <blockquote className="my-2 border-l-4 border-neutral-300 pl-3 italic text-neutral-600" {...p} />
  ),
  code: ({ inline, ...p }: any) =>
    inline ? (
      <code className="rounded bg-neutral-100 px-1 py-0.5 font-mono text-[0.85em]" {...p} />
    ) : (
      <code className="font-mono text-xs" {...p} />
    ),
  pre: (p: any) => (
    <pre
      className="my-3 overflow-x-auto rounded bg-neutral-900 p-3 text-xs leading-snug text-neutral-100"
      {...p}
    />
  ),
  table: (p: any) => (
    <div className="my-3 overflow-x-auto">
      <table className="w-full border-collapse text-xs" {...p} />
    </div>
  ),
  th: (p: any) => <th className="border border-neutral-300 bg-neutral-100 px-2 py-1 text-left font-semibold" {...p} />,
  td: (p: any) => <td className="border border-neutral-300 px-2 py-1 align-top" {...p} />,
  hr: () => <hr className="my-4 border-neutral-200" />,
  img: (p: any) => <img className="my-2 max-w-full rounded" {...p} />,
};

function CitationCard({ index, citation }: { index: number; citation: Citation }) {
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const hasRef = Boolean(citation.ref);
  const decisionProvenance =
    citation.decision_provenance || (citation.type === "decision" ? citation.provenance : null);
  const provenanceMissing = Boolean(decisionProvenance?.missing);
  const provenanceLabel = decisionProvenance
    ? provenanceMissing
      ? "provenance missing"
      : [
          decisionProvenance.source_title || decisionProvenance.source_ref,
          decisionProvenance.channel ? `#${decisionProvenance.channel}` : null,
          decisionProvenance.source_date,
        ]
          .filter(Boolean)
          .join(" · ")
    : "";
  const citeText = `[${citation.ref || citation.display_name || "ref"}]${
    citation.content ? " " + citation.content : ""
  }`;

  async function copyCite() {
    try {
      await navigator.clipboard.writeText(citeText);
    } catch {
      /* clipboard may be blocked; still log the intent */
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
    citeExport("clipboard", { citation_ref: citation.ref || undefined });
  }

  function shareCite() {
    navigator.clipboard?.writeText(citeText).catch(() => {});
    citeExport("thread-share", { citation_ref: citation.ref || undefined });
  }

  async function openModal() {
    if (!hasRef) return;
    setOpen(true);
    if (body === null && !loading) {
      setLoading(true);
      setError(null);
      try {
        const resp = await api<{ body: string }>(
          `/api/v1/library/${encodeURI(citation.ref!)}`,
        );
        setBody(resp.body || "(empty)");
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    }
  }

  // Esc to close
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <li className="border-b pb-2 last:border-0">
        <button
          type="button"
          onClick={openModal}
          disabled={!hasRef}
          className={`w-full text-left ${
            hasRef ? "cursor-pointer hover:bg-neutral-50" : "cursor-default"
          } rounded px-1 py-0.5 transition`}
        >
          <div className="font-mono text-xs text-neutral-500">
            [{index}]{hasRef && <span className="ml-1 text-blue-600">↗ open</span>}
          </div>
          <div className="font-medium">{citation.ref || citation.display_name || "(no ref)"}</div>
          {citation.type && (
            <div className="text-xs text-neutral-600">
              {citation.type}
              {citation.date ? ` · ${citation.date}` : ""}
            </div>
          )}
          {citation.type === "decision" && (
            <div
              className={`mt-1 text-xs ${
                provenanceMissing ? "text-amber-700" : "text-emerald-700"
              }`}
            >
              {provenanceLabel || "provenance missing"}
            </div>
          )}
          {citation.content && (
            <div className="mt-1 line-clamp-2 text-xs text-neutral-700">{citation.content}</div>
          )}
        </button>
        <div className="mt-1 flex gap-3 text-xs">
          <button
            type="button"
            onClick={copyCite}
            className="text-neutral-500 hover:text-neutral-900"
          >
            {copied ? "✓ copied" : "📋 cite"}
          </button>
          <button
            type="button"
            onClick={shareCite}
            className="text-neutral-500 hover:text-neutral-900"
          >
            ↗ share
          </button>
        </div>
      </li>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => setOpen(false)}
        >
          <div
            className="flex max-h-[85vh] w-full max-w-4xl flex-col rounded-xl bg-white shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b px-5 py-3">
              <div>
                <div className="font-mono text-xs text-neutral-500">[{index}]</div>
                <div className="font-semibold">{citation.ref}</div>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="rounded p-1 text-neutral-500 hover:bg-neutral-100"
                aria-label="Close"
              >
                ✕
              </button>
            </div>
            <div className="overflow-y-auto px-6 py-4 text-sm text-neutral-900">
              {loading && <p className="text-neutral-500">Loading…</p>}
              {error && <p className="text-red-600">error: {error}</p>}
              {body !== null && (
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                  {body}
                </ReactMarkdown>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

const OFFLINE_MOCK_PREFIX = "[offline LLM mock]";
const OFFLINE_FALLBACK =
  "AI provider not configured (offline mode). Citations are real; replies are mocked. Set LLM_PROVIDER in .env to enable streaming responses.";

// Rendered when the chat retriever returned 0 candidates. Offers a deep-link
// to the Library /search endpoint (different retrieval path — more permissive
// BM25 + dedup) and surfaces vault freshness so the user can tell whether the
// gap is "not ingested yet" vs "genuinely absent". See sediment#52.
function NoEvidencePanel({ query }: { query: string }) {
  const [fresh, setFresh] = useState<Freshness | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const f = await getFreshness();
        if (!cancelled) setFresh(f);
      } catch {
        /* freshness is informational — silent fail */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const freshLabel = (() => {
    if (!fresh) return null;
    const s = fresh.seconds_ago;
    if (fresh.last_ingest_ts == null) return "vault: never ingested";
    if (s == null) return "vault: ?";
    if (s < 3600) return `vault ${Math.max(1, Math.round(s / 60))}m ago`;
    if (s < 86400) return `vault ${Math.round(s / 3600)}h ago`;
    return `vault ${Math.round(s / 86400)}d ago`;
  })();

  const trimmed = (query || "").trim();
  const libHref = trimmed
    ? `/sediment/library?q=${encodeURIComponent(trimmed)}`
    : `/sediment/library`;

  return (
    <div
      data-testid="no-evidence-panel"
      className="ml-2 mt-2 max-w-[80%] rounded-xl border border-dashed border-neutral-300 bg-neutral-50 px-4 py-3 text-sm text-neutral-700"
    >
      <div className="font-medium text-neutral-900">No evidence yet for this question.</div>
      {trimmed && (
        <div className="mt-1 text-xs text-neutral-600">
          Searched for: <span className="font-mono">{trimmed}</span>
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
        <Link
          href={libHref}
          className="rounded bg-blue-600 px-2 py-1 font-medium text-white hover:bg-blue-700"
        >
          Search vault for {trimmed ? `“${trimmed.length > 28 ? trimmed.slice(0, 28) + "…" : trimmed}”` : "this"} →
        </Link>
        {freshLabel && (
          <span className="text-neutral-500" title="Most recent ingest time">
            {freshLabel}
          </span>
        )}
        <Link href="/sediment/library" className="text-neutral-500 underline hover:text-neutral-900">
          recent ingests
        </Link>
      </div>
      <p className="mt-2 text-xs text-neutral-500">
        Tip: chat retrieval is stricter than library search. Direct name or filename queries often work better there.
      </p>
    </div>
  );
}

function Bubble({
  role,
  content,
  citations,
  streaming,
}: {
  role: string;
  content: string;
  citations?: any[];
  streaming?: boolean;
}) {
  const isUser = role === "user";
  const displayContent =
    !isUser && content.startsWith(OFFLINE_MOCK_PREFIX) ? OFFLINE_FALLBACK : content;
  return (
    <div data-role={role} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-2 text-sm ${
          isUser ? "bg-blue-600 text-white" : "bg-neutral-100 text-neutral-900"
        }`}
      >
        <div className="whitespace-pre-wrap">{displayContent}</div>
        {streaming && <span className="ml-1 animate-pulse">▍</span>}
        {citations && citations.length > 0 && !isUser && (
          <div className="mt-2 text-xs text-neutral-600">
            {citations.length} citation{citations.length === 1 ? "" : "s"}
          </div>
        )}
      </div>
    </div>
  );
}
