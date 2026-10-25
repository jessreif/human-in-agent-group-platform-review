import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

export default function globalTeardown() {
  if (process.env.INVESTIGATORS_E2E !== "1") return Promise.resolve();
  return new Promise((resolve) => {
    const child = spawn("bash", [path.join(here, "stack.sh"), "stop"], {
      cwd: path.join(here, ".."),
      stdio: "inherit",
    });
    child.on("exit", () => resolve());
    child.on("error", () => resolve());
  });
}
