#!/usr/bin/env node
/**
 * Render a session-replay payload to the standalone simulation HTML.
 *
 * The replay JSON is the same shape for both callers:
 *   - the admin walkthrough (`GET /admin/sessions/{id}/replay`), and
 *   - a harness run's `replays/<sid>.json` artifact (`harness.cli --simulate`).
 *
 * This reuses the exact `buildSimulationHtml` the download button runs —
 * transformed from TypeScript via vite's `transformWithOxc` — so a bot session
 * and a live session render through identical code, and edits to the player
 * never fork.
 *
 * Usage:
 *   node frontend/scripts/render_simulation.mjs <replay.json> [-o out.html]
 */
import { readFileSync, writeFileSync } from "node:fs";
import { basename } from "node:path";
import { transformWithOxc } from "vite";

const args = process.argv.slice(2);
if (args.length < 1) {
  console.error(
    `usage: node ${basename(process.argv[1])} <replay.json> [-o out.html]`,
  );
  process.exit(2);
}
const src = args[0];
const oi = args.indexOf("-o");
const out =
  oi >= 0 ? args[oi + 1] : src.replace(/\.json$/, "") + ".simulation.html";

const ts = readFileSync(
  new URL("../src/admin/simulation.ts", import.meta.url),
  "utf8",
);
const { code } = await transformWithOxc(ts, "simulation.ts", {
  lang: "ts",
  sourceType: "module",
});
const { buildSimulationHtml } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
);

const replay = JSON.parse(readFileSync(src, "utf8"));
writeFileSync(out, buildSimulationHtml(replay));
console.log(`wrote ${out}`);
