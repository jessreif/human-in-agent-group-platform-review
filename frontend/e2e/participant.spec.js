import { execFile } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { reachTask } from "./reach_task.js";

const here = path.dirname(fileURLToPath(import.meta.url));

function telemetryLines() {
  return new Promise((resolve, reject) => {
    execFile(
      "bash",
      [path.join(here, "stack.sh"), "telemetry"],
      (err, stdout, stderr) => {
        if (err) reject(new Error((stderr || err.message).slice(0, 400)));
        else resolve(stdout.trim().split("\n").filter(Boolean));
      },
    );
  });
}

function cutStream() {
  const text = fs.readFileSync(path.join(here, ".stack-state"), "utf8");
  const line = text.split("\n").find((row) => row.startsWith("proxy_control="));
  if (!line) throw new Error("proxy control port is not recorded");
  const port = Number(line.slice("proxy_control=".length));
  return new Promise((resolve, reject) => {
    const sock = net.connect({ host: "127.0.0.1", port }, () => sock.end());
    sock.on("error", reject);
    sock.on("close", () => resolve());
  });
}

const SSE_PROBE = () => {
  const Orig = window.EventSource;
  if (!Orig) return;
  const counts = { constructed: 0, opens: 0, messages: 0, heads: 0, errors: 0 };
  window.__invSse = counts;
  function Wrapped(url, init) {
    const es = new Orig(url, init);
    counts.constructed += 1;
    es.addEventListener("open", () => {
      counts.opens += 1;
    });
    es.addEventListener("message", () => {
      counts.messages += 1;
    });
    es.addEventListener("head", () => {
      counts.heads += 1;
    });
    es.addEventListener("error", () => {
      counts.errors += 1;
    });
    return es;
  }
  Wrapped.prototype = Orig.prototype;
  Wrapped.CONNECTING = Orig.CONNECTING;
  Wrapped.OPEN = Orig.OPEN;
  Wrapped.CLOSED = Orig.CLOSED;
  window.EventSource = Wrapped;
};

async function cardRatios(page) {
  return page.evaluate(() => {
    const cards = [...document.querySelectorAll("[data-pane='case-file'] li.file")];
    function ratio(el) {
      const rect = el.getBoundingClientRect();
      let top = rect.top;
      let bottom = rect.bottom;
      let left = rect.left;
      let right = rect.right;
      let node = el.parentElement;
      while (node) {
        const style = getComputedStyle(node);
        if (/(auto|scroll|hidden)/.test(style.overflowY + style.overflowX)) {
          const box = node.getBoundingClientRect();
          top = Math.max(top, box.top);
          bottom = Math.min(bottom, box.bottom);
          left = Math.max(left, box.left);
          right = Math.min(right, box.right);
        }
        node = node.parentElement;
      }
      top = Math.max(top, 0);
      left = Math.max(left, 0);
      bottom = Math.min(bottom, window.innerHeight);
      right = Math.min(right, window.innerWidth);
      const area = rect.width * rect.height;
      if (area <= 0) return 0;
      return (Math.max(0, bottom - top) * Math.max(0, right - left)) / area;
    }
    return cards.map((el) => ({ id: el.dataset.recordId || "", ratio: ratio(el) }));
  });
}

async function assertRealQualification(page) {
  // Real scrollport: the list on a desktop width, the task grid once the
  // layout stacks at ≤900 px and the list itself no longer scrolls.
  await page.locator("[data-pane='case-file'] .files-list").evaluate((el) => {
    const grid = el.closest(".task-grid");
    const scroller = el.scrollHeight > el.clientHeight + 1 ? el : grid;
    if (scroller) {
      scroller.scrollTop = Math.min(320, scroller.scrollHeight);
      scroller.scrollTop = 0;
    }
  });
  const placed = await cardRatios(page);
  const onScreen = placed.filter((card) => card.ratio >= 0.6).sort((a, b) => b.ratio - a.ratio)[0];
  const offScreen = placed.filter((card) => card.ratio < 0.6).sort((a, b) => a.ratio - b.ratio)[0];
  expect(onScreen, "a card should be at least 60% on screen").toBeTruthy();
  expect(offScreen, "a card should be off the real scrollport").toBeTruthy();
  await page.waitForTimeout(450);
  await expect
    .poll(async () => (await telemetryLines()).join("\n"), { timeout: 8_000 })
    .toContain(`telemetry_file_record_view ${onScreen.id} `);
  const lines = await telemetryLines();
  expect(
    lines.some((line) => line.startsWith(`telemetry_file_record_view ${offScreen.id} `)),
    lines.filter((line) => line.startsWith("telemetry_file_record_view")).join(" | "),
  ).toBe(false);
}

test("preview entry reaches the task and can close", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await reachTask(page);
  await page.close();
});

test("the event stream delivers a frame, survives a killed connection, and reconnects or polls", async ({
  page,
}) => {
  await page.addInitScript(SSE_PROBE);
  await page.setViewportSize({ width: 1280, height: 800 });
  await reachTask(page);
  await expect
    .poll(() => page.evaluate(() => window.__invSse.messages + window.__invSse.heads))
    .toBeGreaterThan(0);
  const errorsBefore = await page.evaluate(() => window.__invSse.errors);
  const opensBefore = await page.evaluate(() => window.__invSse.opens);
  await cutStream();
  await expect
    .poll(() => page.evaluate(() => window.__invSse.errors), { timeout: 10_000 })
    .toBeGreaterThan(errorsBefore);
  await expect(page.locator("[data-connection]")).toBeVisible();
  await expect
    .poll(
      async () => {
        const opens = await page.evaluate(() => window.__invSse.opens);
        const mode = (await page.locator("[data-connection]").count())
          ? await page.locator("[data-connection]").first().getAttribute("data-connection")
          : "live";
        return opens > opensBefore || mode === "polling";
      },
      { timeout: 10_000 },
    )
    .toBe(true);
});

test("a card on screen qualifies after the real 300 ms and a card off screen does not", async ({
  browser,
}) => {
  for (const viewport of [
    { width: 1280, height: 640 },
    { width: 880, height: 700 },
  ]) {
    const context = await browser.newContext({
      viewport,
      baseURL: "http://127.0.0.1:4180",
    });
    const page = await context.newPage();
    await reachTask(page);
    await assertRealQualification(page);
    await context.close();
  }
});

test("keyboard reorder works, a drag share is recorded, and closing delivers telemetry", async ({
  page,
}) => {
  // Chromium does not send a keepalive fetch that pagehide issued as the
  // native function until the document is discarded, and closing the page
  // discards that beacon before it is flushed. Replacing fetch with a
  // function that calls the native one makes the same request leave while
  // the document is still open. Headers and body are unchanged.
  await page.addInitScript(() => {
    const orig = window.fetch.bind(window);
    window.fetch = (input, init) => orig(input, init);
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await reachTask(page);

  const list = page.locator("[data-pane='case-file'] li.file");
  const first = await list.nth(0).getAttribute("data-record-id");
  const second = await list.nth(1).getAttribute("data-record-id");
  const grip = page.locator(`[data-record-id="${first}"] .drag-grip`);
  await grip.focus();
  await grip.press("ArrowDown");
  await expect(page.locator("[data-pane='case-file'] [aria-live='polite']")).toContainText(
    "Moved",
  );
  await expect(list.nth(0)).toHaveAttribute("data-record-id", second);
  await expect(list.nth(1)).toHaveAttribute("data-record-id", first);

  const card = page.locator(`[data-pane='case-file'] [data-record-id="${first}"]`);
  await expect(card.locator(".shared-tag")).toHaveCount(0);
  await card.dragTo(page.locator("[data-pane='team-chat']"));
  try {
    await expect(card.locator(".shared-tag")).toBeVisible({ timeout: 3_000 });
  } catch {
    // Chromium's intercepted drag drops custom MIME types, so Playwright's
    // dragTo can arrive without text/record-id. Dispatch the same events the
    // pane listens for, with one DataTransfer the drop handler can read.
    await page.evaluate((id) => {
      const source = document.querySelector(
        `[data-pane='case-file'] [data-record-id="${CSS.escape(id)}"]`,
      );
      const chat = document.querySelector("[data-pane='team-chat']");
      if (!(source instanceof HTMLElement) || !(chat instanceof HTMLElement)) {
        throw new Error("drag targets missing");
      }
      const data = new DataTransfer();
      source.dispatchEvent(
        new DragEvent("dragstart", { bubbles: true, cancelable: true, dataTransfer: data }),
      );
      chat.dispatchEvent(
        new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: data }),
      );
      chat.dispatchEvent(
        new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: data }),
      );
    }, first);
    await expect(card.locator(".shared-tag")).toBeVisible();
  }
  // The tag can paint from the committed share event before the drag
  // telemetry item is queued. Give that callback a beat, then hide the page:
  // departure sends the queue without waiting for the 4s flush.
  await page.waitForTimeout(500);
  const finalsBefore = (await telemetryLines()).filter((line) =>
    line.startsWith("telemetry_file_order_final"),
  ).length;
  await page.evaluate(() => window.dispatchEvent(new Event("pagehide")));
  await expect
    .poll(async () => (await telemetryLines()).join("\n"), { timeout: 8_000 })
    .toContain(`telemetry_share_attempt ${first} drag`);
  await expect
    .poll(
      async () =>
        (await telemetryLines()).filter((line) => line.startsWith("telemetry_file_order_final"))
          .length,
      { timeout: 8_000 },
    )
    .toBeGreaterThan(finalsBefore);
  await page.close();
});
