"use client";

import type { ReactNode } from "react";
import { SessionProvider } from "next-auth/react";
import AuthBridge from "./AuthBridge";

export default function Providers({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <AuthBridge />
      {children}
    </SessionProvider>
  );
}
