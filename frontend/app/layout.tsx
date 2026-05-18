import type { ReactNode } from "react";
import "./globals.css";

export const metadata = {
  title: "Sediment — HypeProof Lab",
  description:
    "Where doing becomes knowing. Evidence-grounded memory for HypeProof Lab — every answer comes with citations.",
};

// Root layout (required by the App Router). The visible chrome (header/nav)
// lives in the nested app/sediment/layout.tsx, kept verbatim from the
// community app so the 11 UI files needed zero changes.
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
