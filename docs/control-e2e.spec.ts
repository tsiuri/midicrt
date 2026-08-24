// Playwright e2e spec for the /control surface (run from any box with
// @playwright/test: symlink a node_modules, baseURL http://192.168.0.171:8765).
// Kept in docs/ as the reference harness — the repo has no JS toolchain.
import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  page.on("console", (msg) => {
    if (["error", "warning"].includes(msg.type()))
      console.log(`[browser ${msg.type()}]`, msg.text());
  });
  page.on("pageerror", (err) => console.log("[pageerror]", err.message));
});

test("control surface renders and posts field changes", async ({ page }) => {
  await page.goto("/control");
  await expect(page.locator("#tabs button")).toHaveCount(3);
  await expect(page.locator("#status")).toHaveText("ready");

  // LXP-1 tab default: fields render
  await expect(page.locator(".field").first()).toBeVisible();

  // move first slider; expect a successful set POST
  const reqP = page.waitForRequest((r) =>
    r.url().includes("/api/manage/control/set") && r.method() === "POST");
  const slider = page.locator('.field input[type="range"]').first();
  await slider.focus();
  await page.keyboard.press("ArrowRight");
  const req = await reqP;
  const payload = req.postDataJSON();
  console.log("set payload:", JSON.stringify(payload));
  expect(payload.device).toBe("lxp1");
  const res = await (await page.waitForResponse((r) =>
    r.url().includes("/api/manage/control/set"))).json();
  expect(res.ok).toBe(true);

  // switch to Matrix-1000 tab: 13 groups render
  await page.locator("#tabs button").nth(1).click();
  await expect(page.locator("main details")).toHaveCount(14); // actions + 13 groups
  // open Mod Matrix group and change a choice select
  const mm = page.locator("main details").last();
  await mm.locator("summary").click();
  const req2P = page.waitForRequest((r) =>
    r.url().includes("/api/manage/control/set") && r.method() === "POST");
  await mm.locator("select").first().selectOption({ index: 4 });
  const req2 = await req2P;
  console.log("mod payload:", JSON.stringify(req2.postDataJSON()));
  expect(req2.postDataJSON().device).toBe("matrix1000");

  await page.screenshot({ path: "control-matrix.png", fullPage: false });
});

test("tg77 tab renders operator groups", async ({ page }) => {
  await page.goto("/control");
  await page.locator("#tabs button").nth(2).click();
  await expect(page.locator("main details")).toHaveCount(11); // actions + 10 groups
  const op1 = page.locator("main details").nth(1); // OP1 renders open by default
  const reqP = page.waitForRequest((r) =>
    r.url().includes("/api/manage/control/set") && r.method() === "POST");
  const slider = op1.locator('input[type="range"]').first();
  await slider.focus();
  await page.keyboard.press("ArrowRight");
  const req = await reqP;
  console.log("tg77 payload:", JSON.stringify(req.postDataJSON()));
});
