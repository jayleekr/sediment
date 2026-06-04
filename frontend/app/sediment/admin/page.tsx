"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { SectionHeader, Surface } from "../components/ui";

export default function AdminPage() {
  const [tenants, setTenants] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ items: any[] }>("/api/v1/admin/tenants")
      .then((d) => setTenants(d.items))
      .catch((e) => setError(e.message));
  }, []);

  if (error) {
    return (
      <div className="rounded-xl border bg-yellow-50 p-4 text-sm">
        <strong>admin only.</strong> {error}
      </div>
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
            <thead className="text-left text-xs text-neutral-500">
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
      <p className="text-xs text-neutral-500">
        Phase 6+ adds: per-tenant detail, integrations, billing, audit log drill-down.
      </p>
    </div>
  );
}
