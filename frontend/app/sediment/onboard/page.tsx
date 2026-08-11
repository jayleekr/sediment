"use client";

import { useState } from "react";
import { Surface, TrustBadge } from "../components/ui";

// 이 폼 전체가 쓰는 입력 레시피. Surface 처럼 한곳에 두어야 다음 필드가
// 추가될 때 또 손으로 재조립되지 않는다.
const FIELD =
  "w-full rounded-md border border-rule bg-paper-2/30 px-3 py-2 text-sm outline-none " +
  "transition-colors placeholder:text-ink-3 focus:border-accent focus:bg-card";

type Step = "workspace" | "invite" | "sources" | "ingest" | "done";

export default function OnboardPage() {
  const [step, setStep] = useState<Step>("workspace");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [emails, setEmails] = useState("");
  const [sources, setSources] = useState<string[]>(["markdown"]);

  function next() {
    if (step === "workspace") setStep("invite");
    else if (step === "invite") setStep("sources");
    else if (step === "sources") setStep("ingest");
    else if (step === "ingest") setStep("done");
  }

  function toggleSource(s: string) {
    setSources((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className="rounded-lg border border-ochre/40 bg-ochre-soft/50 px-4 py-3 text-sm text-ink-2">
        <div className="flex flex-wrap items-center gap-2">
          <TrustBadge tone="warning">demo only</TrustBadge>
          <span className="font-medium">No workspace or ingest job is created from this stub yet.</span>
        </div>
      </div>
      <Stepper current={step} />

      <Surface className="p-6">
        {step === "workspace" && (
          <>
            <h2 className="mb-3 font-display text-lg font-semibold text-ink">1. Create your workspace</h2>
            <label className="mb-3 block text-sm">
              Display name
              <input
                className={`mt-1 ${FIELD}`}
                placeholder="Acme Editorial"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="mb-3 block text-sm">
              URL slug
              <div className="mt-1 flex">
                <input
                  className={`${FIELD} flex-1 rounded-r-none`}
                  placeholder="acme-editorial"
                  value={slug}
                  onChange={(e) => setSlug(e.target.value.replace(/[^a-z0-9-]/g, ""))}
                />
                <span className="whitespace-nowrap rounded-r-md border border-l-0 border-rule bg-paper-2 px-3 py-2 font-mono text-[13px] text-ink-2">
                  .curator.hypeproof-ai.xyz
                </span>
              </div>
            </label>
          </>
        )}

        {step === "invite" && (
          <>
            <h2 className="mb-3 font-display text-lg font-semibold text-ink">2. Invite your team</h2>
            <p className="mb-2 text-sm text-ink-2">
              One email per line. Free tier: up to 3 seats.
            </p>
            <textarea
              rows={6}
              className={`${FIELD} font-mono`}
              placeholder="alice@acme.com&#10;bob@acme.com"
              value={emails}
              onChange={(e) => setEmails(e.target.value)}
            />
          </>
        )}

        {step === "sources" && (
          <>
            <h2 className="mb-3 font-display text-lg font-semibold text-ink">3. Connect vault sources</h2>
            <ul className="space-y-2 text-sm">
              {[
                ["markdown", "Markdown ZIP upload (any folder structure)"],
                ["drive", "Google Drive (folder picker)"],
                ["notion", "Notion workspace"],
                ["github", "GitHub repo (read-only)"],
                ["discord", "Discord server (Mother bot)"],
              ].map(([id, label]) => (
                <li key={id}>
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={sources.includes(id)}
                      onChange={() => toggleSource(id)}
                    />
                    {label}
                  </label>
                </li>
              ))}
            </ul>
          </>
        )}

        {step === "ingest" && (
          <>
            <h2 className="mb-3 font-display text-lg font-semibold text-ink">4. Initial indexing</h2>
            <p className="mb-3 text-sm text-ink-2">
              We&apos;ll embed your sources in the background (~30s per 100 docs). You&apos;ll
              get an email when it&apos;s done.
            </p>
            <div className="mb-3 space-y-1 rounded-md border border-rule bg-paper-2/50 p-3.5 text-sm text-ink-2">
              <div>Workspace: <strong>{name || "(unnamed)"}</strong></div>
              <div>Slug: <code>{slug || "(empty)"}</code></div>
              <div>Invitees: {emails.split("\n").filter(Boolean).length}</div>
              <div>Sources: {sources.join(", ")}</div>
            </div>
          </>
        )}

        {step === "done" && (
          <>
            <h2 className="mb-3 font-display text-lg font-semibold text-ink">All set!</h2>
            <p className="mb-3 text-sm text-ink-2">
              Your workspace is being prepared. You&apos;ll receive an email when ingest completes.
            </p>
            <a
              href="/sediment"
              className="inline-flex min-h-9 items-center rounded-md bg-ink px-4 py-2 text-sm text-paper transition-colors hover:bg-accent-ink"
            >
              Go to chat →
            </a>
          </>
        )}

        {step !== "done" && (
          <div className="mt-6 flex justify-end">
            <button
              onClick={next}
              disabled={(step === "workspace" && !slug) || (step === "invite" && !emails)}
              className="min-h-10 rounded-md bg-accent px-5 py-2 text-[15px] font-medium text-paper transition-colors hover:bg-accent-ink disabled:opacity-50"
            >
              {step === "ingest" ? "Start ingest" : "Next"}
            </button>
          </div>
        )}
      </Surface>

      <p className="text-center text-xs text-ink-3">
        Phase 6 stub — backend wiring lands when first external customer signs up.
      </p>
    </div>
  );
}

function Stepper({ current }: { current: Step }) {
  const steps: Step[] = ["workspace", "invite", "sources", "ingest", "done"];
  return (
    <div className="flex items-center justify-between text-xs">
      {steps.map((s, i) => {
        const idx = steps.indexOf(current);
        const past = i <= idx;
        return (
          <div key={s} className="flex flex-1 items-center">
            <span
              className={`flex h-7 w-7 items-center justify-center rounded-full ${
                past ? "bg-accent text-paper" : "bg-rule"
              }`}
            >
              {i + 1}
            </span>
            <span className={`ml-2 capitalize ${past ? "text-ink" : "text-ink-3"}`}>
              {s}
            </span>
            {i < steps.length - 1 && (
              <span className={`mx-2 h-px flex-1 ${past ? "bg-accent" : "bg-rule"}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}
