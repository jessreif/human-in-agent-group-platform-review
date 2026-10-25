import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

export default function globalSetup() {
  if (process.env.INVESTIGATORS_E2E !== "1") return Promise.resolve();
  return new Promise((resolve, reject) => {
    const child = spawn("bash", [path.join(here, "stack.sh"), "start"], {
      cwd: path.join(here, ".."),
      stdio: "inherit",
    });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`e2e stack exited ${code}`));
    });
  });
}
