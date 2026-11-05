// Bundles the plugin, the Gate core and its one dependency into dist/index.js.
// OpenClaw installs a plugin archive without installing its dependencies, so
// the archive carries everything it runs.
import { build } from "esbuild";

await build({
  entryPoints: ["src/index.ts"],
  outfile: "dist/index.js",
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node20",
  legalComments: "eof",
  logLevel: "warning",
});
