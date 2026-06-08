import type { ReactNode } from "react";
import "./globals.css";

export const metadata = {
  title: "Sediment — HypeProof Lab",
  description:
    "Where doing becomes knowing. Evidence-grounded memory for HypeProof Lab — every answer comes with citations.",
};

// Root layout (required by the App Router). The visible chrome (header/nav)
// lives in the nested app/sediment/layout.tsx. Editorial-Archive type system
// is loaded here via <link> (runtime fetch by the browser) so the build never
// needs network — Fraunces (display), Newsreader (reading body), IBM Plex
// Mono (refs/ledger metadata). Families are wired to tokens in globals.css.
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,500;0,9..144,600;0,9..144,700;1,9..144,400;1,9..144,500&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400;1,6..72,500&family=IBM+Plex+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
