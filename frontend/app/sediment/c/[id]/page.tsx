"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { api, citeExport, getFreshness, type Citation, type Freshness, type Message } from "../../lib/api";
import { streamSediment } from "../../lib/sse";
import { EmptyState, SectionHeader, Surface, TrustBadge } from "../../components/ui";

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
  const [loadError, setLoadError] = useState<string | null>(null);
  const [stream, setStream] = useState<StreamState>({ status: "", citations: [], buffer: "", done: true });
  // S3(a) signal: when on, queries are tagged task_tag='owned'. The concierge
  // sets this for the member's one owned task; persisted per browser.
  const [ownedMode, setOwnedMode] = useState(false);
  const aborter = useRef<AbortController | null>(null);

  useEffect(() => {
    setOwnedMode(localStorage.getItem("sediment.owned_task") === "1");
  }, []);

  async function load() {
    try {
      const data = await api<{ conversation: any; messages: Message[] }>(
        `/api/v1/conversations/${id}`
      );
      setLoadError(null);
      setConv(data.conversation);
      setMessages(data.messages);
      return data.messages;
    } catch (e: unknown) {
      if (e instanceof Error) setLoadError(e.message);
      else setLoadError(String(e));
      throw e;
    }
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let msgs: Message[];
      try {
        msgs = await load();
      } catch {
        return;
      }
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
    try {
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
    } catch (e: unknown) {
      setStream({
        status: "failed",
        citations: [],
        buffer: "",
        done: true,
        error: e instanceof Error ? e.message : String(e),
      });
      return;
    }
    setInput("");
    setStream({ status: "thinking…", citations: [], buffer: "", done: false });
    aborter.current = new AbortController();
    await streamSediment(
      id,
      q,
      {
        onStatus: (_msg, meta) => setStream((s) => ({ ...s, status: `${meta?.step ?? ""} ${meta?.intent ? `(${meta.intent})` : ""}`.trim() })),
        onCitation: (c) => setStream((s) => ({ ...s, citations: [...s.citations, c] })),
        onDelta: (t) => setStream((s) => ({ ...s, buffer: s.buffer + t })),
        onAnswerEnd: () => setStream((s) => ({ ...s, done: true, status: "done" })),
        onError: (e) => setStream((s) => ({ ...s, error: e.message, done: true, status: "failed" })),
        onDone: async () => {
          setStream((s) => ({
            ...s,
            status: s.error ? "failed" : "done",
            done: true,
          }));
          try {
            await load(); // re-pull messages so citations are persisted
          } catch {
            // Keep the stream error visible instead of replacing it with a blank shell.
          }
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
  const shownCitations = stream.citations.length
    ? stream.citations
    : (messages.flatMap((m) => m.citations || []) as Citation[]);

  if (loadError) {
    return (
      <Surface className="mx-auto max-w-2xl p-6 md:p-7">
        <h1 className="mb-3 font-display text-2xl font-semibold text-ink">Sediment</h1>
        <div role="alert" className="rounded-md border border-accent/30 bg-claret-soft/50 p-4 text-sm text-accent-ink">
          <p className="font-display text-base font-semibold">Conversation could not be loaded.</p>
          <p className="mt-1 font-mono text-xs">{loadError}</p>
          {loadError.startsWith("401") && (
            <p className="mt-2">Sign in again, then reopen this conversation.</p>
          )}
        </div>
        <Link
          href="/sediment"
          className="mt-5 inline-flex rounded-md bg-ink px-4 py-2 text-[15px] text-paper transition-colors hover:bg-accent-ink"
        >
          ← Back to Sediment
        </Link>
      </Surface>
    );
  }

  return (
    <div className="grid grid-cols-12 gap-6">
      <main className="col-span-12 md:col-span-8">
        <Surface className="p-5 md:p-6">
          <SectionHeader
            title={conv?.title || "(untitled)"}
            description="Answers are only useful when the evidence can be inspected."
            action={
              <div className="flex flex-wrap gap-2">
                <TrustBadge tone="info">all vault</TrustBadge>
                <TrustBadge tone={shownCitations.length ? "success" : "neutral"}>
                  {shownCitations.length} citation{shownCitations.length === 1 ? "" : "s"}
                </TrustBadge>
              </div>
            }
          />
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
            {stream.error && (
              <div role="alert" className="rounded-md border border-accent/30 bg-claret-soft/50 p-3 text-sm text-accent-ink">
                <p className="font-display text-base font-semibold">Answer generation failed.</p>
                <p className="mt-1 font-mono text-xs">{stream.error}</p>
              </div>
            )}
          </div>
        </Surface>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(input);
          }}
          className="mt-4 flex flex-col gap-2 rounded-md border border-rule bg-card p-3 shadow-sm sm:flex-row"
        >
          <input
            className="min-h-11 flex-1 rounded-md border border-rule bg-paper-2/30 px-3.5 py-2.5 text-[15px] outline-none transition-colors placeholder:text-ink-3 focus:border-accent focus:bg-card"
            placeholder="ask the lab…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={!stream.done}
          />
          <button
            className="min-h-11 rounded-md bg-accent px-5 py-2 text-[15px] font-medium text-paper transition-colors hover:bg-accent-ink disabled:opacity-50"
            disabled={!stream.done}
          >
            Send
          </button>
        </form>
        <label className="mt-2.5 flex items-start gap-2 text-[13px] leading-6 text-ink-2">
          <input
            className="mt-1 accent-[#8b3a2c]"
            type="checkbox"
            checked={ownedMode}
            onChange={(e) => {
              setOwnedMode(e.target.checked);
              localStorage.setItem("sediment.owned_task", e.target.checked ? "1" : "0");
            }}
          />
          <span>This is my owned-task lookup, replacing grep, Drive, or Discord scroll.</span>
        </label>
      </main>

      <aside className="col-span-12 md:col-span-4">
        <Surface as="aside" className="sticky top-4 p-5">
          <div className="flex items-baseline justify-between">
            <h2 className="font-display text-xl font-semibold leading-tight text-ink">
              Evidence
            </h2>
            <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-3">
              apparatus
            </span>
          </div>
          <p className="mt-1.5 text-[14px] leading-6 text-ink-2">
            Open a source before copying an answer into work.
          </p>
          <hr className="rule-dotted mt-4" />
          {shownCitations.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                title="No citations yet"
                description="Sources will appear here as retrieval finds support for the answer."
              />
            </div>
          ) : (
            <ul className="mt-2 text-sm">
              {shownCitations.slice(0, 10).map((c, i) => (
                <CitationCard key={citationKey(c, i)} index={i + 1} citation={c} />
              ))}
            </ul>
          )}
          <div className="mt-5 border-t border-rule pt-3 font-mono text-[11px] uppercase tracking-[0.14em] text-ink-3">
            status: {stream.status || "idle"}
          </div>
        </Surface>
      </aside>
    </div>
  );
}

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Explicit element styling — Tailwind 4 here has no @tailwindcss/typography
// plugin, so `prose` is a no-op. Map each markdown node to concrete classes
// in the Editorial-Archive voice: Fraunces display headings, Newsreader body,
// oxblood links, a warm-ink code block, and ledger-ruled tables.
const MD_COMPONENTS = {
  h1: (p: any) => <h1 className="mb-3 mt-5 border-b border-rule pb-1.5 font-display text-2xl font-semibold text-ink" {...p} />,
  h2: (p: any) => <h2 className="mb-2 mt-5 font-display text-xl font-semibold text-ink" {...p} />,
  h3: (p: any) => <h3 className="mb-2 mt-4 font-display text-lg font-semibold text-ink" {...p} />,
  h4: (p: any) => <h4 className="mb-1 mt-3 font-display text-base font-semibold text-ink" {...p} />,
  p:  (p: any) => <p className="my-2.5 leading-7 text-ink" {...p} />,
  ul: (p: any) => <ul className="my-2.5 list-disc space-y-1 pl-5 marker:text-ink-3" {...p} />,
  ol: (p: any) => <ol className="my-2.5 list-decimal space-y-1 pl-5 marker:text-ink-3" {...p} />,
  li: (p: any) => <li className="leading-7" {...p} />,
  a:  (p: any) => <a className="text-accent underline decoration-rule-2 decoration-1 underline-offset-2 transition-colors hover:decoration-accent" target="_blank" rel="noreferrer" {...p} />,
  blockquote: (p: any) => (
    <blockquote className="my-3 border-l-2 border-ochre pl-4 font-body italic text-ink-2" {...p} />
  ),
  code: ({ inline, ...p }: any) =>
    inline ? (
      <code className="rounded-sm bg-ochre-soft/50 px-1.5 py-0.5 font-mono text-[0.82em] text-ink" {...p} />
    ) : (
      <code className="font-mono text-[0.82em]" {...p} />
    ),
  pre: (p: any) => (
    <pre
      className="my-3.5 overflow-x-auto rounded-md bg-ink p-4 font-mono text-xs leading-relaxed text-[#e9e0cb] shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]"
      {...p}
    />
  ),
  table: (p: any) => (
    <div className="my-3.5 overflow-x-auto rounded-sm border border-rule">
      <table className="w-full border-collapse text-[13px]" {...p} />
    </div>
  ),
  th: (p: any) => <th className="border border-rule bg-paper-2 px-2.5 py-1.5 text-left font-mono text-[11px] uppercase tracking-wide text-ink-2" {...p} />,
  td: (p: any) => <td className="border border-rule px-2.5 py-1.5 align-top" {...p} />,
  hr: () => <hr className="my-5 border-rule" />,
  img: (p: any) => (
    <img
      referrerPolicy="no-referrer"
      loading="lazy"
      className="my-3 max-w-full rounded-md border border-rule"
      {...p}
    />
  ),
};

function citationKey(citation: Citation, index: number): string {
  return citation.ref || citation.display_name || `${citation.type || "citation"}-${citation.date || index}`;
}

function CitationCard({ index, citation }: { index: number; citation: Citation }) {
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

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
    closeButtonRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <li className="border-b border-rule py-3 first:pt-2 last:border-0">
        <button
          type="button"
          onClick={openModal}
          disabled={!hasRef}
          className={`group/cite -mx-2 block w-full rounded-md px-2 py-1 text-left transition-colors ${
            hasRef ? "cursor-pointer hover:bg-paper-2/70" : "cursor-default"
          }`}
        >
          <div className="flex items-baseline gap-1.5 font-mono text-[11px] uppercase tracking-wide text-ink-3">
            <span className="text-accent">[{index}]</span>
            {hasRef && (
              <span className="text-ink-3 transition-colors group-hover/cite:text-accent">
                ↗ open
              </span>
            )}
          </div>
          <div className="mt-0.5 font-mono text-[13px] font-medium text-ink">
            {citation.ref || citation.display_name || "(no ref)"}
          </div>
          {citation.type && (
            <div className="font-mono text-[11px] uppercase tracking-wide text-ink-3">
              {citation.type}
              {citation.date ? ` · ${citation.date}` : ""}
            </div>
          )}
          {citation.type === "decision" && (
            <div
              className={`mt-1 font-mono text-[11px] ${
                provenanceMissing ? "text-ochre" : "text-sage"
              }`}
            >
              {provenanceLabel || "provenance missing"}
            </div>
          )}
          {citation.content && (
            <div className="mt-1.5 line-clamp-2 font-body text-[13px] italic leading-6 text-ink-2">
              {citation.content}
            </div>
          )}
        </button>
        <div className="mt-1.5 flex gap-4 font-mono text-[11px] uppercase tracking-wide">
          <button
            type="button"
            onClick={copyCite}
            className={`transition-colors hover:text-accent ${copied ? "text-sage" : "text-ink-3"}`}
          >
            {copied ? "✓ copied" : "cite"}
          </button>
          <button
            type="button"
            onClick={shareCite}
            className="text-ink-3 transition-colors hover:text-accent"
          >
            ↗ share
          </button>
        </div>
      </li>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-[#1a160f]/55 p-4 backdrop-blur-[1px]"
          onClick={() => setOpen(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby={`citation-title-${index}`}
            className="flex max-h-[85vh] w-full max-w-4xl flex-col rounded-lg border border-rule-2 bg-card shadow-[0_24px_70px_-20px_rgba(26,22,15,0.5)]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4 border-b border-rule px-6 py-4">
              <div>
                <div className="font-mono text-[11px] uppercase tracking-[0.14em] text-accent">
                  Source [{index}]
                </div>
                <div
                  id={`citation-title-${index}`}
                  className="mt-1 font-mono text-[15px] font-medium text-ink"
                >
                  {citation.ref}
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {citation.type && <TrustBadge>{citation.type}</TrustBadge>}
                  {citation.date && <TrustBadge>{citation.date}</TrustBadge>}
                  {typeof citation.score === "number" && (
                    <TrustBadge tone="info">score {citation.score.toFixed(3)}</TrustBadge>
                  )}
                  {citation.type === "decision" && (
                    <TrustBadge tone={provenanceMissing ? "warning" : "success"}>
                      {provenanceMissing ? "provenance missing" : "provenance linked"}
                    </TrustBadge>
                  )}
                </div>
              </div>
              <button
                ref={closeButtonRef}
                onClick={() => setOpen(false)}
                className="min-h-9 rounded-md border border-rule-2 px-3 py-1 font-mono text-[11px] uppercase tracking-wide text-ink-2 transition-colors hover:border-accent hover:text-accent"
                aria-label="Close"
              >
                Close
              </button>
            </div>
            <div className="overflow-y-auto px-7 py-5 text-[15px] text-ink">
              {loading && <p className="font-mono text-xs uppercase tracking-wide text-ink-3">Loading…</p>}
              {error && <p className="font-mono text-xs text-accent">error: {error}</p>}
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
      className="ml-2 mt-2.5 max-w-[82%] rounded-md border border-dashed border-rule-2 bg-paper-2/60 px-4 py-3.5 text-sm text-ink-2"
    >
      <div className="font-display text-[15px] font-semibold text-ink">No evidence yet for this question.</div>
      {trimmed && (
        <div className="mt-1.5 text-[13px] text-ink-2">
          Searched for: <span className="font-mono text-ink">{trimmed}</span>
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
        <Link
          href={libHref}
          className="rounded-md bg-accent px-2.5 py-1.5 font-medium text-paper transition-colors hover:bg-accent-ink"
        >
          Search vault for {trimmed ? `“${trimmed.length > 28 ? trimmed.slice(0, 28) + "…" : trimmed}”` : "this"} →
        </Link>
        {freshLabel && (
          <span className="font-mono text-[11px] uppercase tracking-wide text-ink-3" title="Most recent ingest time">
            {freshLabel}
          </span>
        )}
        <Link href="/sediment/library" className="text-ink-3 underline decoration-rule-2 underline-offset-2 transition-colors hover:text-accent">
          recent ingests
        </Link>
      </div>
      <p className="mt-2.5 text-[13px] leading-6 text-ink-3">
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
        className={
          isUser
            ? "max-w-[85%] rounded-md rounded-br-sm bg-accent px-4 py-2.5 text-[15px] text-paper shadow-sm md:max-w-[75%]"
            : "max-w-[94%] rounded-md border border-rule border-l-2 border-l-accent/50 bg-card px-4 py-3 text-[15px] text-ink shadow-sm md:max-w-[82%]"
        }
      >
        {isUser ? (
          <div className="whitespace-pre-wrap font-body leading-7">{displayContent}</div>
        ) : (
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
            {displayContent}
          </ReactMarkdown>
        )}
        {streaming && <span className="ink-caret align-baseline" aria-hidden="true" />}
        {citations && citations.length > 0 && !isUser && (
          <div className="mt-2.5 border-t border-rule pt-2 font-mono text-[11px] uppercase tracking-[0.14em] text-ink-3">
            {citations.length} citation{citations.length === 1 ? "" : "s"}
          </div>
        )}
      </div>
    </div>
  );
}
