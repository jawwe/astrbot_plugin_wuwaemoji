/* Browser regression tests with AstrBot's real bridge SDK and synthetic local data. */
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const net = require("node:net");
const { spawn } = require("node:child_process");
const { chromium } = require("playwright");

(async () => {
  const root = path.resolve(__dirname, "..");
  const python = process.env.ASTRBOT_TEST_PYTHON || "python";
  const astrbot = process.env.ASTRBOT_TEST_ROOT;
  assert(astrbot, "Set ASTRBOT_TEST_ROOT to an installed AstrBot checkout");
  const port = await new Promise((resolve) => {
    const listener = net.createServer();
    listener.listen(0, "127.0.0.1", () => { const value = listener.address().port; listener.close(() => resolve(value)); });
  });
  const server = spawn(python, [path.join(__dirname, "admin_ui_fixture.py"), "--astrbot-root", astrbot, "--port", String(port)], { cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, PYTHONUTF8: "1" } });
  let log = "";
  server.stderr.on("data", (data) => { log = (log + data).slice(-10000); });
  server.stdout.on("data", (data) => { log = (log + data).slice(-10000); });
  let browser;
  try {
    const url = `http://127.0.0.1:${port}`;
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      try { ready = (await (await fetch(url)).text()).includes("data-wuwa-fixture"); } catch {}
      if (ready) break;
      if (server.exitCode !== null) throw Error(log);
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
    assert(ready, `Fixture did not start: ${log}`);
    browser = await chromium.launch({ headless: true, ...(process.env.ADMIN_UI_CHROME ? { executablePath: process.env.ADMIN_UI_CHROME } : {}) });
    const page = await browser.newPage({ viewport: { width: 1360, height: 1040 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(url);
    const view = page.frameLocator("#view");
    await view.locator(".emoji-card").first().waitFor();
    assert.equal(await view.locator("#stat-count").innerText(), "6");
    await view.locator("img.loaded").first().waitFor();
    const uiFrame = page.frames().find((frame) => frame.url().endsWith("/view/"));
    await uiFrame.waitForFunction(() => [...document.querySelectorAll(".image-area img")].every((img) => img.classList.contains("loaded")));
    const output = path.join(root, ".pytest_cache", "admin-ui");
    fs.mkdirSync(output, { recursive: true });
    await page.screenshot({ path: path.join(output, "desktop-local.png"), fullPage: true, animations: "disabled" });

    await view.getByRole("tab", { name: "小站搜索" }).click();
    await view.locator('#remote-form input[name="character"]').fill("爱弥斯");
    await view.getByRole("button", { name: "搜索小站", exact: true }).click();
    await view.locator(".emoji-card").nth(7).waitFor();
    assert.equal(await view.locator("#stat-count").innerText(), "6", "Preview must not persist remote media");
    await view.locator(".card-check input").nth(0).check();
    await view.locator(".card-check input").nth(1).check();
    await view.locator("#target-category").selectOption("开心");
    await view.locator("#apply-selection").click();
    await view.locator("#job-state").filter({ hasText: "缓存完成" }).waitFor({ timeout: 15000 });
    await page.waitForFunction(() => document.querySelector("iframe").contentWindow !== null);
    await view.locator("#stat-count").filter({ hasText: "8" }).waitFor();

    await view.locator(".batch-panel summary").click();
    await view.locator('#batch-form input[name="pages"]').fill("2");
    await view.locator('#batch-form input[name="maximum"]').fill("4");
    await view.getByRole("button", { name: "开始批量缓存", exact: true }).click();
    await view.locator("#job-counts").filter({ hasText: "成功 4" }).waitFor({ timeout: 15000 });
    await view.locator("#job-state").filter({ hasText: "缓存完成" }).waitFor();
    await page.screenshot({ path: path.join(output, "desktop-batch.png"), fullPage: true });

    await view.locator('#batch-form input[name="pages"]').fill("5");
    await view.locator('#batch-form input[name="start_page"]').fill("4");
    await view.locator('#batch-form input[name="maximum"]').fill("10");
    await view.getByRole("button", { name: "开始批量缓存", exact: true }).click();
    await view.locator("#cancel-job:not([disabled])").waitFor();
    await view.locator("#cancel-job").click();
    await view.locator("#job-state").filter({ hasText: "任务已取消" }).waitFor({ timeout: 15000 });

    await view.getByRole("tab", { name: "本地图库" }).click();
    await view.locator(".emoji-card").first().waitFor();
    await view.locator(".card-action").first().click();
    await view.locator('#edit-form textarea').fill("用于开心地回应朋友");
    await view.locator('#edit-category').selectOption("开心");
    await view.locator('#edit-form button[type="submit"]').click();
    await view.locator("#edit-dialog").waitFor({ state: "hidden" });
    await view.getByRole("button", { name: "＋ 新建分类" }).click();
    await view.locator('#category-form input').fill("浏览器测试分类");
    await view.locator('#category-form textarea').fill("用于测试保存与刷新分离");
    await page.request.post(`${url}/fixture/fail-refresh`);
    await view.locator('#category-form button[type="submit"]').click();
    await view.locator("#category-dialog").waitFor({ state: "hidden" });
    await view.locator("#notice.warning").filter({ hasText: "已保存" }).waitFor();
    await view.locator("#refresh").click();
    await view.locator("#notice").filter({ hasText: "图库已刷新" }).waitFor();
    await view.locator('#target-category option[value="浏览器测试分类"]').waitFor({ state: "attached" });

    await page.evaluate(() => window.setTheme(true));
    await view.locator('html[data-theme="dark"]').waitFor();
    await page.screenshot({ path: path.join(output, "desktop-dark.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    const frame = page.frames().find((frame) => frame.url().endsWith("/view/"));
    assert(await frame.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), "Mobile layout must not overflow horizontally");
    await page.screenshot({ path: path.join(output, "mobile.png"), fullPage: true });
    await page.reload();
    await view.locator(".emoji-card").first().waitFor();
    await view.locator("#job-state").filter({ hasText: "任务已取消" }).waitFor();
    assert.deepEqual(errors, [], "No browser runtime errors");
    console.log("Admin UI: real bridge, local previews, selected/multi-page cache, cancellation, classification, refresh failure, themes and mobile layout: OK");
    console.log(`Screenshots: ${output}`);
  } catch (error) {
    if (browser) {
      const page = browser.contexts()[0]?.pages()[0];
      if (page) {
        const output = path.join(root, ".pytest_cache", "admin-ui");
        fs.mkdirSync(output, { recursive: true });
        await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true });
        for (const frame of page.frames()) {
          console.error("Fixture frame:", frame.url(), await frame.locator("body").innerText().catch(() => "unavailable"));
        }
      }
    }
    console.error(log);
    throw error;
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
