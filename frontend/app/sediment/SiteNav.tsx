"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Masthead navigation. The current section is marked like a running head in a
// journal — oxblood label + a rule under it — so you always know which part of
// the archive you're reading.
const ITEMS: { href: string; label: string }[] = [
  { href: "/sediment", label: "Chat" },
  { href: "/sediment/library", label: "Library" },
  { href: "/sediment/members", label: "Members" },
  { href: "/sediment/admin", label: "Admin" },
];

function isActive(pathname: string | null, href: string): boolean {
  if (!pathname) return false;
  if (href === "/sediment") {
    // Chat is the root + every conversation thread under /sediment/c/*.
    return pathname === "/sediment" || pathname.startsWith("/sediment/c");
  }
  return pathname === href || pathname.startsWith(href + "/");
}

export default function SiteNav() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Sediment sections"
      className="flex flex-wrap items-baseline gap-x-6 gap-y-1"
    >
      {ITEMS.map((it) => {
        const active = isActive(pathname, it.href);
        return (
          <Link
            key={it.href}
            href={it.href}
            aria-current={active ? "page" : undefined}
            className={`group relative pb-0.5 text-[15px] transition-colors ${
              active
                ? "text-accent"
                : "text-ink-2 hover:text-ink"
            }`}
          >
            {it.label}
            <span
              className={`absolute -bottom-px left-0 h-px w-full origin-left scale-x-0 bg-accent transition-transform duration-200 group-hover:scale-x-100 ${
                active ? "scale-x-100" : ""
              }`}
            />
          </Link>
        );
      })}
    </nav>
  );
}
