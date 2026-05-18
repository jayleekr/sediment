"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";

type Member = {
  id: string;
  external_id: string | null;
  email: string;
  display_name: string;
  real_name: string | null;
  role: string;
  title: string | null;
  expertise: string[];
  interests: string[];
};

export default function MembersPage() {
  const [items, setItems] = useState<Member[]>([]);

  useEffect(() => {
    api<{ items: Member[] }>("/api/v1/members").then((d) => setItems(d.items));
  }, []);

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      {items.map((m) => (
        <div key={m.id} className="rounded-xl border bg-white p-4 shadow-sm">
          <div className="flex items-baseline justify-between">
            <h3 className="text-lg font-semibold">{m.display_name}</h3>
            <span className="text-xs text-neutral-500">{m.role}</span>
          </div>
          <p className="text-sm text-neutral-600">{m.real_name}</p>
          <p className="mt-1 text-xs">{m.title}</p>
          <div className="mt-3 flex flex-wrap gap-1">
            {m.expertise?.map((e) => (
              <span key={e} className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-700">
                {e}
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
