// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - tanstackStart, viteReact, tailwindcss, tsConfigPaths, nitro (build-only using cloudflare as a default target),
//     componentTagger (dev-only), VITE_* env injection, @ path alias, React/TanStack dedupe,
//     error logger plugins, and sandbox detection (port/host/strictPort).
// You can pass additional config via defineConfig({ vite: { ... }, etc... }) if needed.
import type { Plugin, TransformResult } from "vite";
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

const R3F_FILES = /RackMap(Scene|3D)\.tsx$/;
const R3F_CODE =
  /@react-three\/(fiber|drei)|<(mesh|group|ambientLight|directionalLight|pointLight|gridHelper)\b/;

function stripTsdSource(code: string): string {
  return code.replace(/\sdata-tsd-source="[^"]*"/g, "");
}

/** Skip inject + strip leftovers — TanStack devtools + R3F are incompatible on lowercase Three tags. */
function shieldR3fFromDevtools(): Plugin {
  return {
    name: "shield-r3f-from-devtools",
    enforce: "post",
    transform(code, id): TransformResult | undefined {
      const path = id.split("?")[0] ?? id;
      if (!R3F_FILES.test(path) && !R3F_CODE.test(code)) return;
      if (!code.includes("data-tsd-source")) return;
      const next = stripTsdSource(code);
      if (next === code) return;
      return { code: next, map: null };
    },
    configResolved(resolved) {
      const inject = resolved.plugins.find((p) => p.name === "@tanstack/devtools:inject-source");
      if (!inject?.transform) return;

      const transform = inject.transform;
      const handler =
        typeof transform === "function"
          ? transform
          : transform && "handler" in transform
            ? transform.handler
            : null;
      if (!handler) return;

      const wrapped = function (this: unknown, code: string, id: string) {
        const path = id.split("?")[0] ?? id;
        if (R3F_FILES.test(path)) return null;
        const result = (handler as (code: string, id: string) => TransformResult | string | null | undefined).call(
          this,
          code,
          id,
        );
        if (!result) return result;
        const out = typeof result === "string" ? result : result.code;
        if (!out?.includes("data-tsd-source")) return result;
        const stripped = stripTsdSource(out);
        if (stripped === out) return result;
        return typeof result === "string"
          ? stripped
          : { ...result, code: stripped, map: null };
      };

      if (typeof transform === "function") {
        inject.transform = wrapped;
      } else if (transform && "handler" in transform) {
        transform.handler = wrapped;
      }
    },
  };
}

// Leave VITE_API_BASE_URL empty in dev to route /api/* through the Vite proxy (vite.config.ts).
export default defineConfig({
  plugins: [shieldR3fFromDevtools()],
  tanstackStart: {
    // Redirect TanStack Start's bundled server entry to src/server.ts (our SSR error wrapper).
    // nitro/vite builds from this
    server: { entry: "server" },
  },
  vite: {
    server: {
      proxy: {
        "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
        "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
        "/stream": { target: "ws://127.0.0.1:8000", ws: true },
      },
    },
  },
});
