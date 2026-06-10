#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const ROOT = path.resolve(__dirname, "..");
const DEFAULT_ACCOUNT_FILE = "/Users/sunlixiao/Desktop/api 账号信息.rtf";
const TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token";
const TEMPLATE_SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/template/send";

function readEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return {};
  const values = {};
  for (const line of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match) continue;
    values[match[1]] = match[2].replace(/^["']|["']$/g, "");
  }
  return values;
}

function readAccountFile(filePath) {
  if (!fs.existsSync(filePath)) return {};
  let text = "";
  if (filePath.endsWith(".rtf")) {
    text = execFileSync("textutil", ["-convert", "txt", "-stdout", filePath], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    });
  } else {
    text = fs.readFileSync(filePath, "utf8");
  }

  return {
    WECHAT_APP_ID: pick(text, /AppID[^：:]*[：:]\s*([A-Za-z0-9_-]+)/i),
    WECHAT_APP_SECRET: pick(text, /AppSecret[^：:]*[：:]\s*([A-Za-z0-9_-]+)/i),
    WECHAT_OPENID: pick(text, /用户\s*id[^：:]*[：:]\s*([A-Za-z0-9_-]+)/i),
    WECHAT_TEMPLATE_ID: pick(text, /(?:template[_\s-]*id|模[板版]\s*id)[^：:]*[：:]\s*([A-Za-z0-9_-]+)/i),
  };
}

function pick(text, pattern) {
  const match = text.match(pattern);
  return match ? match[1].trim() : undefined;
}

function configValue(name, sources) {
  for (const source of sources) {
    if (source[name]) return source[name];
  }
  return undefined;
}

function assertPresent(config, names) {
  const missing = names.filter((name) => !config[name]);
  if (missing.length) {
    throw new Error(
      `Missing required config: ${missing.join(", ")}. ` +
        "Put it in .env, environment variables, or the desktop account file."
    );
  }
}

async function getAccessToken(appId, appSecret) {
  const url = new URL(TOKEN_URL);
  url.searchParams.set("grant_type", "client_credential");
  url.searchParams.set("appid", appId);
  url.searchParams.set("secret", appSecret);

  const response = await fetch(url);
  const json = await response.json();
  if (!json.access_token) {
    throw new Error(`Failed to get WeChat access_token: ${safeJson(json)}`);
  }
  return json.access_token;
}

async function sendTemplateMessage({ accessToken, openid, templateId, content, detailUrl }) {
  const url = new URL(TEMPLATE_SEND_URL);
  url.searchParams.set("access_token", accessToken);

  const payload = {
    touser: openid,
    template_id: templateId,
    data: {
      xxx: { value: content },
    },
  };
  if (detailUrl) {
    payload.url = detailUrl;
  }

  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const json = await response.json();
  if (json.errcode !== 0) {
    if (json.errcode === 48001) {
      throw new Error(
        "Failed to send WeChat template message: api unauthorized. " +
          "请确认使用的是微信公众号测试号的 AppID/AppSecret 和该测试号下的 openid/template_id，" +
          "不是小程序 AppID 或小程序用户 id。"
      );
    }
    throw new Error(`Failed to send WeChat template message: ${safeJson(json)}`);
  }
  return json;
}

function todayInShanghai() {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function readBrief(date) {
  const dailyPath = path.join(ROOT, "ai-product-intelligence", "daily", `${date}.md`);
  if (fs.existsSync(dailyPath)) {
    return fs.readFileSync(dailyPath, "utf8").trim();
  }
  return [
    `# AI 产品商业情报｜${date}`,
    "",
    "今天的本地简报文件还不存在。可以先用这条测试消息确认微信推送链路。",
  ].join("\n");
}

function compactForWeChat(markdown, maxChars = 1800) {
  const text = markdown
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*\*/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .trim();
  if (text.length <= maxChars) return text;
  return `${text.slice(0, maxChars - 40)}\n\n……内容较长，完整版本见每日 Markdown 文件。`;
}

function summarizeForWeChat(markdown, detailUrl, maxChars = 160) {
  const text = compactForWeChat(markdown, maxChars);
  if (!detailUrl) return text;
  return `${text}\n\n点击消息查看全文。`;
}

function safeJson(value) {
  return JSON.stringify(value, (key, val) => {
    if (/token|secret|appid|openid/i.test(key)) return "[REDACTED]";
    return val;
  });
}

async function main() {
  const args = new Set(process.argv.slice(2));
  const dryRun = args.has("--dry-run");
  const testMessage = args.has("--test-message");
  const hundredCharTest = args.has("--hundred-char-test");
  const accountFileArg = process.argv.find((arg) => arg.startsWith("--account-file="));
  const accountFile = accountFileArg ? accountFileArg.split("=").slice(1).join("=") : DEFAULT_ACCOUNT_FILE;

  const dotenv = readEnvFile(path.join(ROOT, ".env"));
  const accountFileValues = readAccountFile(accountFile);
  const sources = [process.env, dotenv, accountFileValues];

  const config = {
    WECHAT_APP_ID: configValue("WECHAT_APP_ID", sources),
    WECHAT_APP_SECRET: configValue("WECHAT_APP_SECRET", sources),
    WECHAT_OPENID: configValue("WECHAT_OPENID", sources),
    WECHAT_TEMPLATE_ID: configValue("WECHAT_TEMPLATE_ID", sources),
    WECHAT_DETAIL_URL: configValue("WECHAT_DETAIL_URL", sources),
  };

  assertPresent(config, ["WECHAT_APP_ID", "WECHAT_APP_SECRET", "WECHAT_OPENID"]);
  if (!config.WECHAT_TEMPLATE_ID) {
    throw new Error(
      "Missing WECHAT_TEMPLATE_ID. 在微信公众号测试号后台新增一个模板后，把模板 ID 放进 .env 的 WECHAT_TEMPLATE_ID。"
    );
  }

  const date = todayInShanghai();
  const title = `AI 产品商业情报｜${date}`;
  const content = hundredCharTest
    ? `这是一条约一百字的微信测试内容：今天我们验证公众号测试号模板是否能正常承载较长文本。后续 AI 商业情报会包含产品、用户痛点、收入估算、技术路径和个人创业者可切入机会。`
    : testMessage
      ? `微信推送链路测试成功｜${date}\n\n如果你看到这条消息，说明公众号测试号模板、用户 openid、access_token 获取和发送接口都已打通。`
      : summarizeForWeChat(readBrief(date), config.WECHAT_DETAIL_URL);

  if (dryRun) {
    console.log("Dry run passed. Required WeChat config is present. No secrets printed.");
    console.log(`Message title: ${title}`);
    console.log(`Message length: ${content.length}`);
    return;
  }

  const accessToken = await getAccessToken(config.WECHAT_APP_ID, config.WECHAT_APP_SECRET);
  await sendTemplateMessage({
    accessToken,
    openid: config.WECHAT_OPENID,
    templateId: config.WECHAT_TEMPLATE_ID,
    content,
    detailUrl: config.WECHAT_DETAIL_URL,
  });

  console.log("WeChat template message sent.");
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
