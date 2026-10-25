import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { expect, it } from "vitest";

it.each([
  ["nosolo_nl_noassist_nopress_aiqa", ""],
  ["nosolo_nl_noassist_nopress_aiqa_high", ""],
  ["nosolo_nl_high", "variant high"],
  ["nosolo_nl_low", "variant low"],
])("keeps the %s playable mock header neutral across screens", (variant, suffix) => {
  const stem = `investigators_${variant}_demo`;
  const html = readFileSync(resolve("public", "demos", `${stem}.html`), "utf8");
  const js = readFileSync(resolve("public", "demos", `${stem}.js`), "utf8");
  const dom = new JSDOM(html, {
    runScripts: "outside-only",
    beforeParse(window) { window.scrollTo = () => {}; },
  });
  try {
    dom.window.eval(js);
    const title = dom.window.document.querySelector(".mock-title")?.textContent;
    expect(title).toContain("PLAYABLE MOCK");
    expect(title).not.toMatch(/pressure|time.pressure| arm/i);
    if (suffix) expect(title).toContain(suffix);
    else expect(title).not.toContain("variant");
    const controller = (dom.window as unknown as {
      __demo: { go(stage: string): void };
    }).__demo;
    controller.go("task");
    expect(dom.window.document.querySelector(".mock-title")?.textContent).toBe(title);
  } finally {
    dom.window.close();
  }
});

it("keeps rapid mock Q&A replies attached to their own questions", () => {
  const html = readFileSync(resolve("..", "demos", "template", "investigators_demo.html"), "utf8");
  const dom = new JSDOM(html, {
    runScripts: "dangerously",
    beforeParse(window) { window.scrollTo = () => {}; },
  });
  try {
    const w = dom.window;
    expect(w.document.querySelector(".mock-title")?.textContent).not.toMatch(/pressure/i);
    const callbacks: (() => void)[] = [];
    w.setTimeout = (callback) => {
      if (typeof callback === "function") callbacks.push(() => callback());
      return callbacks.length;
    };
    (w as unknown as { __demo: { go(stage: string): void } }).__demo.go("aiqa");
    const input = w.document.querySelector<HTMLTextAreaElement>("#qa-question")!;
    const ask = w.document.querySelector<HTMLButtonElement>("#qa-ask")!;
    for (const question of ["How many crates?", "How long is the task?"]) {
      input.value = question;
      input.dispatchEvent(new w.Event("input"));
      ask.click();
    }
    expect(callbacks).toHaveLength(2);
    callbacks[1]!(); // completion order must not decide the owning question
    callbacks[0]!();
    const replies = [...w.document.querySelectorAll(".qa-answer")];
    expect(replies).toHaveLength(2);
    expect(replies[0]?.textContent).toContain("assigned");
    expect(replies[1]?.textContent).toContain("timer");
    expect(w.document.querySelector(".qa-answer.pending")).toBeNull();
  } finally {
    dom.window.close();
  }
});
