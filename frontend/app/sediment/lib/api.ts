/* Sediment (codename: curator) — client API helpers
 *
 * Local dev: store JWT in localStorage under "curator.token".
 * Production (Phase 5): NextAuth.js session token.
 */

export const PLATFORM_BASE =
  (process.env.NEXT_PUBLIC_CURATOR_PLATFORM_URL as string) || "http://localhost:10100";
export const LANGGRAPH_BASE =
  (process.env.NEXT_PUBLIC_CURATOR_LANGGRAPH_URL as string) || "http://localhost:10020";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("curator.token");
}

export function setToken(t: string) {
  window.localStorage.setItem("curator.token", t);
}

export function clearToken() {
  if (typeof window !== "undefined") window.localStorage.removeItem("curator.token");
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
  base: string = PLATFORM_BASE
): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${base}${path}`, { ...init, headers });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    // Expired / invalid token → wipe it so the next render shows the sign-in form
    if (res.status === 401) clearToken();
    throw new ApiError(res.status, `${res.status} ${res.statusText}: ${txt}`);
  }
  return res.json() as Promise<T>;
}

/* Mint a dev token (local only) */
export async function mintDevToken(email: string): Promise<{ token: string; display_name: string }> {
  const res = await fetch(`${PLATFORM_BASE}/api/v1/auth/dev-token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  setToken(data.token);
  return data;
}

export type Conversation = {
  id: string;
  user_id: string | null;
  title: string | null;
  created_at: string;
  updated_at: string;
};

export type Message = {
  id: string;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  citations: any[];
  ts: string;
};

export type Citation = {
  ref?: string;
  type?: string;
  date?: string;
  slug?: string;
  score?: number;
  content?: string;
  display_name?: string;
};

export type LibraryItem = {
  id: string;
  ref: string;
  type: string;
  date: string | null;
  slug: string | null;
  lang: string | null;
  frontmatter: Record<string, any>;
  author_name: string | null;
  author_external_id: string | null;
};
