import { expect, test } from "@playwright/test";

// Proves the pinned Playwright/Chromium dependency can launch. It does not
// open the study, a lab URL, or a local API.
test("chromium opens a blank page without touching the study", async ({ page }) => {
  await page.goto("about:blank");
  await expect(page).toHaveURL("about:blank");
});
