import { expect, test, type Page } from "@playwright/test";

const CONV_ID = "11111111-1111-4111-8111-111111111111";
const QUERY = "라이언이 4월에 쓴 mirror-loop 칼럼";

type MockOptions = {
  mode: "answer" | "error";
};

async function installSedimentMocks(page: Page, opts: MockOptions) {
  let assistantPersisted = false;

  await page.route("**/api/auth/session", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ user: null, expires: new Date(Date.now() + 3600_000).toISOString() }),
    })
  );

  await page.route("**/api/v1/vault/freshness", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ last_ingest_ts: new Date().toISOString(), seconds_ago: 120, stale: false }),
    })
  );

  await page.route(`**/api/v1/conversations/${CONV_ID}`, (route) => {
    const messages = [
      {
        id: "user-1",
        role: "user",
        content: QUERY,
        citations: [],
        ts: "2026-06-05T00:00:00.000Z",
      },
    ];
    if (assistantPersisted) {
      messages.push({
        id: "assistant-1",
        role: "assistant",
        content: "제공된 인용 자료에는 mirror-loop 관련 칼럼 정보가 없습니다. [1]",
        citations: [
          {
            ref: "web/src/content/columns/en/2026-02-26-calculus-of-orchestration.md",
            type: "column",
            date: "2026-02-26",
            content: "feedback control loop evidence",
          },
          {
            ref: "web/src/content/columns/en/2026-03-27-automotive-unbundling.md",
            type: "column",
            date: "2026-03-27",
            content: "analysis loop evidence",
          },
        ],
        ts: "2026-06-05T00:00:02.000Z",
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conversation: {
          id: CONV_ID,
          user_id: "member-1",
          title: QUERY,
          created_at: "2026-06-05T00:00:00.000Z",
          updated_at: "2026-06-05T00:00:02.000Z",
        },
        messages,
      }),
    });
  });

  await page.route("**/api/v1/library/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ body: "source body" }),
    })
  );

  await page.route("**/v1/sediment/stream", (route) => {
    if (opts.mode === "error") {
      assistantPersisted = false;
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: [
          'event: citation\ndata: {"v":{"ref":"web/src/content/columns/en/2026-02-26-calculus-of-orchestration.md","type":"column","date":"2026-02-26"}}',
          'event: message\ndata: {"v":"Answer generation failed because the configured LLM provider quota is exhausted.","metadata":{"tag":"error"}}',
          "data: [DONE]",
          "",
        ].join("\n\n"),
      });
    }

    assistantPersisted = true;
    return route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: [
        'event: citation\ndata: {"v":{"ref":"web/src/content/columns/en/2026-02-26-calculus-of-orchestration.md","type":"column","date":"2026-02-26","content":"feedback control loop evidence"}}',
        'event: citation\ndata: {"v":{"ref":"web/src/content/columns/en/2026-03-27-automotive-unbundling.md","type":"column","date":"2026-03-27","content":"analysis loop evidence"}}',
        'event: delta\ndata: {"v":"제공된 인용 자료에는 mirror-loop 관련 칼럼 정보가 없습니다. [1]"}',
        'event: message\ndata: {"v":"","metadata":{"tag":"answer_end"}}',
        "data: [DONE]",
        "",
      ].join("\n\n"),
    });
  });
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("curator.token", "test-token");
  });
});

test("deep-linked auto-ask renders and persists the assistant answer", async ({ page }) => {
  await installSedimentMocks(page, { mode: "answer" });

  await page.goto(`/sediment/c/${CONV_ID}?ask=1`);

  await expect(page.locator("[data-role='assistant']")).toContainText("mirror-loop");
  await expect(page.locator("body")).toContainText("2 citations");
  await expect(page.getByText("2026-02-26-calculus-of-orchestration.md")).toBeVisible();
  await expect(page.locator("div[role='alert']").filter({ hasText: "Answer generation failed" })).toHaveCount(0);

  await page.goto(`/sediment/c/${CONV_ID}`);
  await expect(page.locator("[data-role='assistant']")).toContainText("mirror-loop");
  await expect(page.locator("body")).toContainText("2 citations");
});

test("stream errors stay visible after DONE", async ({ page }) => {
  await installSedimentMocks(page, { mode: "error" });

  await page.goto(`/sediment/c/${CONV_ID}?ask=1`);

  const error = page.locator("div[role='alert']").filter({ hasText: "Answer generation failed" });
  await expect(error).toContainText("Answer generation failed");
  await expect(error).toContainText("quota is exhausted");
  await expect(page.locator("body")).toContainText("1 citation");
  await expect(page.locator("body")).toContainText("status: failed");
});
