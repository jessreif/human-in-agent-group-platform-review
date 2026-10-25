import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));

const COMPREHENSION = [
  ["Every team member receives some, but not all, available intelligence.", "True"],
  ["Every team member is working on the exact same crates.", "False"],
  ["The intelligence in my case file is accurate, but may not be the latest available.", "True"],
  ["My teammates may have intelligence about my assigned crates.", "True"],
  // U+2019, matching the bound comprehension item.
  ["The AI assistant has access to my teammates\u2019 case files.", "False"],
];

function takeEntryPath() {
  const lines = fs.readFileSync(path.join(here, ".entries"), "utf8").trim().split("\n");
  const indexPath = path.join(here, ".entry-index");
  const next = fs.existsSync(indexPath) ? Number(fs.readFileSync(indexPath, "utf8")) : 0;
  if (!Number.isInteger(next) || next < 0 || next >= lines.length) {
    throw new Error("no preview entry path left");
  }
  fs.writeFileSync(indexPath, String(next + 1));
  return lines[next];
}

async function heading(page) {
  return page.locator("h1").first().textContent().catch(() => "");
}

/** Preview link, consent, instructions, practice, comprehension, connecting, task. */
export async function reachTask(page) {
  await page.goto(takeEntryPath());
  const consent = page.getByRole("button", { name: /consent to participate/i });
  await expect(consent, await heading(page)).toBeVisible({ timeout: 20_000 });
  await consent.click();

  await page.locator("#initials").fill("AB");
  await page.getByRole("button", { name: "Continue to your workspace" }).click();
  await page.getByRole("button", { name: "Continue", exact: true }).click();

  await page.getByRole("button", { name: "Share", exact: true }).first().click();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "Continue to your team" }).click();
  await expect(page.getByRole("heading", { name: "Comprehension check" })).toBeVisible();

  for (const [legend, value] of COMPREHENSION) {
    await page.getByRole("group", { name: legend }).getByRole("radio", { name: value }).check();
  }
  await page.getByRole("button", { name: "Continue to your team" }).click();

  const start = page.getByRole("button", { name: "Start", exact: true });
  await expect(start).toBeEnabled({ timeout: 25_000 });
  await start.click();
  await expect(page.locator("[data-stage='task']")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("[data-pane='case-file'] li.file").first()).toBeVisible();
}
