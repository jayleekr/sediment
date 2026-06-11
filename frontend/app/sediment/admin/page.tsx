"use client";

import { useEffect, useState } from "react";
import { signIn as githubSignIn } from "next-auth/react";
import { ApiError, api, clearToken, getToken } from "../lib/api";
import { SectionHeader, Surface } from "../components/ui";

export default function AdminPage() {
  const [tenants, setTenants] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [authState, setAuthState] = useState<"checking" | "signed-out" | "forbidden" | "ready">("checking");

  useEffect(() => {
    if (!getToken()) {
      setAuthState("signed-out");
      return;
    }
    api<{ items: any[] }>("/api/v1/admin/tenants")
      .then((d) => {
        setTenants(d.items);
        setAuthState("ready");
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 401) {
          clearToken();
          setAuthState("signed-out");
          setError("Session expired or missing. Sign in again with a Sediment admin GitHub account.");
          return;
        }
        if (e instanceof ApiError && e.status === 403) {
          setAuthState("forbidden");
          setError("This Sediment member is not an admin for the current tenant.");
          return;
        }
        setAuthState("forbidden");
        setError(e instanceof Error ? e.message : String(e));
      });
  }, []);

  if (authState === "checking") {
    return (
      <Surface className="p-4">
        <SectionHeader title="Admin" description="Checking your Sediment admin session." />
      </Surface>
    );
  }

  if (authState === "signed-out") {
    return (
      <Surface className="mx-auto max-w-md p-6">
        <h2 className="mb-2 text-lg font-semibold">Admin sign in</h2>
        <p className="mb-4 text-sm text-ink-2">
          Sign in with a GitHub account mapped to a Sediment admin member.
        </p>
        <button
          onClick={() => githubSignIn("github", { callbackUrl: "/sediment/admin" })}
          className="flex w-full items-center justify-center rounded-md bg-ink px-4 py-2 text-paper transition-colors hover:bg-accent-ink"
        >
          Sign in with GitHub
        </button>
        {error && <p className="mt-3 text-sm text-ochre">{error}</p>}
      </Surface>
    );
  }

  if (error) {
    return (
      <Surface className="p-4">
        <SectionHeader title="Admin only" description={error} />
      </Surface>
    );
  }

  return (
    <div className="space-y-6">
      <Surface className="p-4">
        <SectionHeader
          title="Tenants"
          description="Operational snapshot for tenant access, usage posture, and rollout readiness."
        />
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <caption className="sr-only">Tenant administration overview</caption>
            <thead className="text-left text-xs text-ink-3">
              <tr>
                <th scope="col">slug</th>
                <th scope="col">plan</th>
                <th scope="col">status</th>
                <th scope="col">members</th>
                <th scope="col">artifacts</th>
                <th scope="col">seats</th>
                <th scope="col">quota/mo</th>
                <th scope="col">created</th>
              </tr>
            </thead>
            <tbody>
              {tenants.map((t) => (
                <tr key={t.id} className="border-t">
                  <td className="font-mono">{t.slug}</td>
                  <td>{t.plan}</td>
                  <td>{t.status}</td>
                  <td>{t.member_count}</td>
                  <td>{t.artifact_count}</td>
                  <td>{t.seat_count}</td>
                  <td>{t.query_quota_per_month}</td>
                  <td>{new Date(t.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Surface>
      <p className="text-xs text-ink-3">
        Phase 6+ adds: per-tenant detail, integrations, billing, audit log drill-down.
      </p>
    </div>
  );
}
