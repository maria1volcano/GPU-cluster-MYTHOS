// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - tanstackStart, viteReact, tailwindcss, tsConfigPaths, nitro (build-only using cloudflare as a default target),
//     componentTagger (dev-only), VITE_* env injection, @ path alias, React/TanStack dedupe,
//     error logger plugins, and sandbox detection (port/host/strictPort).
// You can pass additional config via defineConfig({ vite: { ... }, etc... }) if needed.
import type { Plugin } from "vite";
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

const R3F_FILE = /RackMap(Scene|3D)\.tsx$/;
const R3F_CODE = /@react-three\/(fiber|drei)|<(mesh|group|ambientLight|directionalLight|pointLight)\b/;

/** TanStack devtools injects data-tsd-source on JSX; R3F forwards it to THREE objects and crashes. */
function stripTsdSourceFromR3f(): Plugin {
  return {
    name: "strip-tsd-source-r3f",
    enforce: "post",
    transform(code, id) {
      const path = id.split("?")[0] ?? id;
      if (!R3F_FILE.test(path) && !R3F_CODE.test(code)) return;
      if (!code.includes("data-tsd-source")) return;
      const next = code.replace(/\sdata-tsd-source="[^"]*"/g, "");
      if (next === code) return;
      return { code: next, map: null };
    },
  };
}

// Leave VITE_API_BASE_URL empty in dev to route /api/* through the Vite proxy (vite.config.ts).
export default defineConfig({
  plugins: [stripTsdSourceFromR3f()],
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
