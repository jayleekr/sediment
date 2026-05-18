"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";

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
      <div className="rounded-xl border bg-white p-4 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold">Tenants</h3>
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-neutral-500">
            <tr>
              <th>slug</th>
              <th>plan</th>
              <th>status</th>
              <th>members</th>
              <th>artifacts</th>
              <th>seats</th>
              <th>quota/mo</th>
              <th>created</th>
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
      <p className="text-xs text-neutral-500">
        Phase 6+ adds: per-tenant detail, integrations, billing, audit log drill-down.
      </p>
    </div>
  );
}
