import { useEffect, useState, type ReactNode } from "react";

/** Render children only after mount — keeps WebGL / browser-only APIs off the SSR path. */
export function ClientOnly({
  children,
  fallback = null,
}: {
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return fallback;
  return children;
}
