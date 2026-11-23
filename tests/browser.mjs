import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";
import {fileURLToPath} from "node:url";
import {beijingInput} from "../dist/assets/model.mjs";

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.ATTENDANCE_PLAYWRIGHT || "playwright");
const base = process.env.ATTENDANCE_TEST_URL || "http://127.0.0.1:8765";
const artifacts = await fs.mkdtemp(path.join(os.tmpdir(), "upc-site-qa-"));
const fixtures = path.join(artifacts, "accounts");
execFileSync(process.env.ATTENDANCE_PYTHON || "python", ["-B",
  fileURLToPath(new URL("./create-login-fixtures.py", import.meta.url)), fixtures], {stdio: "ignore"});
const browser = await chromium.launch({headless: true, executablePath: process.env.ATTENDANCE_CHROMIUM});
const errors = [];
let sequence = 0;

async function context(options = {}) {
  const scope = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, ...options});
  await scope.route("**/assets/account-public.mjs", route => route.fulfill({path: path.join(fixtures, "public.mjs"),
    contentType: "text/javascript"}));
  return scope;
}

async function pageAt(scope, target) {
  const page = await scope.newPage();
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", response => {
    if (response.status() >= 400 && response.url().startsWith(base)) errors.push(response.url() + " " + response.status());
  });
  await page.goto(base + "/" + target);
  await page.locator("h1").waitFor();
  assert.doesNotMatch(await page.locator("h1").innerText(), /无法打开/);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  return page;
}

function click(scope, name) {
  return scope.getByRole("button", {name, exact: true}).first().click();
}

async function download(scope, name) {
  const pending = scope.waitForEvent("download");
  await click(scope, name);
  const file = await pending;
  const result = path.join(artifacts, String(++sequence) + "-" + file.suggestedFilename());
  await file.saveAs(result);
  return result;
}

async function upload(scope, name, files, confirm = true) {
  const pending = scope.waitForEvent("filechooser");
  await click(scope, name);
  await (await pending).setFiles(files);
  if (confirm) {
    await click(scope.getByRole("dialog"), "确认导入");
    await scope.getByRole("dialog").waitFor({state: "hidden"});
  }
}

async function includes(scope, text) {
  await scope.getByText(text, {exact: false}).first().waitFor();
}

async function submitLogin(page, user, password, file) {
  await page.getByLabel("用户名", {exact: true}).fill(user);
  await page.getByLabel("密码", {exact: true}).fill(password);
  if (file) await page.locator("#account-file").setInputFiles(file);
  await click(page, "登录");
}

async function login(page, role, user, password, file) {
  await submitLogin(page, user, password, file);
  await page.waitForURL(new RegExp("/" + role + "\\.html"));
  await page.getByRole("button", {name: "退出登录"}).waitFor();
}

const studentFile = (...segments) => path.join(fixtures, "学生", "测试2401", ...segments);

try {
  for (const [width, height] of [[1440, 980], [390, 844], [320, 700]]) {
    const scope = await context({viewport: {width, height}, isMobile: width < 800});
    for (const role of ["index", "student", "leader", "admin", "login"]) {
      const page = await pageAt(scope, role + ".html" + (role === "login" ? "" : "?demo=1"));
      await page.screenshot({path: path.join(artifacts, role + "-" + width + ".png"), fullPage: true});
      if (role === "student" && width === 390) {
        await click(page, "去填报");
        await page.getByRole("dialog").waitFor();
        await click(page, "模拟校外");
        await includes(page.getByRole("dialog"), "当前位置超出任务允许区域");
        await click(page, "关闭");
      }
    }
    await scope.close();
  }

  const staffBrowser = await context();
  const leaderBrowser = await context();
  const studentBrowser = await context({permissions: ["geolocation"],
    geolocation: {longitude: 120.17, latitude: 35.937, accuracy: 19}});

  const admin = await pageAt(staffBrowser, "login.html");
  const leader = await pageAt(leaderBrowser, "login.html");
  const student = await pageAt(studentBrowser, "login.html");
  const firstAccount = studentFile("01-测试甲.json");
  const tampered = JSON.parse(await fs.readFile(firstAccount, "utf8"));
  tampered.signature = (tampered.signature[0] === "A" ? "B" : "A") + tampered.signature.slice(1);
  await submitLogin(student, "测试甲", "001", {name: "篡改.json", mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(tampered))});
  await includes(student, "账户文件未通过签发校验");
  await submitLogin(student, "测试甲", "999", firstAccount);
  await includes(student, "用户名或密码不正确");
  await login(student, "student", "测试甲", "001", firstAccount);
  await login(leader, "leader", "A测试甲", "001", path.join(fixtures, "班级负责人", "测试2401", "01-A测试甲.json"));
  await login(admin, "admin", "admin", "fixture-pass", path.join(fixtures, "管理员账户.json"));
  await includes(admin, "3 名学生 · 2 个班级 · 1 条负责人记录");
  await includes(leader, "测试2401 · 晚点名进度与报送");
  await student.goto(base + "/admin.html");
  await student.waitForURL("**/student.html");
  assert.equal(await student.getByRole("button", {name: "编辑信息", exact: true}).count(), 0);

  const roster = {name: "测试学籍.csv", mimeType: "text/csv", buffer: Buffer.from(
    "姓名,班级,学号\n测试甲,测试2401,001\n测试乙,测试2401,002\n测试丙,测试2402,003\n")};
  await upload(admin, "导入学籍 CSV", roster);
  await includes(admin, "3 名学生 · 2 个班级");
  const staff = {name: "测试负责人.csv", mimeType: "text/csv", buffer: Buffer.from("姓名,班级,学号\n测试甲,测试2401,001\n")};
  await upload(admin, "导入负责人 CSV", staff);
  await includes(admin, "待核对负责人 测试2402");

  const fence = {name: "测试校区.geojson", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({type: "Polygon",
    coordinates: [[[120.165, 35.934], [120.174, 35.934], [120.174, 35.941], [120.165, 35.941], [120.165, 35.934]]]}))};
  await click(admin, "配置校区围栏");
  await admin.getByLabel("校园边界文件").setInputFiles(fence);
  await click(admin, "保存围栏");
  await includes(admin, "唐岛湾校区");

  await click(admin, "新建任务");
  await admin.getByLabel("任务标题").fill("多设备验收任务");
  await admin.getByLabel("任务说明").fill("请用北京时间完成报备。");
  await admin.getByLabel("开放时间（北京时间）").fill(beijingInput(Date.now() - 60000));
  await admin.getByLabel("截止时间（北京时间）").fill(beijingInput(Date.now() + 3600000));
  await click(admin, "保存草稿");
  await click(admin, "编辑并发布");
  await click(admin, "预览发布");
  await click(admin, "返回编辑");
  assert.equal(await admin.getByLabel("任务标题").inputValue(), "多设备验收任务");
  await click(admin, "预览发布");
  await includes(admin.getByRole("dialog"), "应报人数");
  await click(admin, "确认发布");
  const taskFile = await download(admin, "下载任务包");
  const classFile = await download(admin, "下载该班应报名单");
  await admin.getByLabel("应报名单班级").selectOption("测试2402");
  const otherClass = await download(admin, "下载该班应报名单");
  await click(admin, "关闭");
  const task = JSON.parse(await fs.readFile(taskFile, "utf8"));
  assert.deepEqual(task.data.groups, ["测试2401", "测试2402"]);
  assert.equal(JSON.stringify(task.data).includes("测试甲"), false);
  await includes(leader, "暂无班级任务");

  await upload(leader, "导入文件", [classFile, taskFile]);
  await includes(leader, "测试甲");
  await upload(leader, "导入文件", otherClass, false);
  await includes(leader.getByRole("dialog"), "该文件不属于你负责的班级");
  await click(leader, "关闭");
  await upload(student, "导入任务", taskFile);
  await includes(student, "打卡回执会自动使用以上学籍信息。");

  await click(leader, "提醒");
  const text = await leader.getByLabel("提醒文案").inputValue();
  assert.ok(!text.includes("测试甲"));
  const url = text.split("\n").at(-1);
  await click(leader, "关闭");
  await student.goto(url);
  await student.getByRole("dialog").waitFor();
  await student.getByRole("button", {name: /^是，我在校/}).click();
  await includes(student.getByRole("dialog"), "在校打卡成功");
  await includes(student.getByRole("dialog"), "120.17000, 35.93700");
  const receipt = await download(student, "下载回执");
  const record = JSON.parse(await fs.readFile(receipt, "utf8"));
  assert.equal(record.data.accuracy, 19);
  assert.equal(record.data.longitude, undefined);
  assert.equal(record.data.studentId, "001");
  assert.equal(record.data.name, "测试甲");

  const awayBrowser = await context();
  const away = await pageAt(awayBrowser, "login.html");
  await login(away, "student", "测试乙", "002", studentFile("02-测试乙.json"));
  await upload(away, "导入任务", taskFile);
  await click(away, "去填报");
  await away.getByRole("button", {name: /^否，我不在校/}).click();
  await away.getByLabel("离校原因").fill("在外参加活动\n次日返校。");
  await click(away, "确认报备");
  await includes(away.getByRole("dialog"), "离校报备已保存");
  const second = await download(away, "下载回执");

  await upload(leader, "导入文件", [receipt, second]);
  await includes(leader, "完成报备率 100%");
  await upload(leader, "导入文件", receipt);
  await includes(leader, "完成报备率 100%");
  await leader.getByLabel("查找学生").fill("002");
  await click(leader, "详情");
  await includes(leader.getByRole("dialog"), "在外参加活动");
  await click(leader, "关闭");
  const classCSV = await download(leader, "导出本班 CSV");
  assert.match(await fs.readFile(classCSV, "utf8"), /"002","离校报备"/);
  await click(leader, "报送管理员");
  await leader.getByLabel("班级情况说明（可选）").fill("已核对本班回执。");
  const reportFile = await download(leader, "生成报送包");
  await upload(admin, "导入班级报送", reportFile);
  await includes(admin, "已报送范围完成率");
  await includes(admin, "1/2");
  await includes(admin, "未报送");
  await click(admin, "详情");
  await includes(admin.getByRole("dialog"), "已核对本班回执。");
  await click(admin, "关闭");
  const summary = await download(admin, "导出年级汇总");
  assert.match(await fs.readFile(summary, "utf8"), /"测试2402","未报送"/);

  await click(admin, "延长截止");
  await click(admin, "保存新版本");
  const updated = await download(admin, "下载任务包");
  assert.equal(JSON.parse(await fs.readFile(updated, "utf8")).data.revision, 2);
  await click(admin, "关闭");
  await click(admin, "撤销任务");
  await click(admin, "确认撤销");
  const revoked = await download(admin, "下载撤销通知");
  const cancellation = JSON.parse(await fs.readFile(revoked, "utf8"));
  assert.equal(cancellation.data.mode, "revoked");
  assert.equal(cancellation.data.revision, 3);
  await click(student, "关闭");
  await upload(student, "导入任务", revoked, false);
  await click(student.getByRole("dialog"), "确认导入");
  await includes(student.getByRole("dialog"), "已撤销");
  await click(student, "关闭");
  await click(student, "本机数据");
  const backup = await download(student, "下载本机备份");
  assert.equal(JSON.parse(await fs.readFile(backup, "utf8")).data.workspace.receipts.length, 1);
  await student.getByLabel("可清理的已结束任务").selectOption(task.data.id);
  await click(student, "选择清理任务");
  await click(student, "确认清理");
  await click(student, "关闭");
  await click(student, "查看");
  await includes(student.getByRole("dialog"), "已清理此任务的本机报备数据");
  await click(student, "关闭");
  await click(student, "退出登录");
  await student.waitForURL("**/login.html");
  await student.goto(base + "/student.html#task=" + task.data.id);
  await student.waitForURL("**/login.html?task=*");
  await login(student, "student", "测试甲", "001");
  await includes(student.getByRole("dialog"), "已撤销");
  await click(leader, "退出登录");
  await leader.waitForURL("**/login.html");
  await click(admin, "关闭");
  await click(admin, "退出登录");
  await admin.waitForURL("**/login.html");
  assert.deepEqual(errors, []);
  console.log("Browser QA passed at 320, 390 and 1440px. Outputs: " + artifacts);
} finally {
  console.log("QA artifacts: " + artifacts);
  await browser.close();
}
