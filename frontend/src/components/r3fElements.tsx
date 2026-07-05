import { createElement, type ReactNode } from "react";

/**
 * Build Three/R3F primitives without JSX so TanStack devtools never injects
 * `data-tsd-source` (R3F treats dashed props as pierced paths and crashes).
 */
export function R3F(
  type: string,
  props: Record<string, unknown> | null | undefined,
  ...children: ReactNode[]
) {
  const safe = props ? { ...props } : {};
  delete safe["data-tsd-source"];
  return createElement(type, safe, ...children);
}
