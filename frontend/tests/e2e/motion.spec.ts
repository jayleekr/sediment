import { expect, test, type Page } from "@playwright/test";

// Motion regressions are invisible to code review and to every other test in
// this suite: the DOM is correct at rest in all of them. What breaks is the
// path between two rest states — an answer that paints in blocks, a viewport
// that stops following a stream, a swap that flashes, a staggered row that
// never fades in. Each of those shipped at least once before these existed.
//
// The backend is stubbed at `window.fetch` rather than with `page.route`
// because route.fulfill() hands the page one complete body: every delta lands
// in a single microtask and the bursty arrival pattern under test never
// happens. A ReadableStream on real timers reproduces it.

const CONV = "11111111-2222-3333-4444-555555555555";
const ANSWER =
  "근거를 확인한 결과, 2026년 4월 mirror-loop 칼럼은 라이언이 작성했으며 반복적 자기참조 루프를 다룹니다. " +
  "해당 글은 에이전트가 자기 출력을 다시 입력으로 삼을 때 발생하는 품질 저하를 다루고, 세 가지 완화 전략을 제시합니다. " +
  "첫째는 외부 근거 고정, 둘째는 세대 간 다양성 유지, 셋째는 인용 기반 검증입니다.";

type StreamShape = "bursty" | "even";

/** Install a fake platform + langgraph. `shape: "bursty"` sends the answer as
 *  three small deltas followed by one ~200-character flush — the buffered
 *  provider write that the smoothing exists to absorb. */
async function installStreamMocks(page: Page, shape: StreamShape = "bursty") {
  await page.addInitScript(
    ({ convId, answer, shape }) => {
      localStorage.setItem("curator.token", "test-token");
      localStorage.setItem("cookie_consent", "accepted");

      const CITATION = {
        ref: "vault/column/2026-04-01.md",
        type: "column",
        date: "2026-04-10",
        score: 0.91,
        content: "mirror-loop 관련 근거 문단입니다.",
      };

      // The flush sits in the MIDDLE, with ordinary deltas after it. Placing it
      // last would measure the wrong thing: once `answer_end` arrives the
      // smoother deliberately switches to a fast closing drain, so a flush at
      // the very end is drained under the tail policy, not the live one.
      const deltas =
        shape === "bursty"
          ? ["근거를 ", "확인한 ", "결과, ", answer.slice(14, 160), answer.slice(160), " 이상입니다."]
          : Array.from({ length: 6 }, (_, i) =>
              answer.slice((answer.length / 6) * i, (answer.length / 6) * (i + 1)),
            );

      let convGets = 0;
      const sse = (event: string, data: unknown) =>
        `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
      const json = (body: unknown) =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });

      const realFetch = window.fetch.bind(window);
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : (input as Request).url ?? String(input);

        if (url.includes("/v1/sediment/stream")) {
          const enc = new TextEncoder();
          const body = new ReadableStream({
            start(c) {
              let t = 0;
              const push = (s: string) => c.enqueue(enc.encode(s));
              setTimeout(
                () => push(sse("message", { v: "retrieving", metadata: { step: "retrieve" } })),
                (t += 120),
              );
              setTimeout(() => push(sse("citation", { v: CITATION })), (t += 140));
              for (const d of deltas) setTimeout(() => push(sse("delta", { v: d })), (t += 160));
              setTimeout(
                () => push(sse("message", { v: "", metadata: { tag: "answer_end" } })),
                (t += 140),
              );
              setTimeout(() => {
                push("data: [DONE]\n\n");
                c.close();
              }, (t += 60));
            },
          });
          return new Response(body, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          });
        }

        if (url.includes("/api/v1/ingest/freshness")) {
          return json({
            last_ingest_ts: new Date().toISOString(),
            seconds_ago: 90,
            stale: false,
            violations: [],
          });
        }
        if (url.includes("/api/v1/library/")) {
          return json({ body: "# mirror-loop\n\n본문 전체가 여기에 들어옵니다." });
        }
        if (url.includes(`/api/v1/conversations/${convId}`) && !url.includes("/messages")) {
          convGets++;
          const messages: unknown[] = [
            {
              id: "m-1",
              role: "user",
              content: "라이언이 4월에 쓴 mirror-loop 칼럼 정리해줘",
              citations: [],
              ts: new Date().toISOString(),
            },
          ];
          // Only the reload that follows [DONE] carries the persisted answer.
          if (convGets > 1) {
            messages.push({
              id: "m-2",
              role: "assistant",
              content: answer,
              citations: [CITATION],
              ts: new Date().toISOString(),
            });
          }
          return json({ conversation: { id: convId, title: "mirror-loop 칼럼" }, messages });
        }
        if (url.includes("/api/v1/")) return json({ ok: true, items: [] });
        return realFetch(input, init);
      };
    },
    { convId: CONV, answer: ANSWER, shape },
  );
}

/** Poll the rendered answer while it streams. Each sample waits for a frame
 *  first: the scroll pin runs from a ResizeObserver callback, which is
 *  delivered after layout but before paint, so reading synchronously can catch
 *  a state the reader is never shown. */
async function sampleStream(page: Page, samples: number, everyMs: number) {
  const out: {
    len: number;
    overshoot: number;
    scrollable: boolean;
    sheets: number;
    doubled: boolean;
  }[] = [];
  for (let i = 0; i < samples; i++) {
    out.push(
      await page.evaluate(
        () =>
          new Promise<{
            len: number;
            overshoot: number;
            scrollable: boolean;
            sheets: number;
            doubled: boolean;
          }>((resolve) =>
            requestAnimationFrame(() => {
              const sheets = [...document.querySelectorAll('[data-role="assistant"]')];
              const texts = sheets.map((s) =>
                (s as HTMLElement).innerText.replace(/\s+/g, " ").trim(),
              );
              const last = sheets[sheets.length - 1];
              const bottom = last ? last.getBoundingClientRect().bottom : 0;
              resolve({
                len: texts.reduce((m, t) => Math.max(m, t.length), 0),
                // What the reader actually cares about: how far the end of the
                // answer has fallen past the bottom edge of the window.
                overshoot: Math.round(bottom - window.innerHeight),
                scrollable: document.documentElement.scrollHeight > window.innerHeight,
                sheets: sheets.length,
                doubled: texts.filter((t) => t.length > 60).length > 1,
              });
            }),
          ),
      ),
    );
    await page.waitForTimeout(everyMs);
  }
  return out;
}

test.describe("streaming motion", () => {
  test("a buffered flush is revealed progressively, not painted as a block", async ({ page }) => {
    await installStreamMocks(page, "bursty");
    await page.goto(`/sediment/c/${CONV}?ask=1`);

    const samples = await sampleStream(page, 70, 50);
    const final = samples.at(-1)!.len;
    expect(final, "the whole answer must end up rendered").toBeGreaterThan(150);

    // Measure the live phase only. The tail is excluded on purpose: after
    // `answer_end` the smoother switches to a fast closing drain by design,
    // and folding that into this bound would test the opposite policy.
    const lastGrowth = samples.reduce((n, s, i) => (i > 0 && s.len > samples[i - 1].len ? i : n), 0);
    const live = samples.slice(0, Math.max(2, lastGrowth - 4));

    let maxJump = 0;
    for (let i = 1; i < live.length; i++) {
      maxJump = Math.max(maxJump, live[i].len - live[i - 1].len);
    }
    // Unpaced, the ~145-char flush lands inside one sample. The bound is
    // deliberately loose — this guards the mechanism, not a specific rate.
    expect(maxJump, `largest reveal in one 50ms sample (answer is ${final} chars)`).toBeLessThan(60);

    // ...and it genuinely animated rather than arriving in two steps.
    const growthSamples = samples.filter((s, i) => i > 0 && s.len > samples[i - 1].len).length;
    expect(growthSamples, "distinct frames in which the answer grew").toBeGreaterThan(5);
  });

  test("the viewport keeps following the answer while it streams", async ({ page }) => {
    await installStreamMocks(page, "even");
    await page.goto(`/sediment/c/${CONV}?ask=1`);

    const samples = await sampleStream(page, 70, 50);
    const growing = samples.filter((s, i) => i > 0 && s.len > samples[i - 1].len && s.scrollable);
    expect(
      growing.length,
      "the transcript must have outgrown the viewport at some point",
    ).toBeGreaterThan(0);

    // The end of the answer must stay on screen. Reacting to the streamed text
    // alone used to leave a shortfall that grew as citations and the feedback
    // row added height in commits the text did not drive; the pin observes
    // rendered height instead. Zero would be over-fitting to one frame's
    // rounding, so allow a hair — but nothing close to a line of text.
    const worst = growing.reduce((m, s) => Math.max(m, s.overshoot), 0);
    expect(worst, "px the answer's last line fell below the window").toBeLessThan(8);
  });

  test("the persisted row replaces the live bubble without blanking or doubling", async ({
    page,
  }) => {
    await installStreamMocks(page, "even");
    await page.goto(`/sediment/c/${CONV}?ask=1`);

    // 25ms sampling — the old flash was a single frame.
    const samples = await sampleStream(page, 140, 25);
    const firstText = samples.findIndex((s) => s.len > 60);
    expect(firstText, "the answer must have rendered at all").toBeGreaterThan(-1);

    const settled = samples.findIndex((s) => s.sheets > 0 && s.len > 60);
    const window_ = samples.slice(firstText, settled > 0 ? undefined : samples.length);
    expect(window_.filter((s) => s.len === 0), "frames where the answer vanished").toHaveLength(0);
    expect(samples.filter((s) => s.doubled), "frames showing the answer twice").toHaveLength(0);

    await expect(page.getByRole("button", { name: /helpful/i }).first()).toBeVisible();
  });

  test("waiting for the first token is legible as waiting", async ({ page }) => {
    await installStreamMocks(page, "even");
    await page.goto(`/sediment/c/${CONV}?ask=1`);

    // Scoped to the transcript: the Evidence sidebar's status line also says
    // "thinking…" at this moment. E2E-04 in validator/e2e_spec.yaml waits on
    // this word too, so it has to stay in the DOM.
    await expect(
      page.locator('[data-role="assistant"]').filter({ hasText: "thinking" }),
    ).toBeVisible();
    expect(await page.locator(".think-dot").count()).toBeGreaterThan(0);
  });
});

test.describe("citation modal", () => {
  test("enters, covers the viewport, and animates out before unmounting", async ({ page }) => {
    await installStreamMocks(page, "even");
    await page.goto(`/sediment/c/${CONV}?ask=1`);

    await page.locator("aside button", { hasText: "open" }).first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    const state = await dialog.evaluate((panel) => {
      const overlay = panel.parentElement!;
      const r = overlay.getBoundingClientRect();
      return {
        anim: getComputedStyle(panel).animationName,
        // The Evidence <aside> carries a page-entrance transform, and a
        // transformed element becomes the containing block for `position:
        // fixed` descendants — un-portalled, this scrim covered the sidebar
        // column only.
        portalled: overlay.parentElement === document.body,
        coversViewport:
          r.width >= window.innerWidth - 1 && r.height >= window.innerHeight - 1,
      };
    });
    expect(state.anim).toBe("panel-rise");
    expect(state.portalled, "modal must be portalled out of the transformed aside").toBe(true);
    expect(state.coversViewport, "scrim must cover the whole viewport").toBe(true);

    await page.getByRole("button", { name: "Close" }).click();
    // Still mounted, now playing the exit — this is the whole reason `closing`
    // state exists rather than an immediate unmount.
    await expect
      .poll(async () =>
        page
          .getByRole("dialog")
          .evaluate((el) => getComputedStyle(el).animationDirection)
          .catch(() => "gone"),
      )
      .toBe("reverse");
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });
});

test.describe("prefers-reduced-motion", () => {
  test("nothing that animates in is left invisible", async ({ page }) => {
    // emulateMedia rather than `test.use({ reducedMotion })`: the option is
    // valid at runtime but is not on the `test.use` fixture type in this
    // Playwright version, and a spec that fails `tsc --noEmit` is a spec
    // people start skipping.
    await page.emulateMedia({ reducedMotion: "reduce" });
    await installStreamMocks(page, "even");
    await page.goto(`/sediment/c/${CONV}?ask=1`);
    await page.waitForTimeout(2500);

    // Every reveal starts at opacity 0. If the reduce path ever stops landing
    // on the end state, the UI goes blank for exactly the users who asked for
    // less motion — and no other assertion in this suite would notice.
    const state = await page.evaluate(() => {
      const animated = [...document.querySelectorAll(".enter-item, .reveal-item, .enter-fade")];
      return {
        total: animated.length,
        invisible: animated
          .filter((el) => parseFloat(getComputedStyle(el).opacity) < 0.9)
          .map((el) => el.className),
      };
    });
    expect(state.total).toBeGreaterThan(0);
    expect(state.invisible).toHaveLength(0);
    await expect(page.getByRole("button", { name: /helpful/i }).first()).toBeVisible();
  });
});
