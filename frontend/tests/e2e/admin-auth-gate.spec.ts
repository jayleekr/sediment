import { expect, test, type Page } from "@playwright/test";

const RAW_BACKEND_ERROR = "Traceback: psycopg2.OperationalError: connection refused";

async function installBaseMocks(page: Page) {
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
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("curator.token", "test-token");
  });
  await installBaseMocks(page);
});

test("non-admin member (403) sees the Admin only card", async ({ page }) => {
  await page.route("**/api/v1/admin/tenants", (route) =>
    route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ detail: "member is not admin" }),
    })
  );

  await page.goto("/sediment/admin");

  await expect(page.getByText("Admin only")).toBeVisible();
  await expect(
    page.getByText("This Sediment member is not an admin for the current tenant.")
  ).toBeVisible();
  await expect(page.locator("table")).toHaveCount(0);
});

test("backend failure (500) shows neutral error card with Retry, never raw error text", async ({
  page,
}) => {
  let calls = 0;
  await page.route("**/api/v1/admin/tenants", (route) => {
    calls += 1;
    if (calls === 1) {
      return route.fulfill({
        status: 500,
        contentType: "text/plain",
        body: RAW_BACKEND_ERROR,
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            id: "tenant-1",
            slug: "hypeproof",
            plan: "free",
            status: "active",
            member_count: 3,
            artifact_count: 42,
            seat_count: 5,
            query_quota_per_month: 1000,
            created_at: "2026-05-18T00:00:00.000Z",
          },
        ],
      }),
    });
  });

  await page.goto("/sediment/admin");

  await expect(page.getByText("Something went wrong")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("psycopg2");
  await expect(page.locator("body")).not.toContainText(RAW_BACKEND_ERROR);
  await expect(page.getByText("Admin only")).toHaveCount(0);

  await page.getByRole("button", { name: "Retry" }).click();

  await expect(page.getByText("Tenants")).toBeVisible();
  await expect(page.locator("table")).toContainText("hypeproof");
});
