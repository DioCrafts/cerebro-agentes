// The root of the design system: Primer's theme context and base styles around the whole app.
//
// The colour mode itself is on <html> (src/lib/theme.ts), so Primer's overlays, which render in portals,
// get the app's colours too. Here Primer is only told which mode is on.

import { BaseStyles } from "@primer/react";
import { ThemeProvider } from "@primer/react/next";
import type { ReactNode } from "react";

import { useTheme } from "../lib/theme";

export function ProveedorUI({ children }: { children: ReactNode }) {
  const [tema] = useTheme();
  return (
    <ThemeProvider colorMode={tema === "dark" ? "night" : "day"} dayScheme="light" nightScheme="dark">
      <BaseStyles>{children}</BaseStyles>
    </ThemeProvider>
  );
}
