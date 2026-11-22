const MAX_BODY_BYTES = 64 * 1024;
const SAFE_NETWORK_ERROR_CODES = new Set([
  "EAI_AGAIN",
  "ENOTFOUND",
  "ECONNRESET",
  "ETIMEDOUT",
  "ECONNREFUSED",
  "EHOSTUNREACH",
  "ENETUNREACH",
  "ERR_TLS_CERT_ALTNAME_INVALID",
  "CERT_HAS_EXPIRED",
  "ERR_CERT_AUTHORITY_INVALID",
]);
const TELEGRAM_TIMEOUT_MS = 8000;
const HUB_CLIENT_ID_DEFAULT = "3a1c9d72-6f84-4b35-9e21-7d0c8a6f4b52";
const TELEGRAM_WEBHOOK_PATH = "/telegram/webhook";
const CLIENT_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const BRIDGE_OWNER_COMMANDS = new Set([
  "/hub", "/tasks", "/memory", "/memoryentry", "/memoryapprove",
  "/memorydream", "/memorycheck", "/task", "/taskstatus", "/taskprogress",
  "/taskplan", "/agentplan", "/taskplanrun", "/taskplans", "/taskplanstatus",
  "/taskplancancel", "/taskplanpause", "/taskplanresume", "/taskresult",
  "/taskcancel", "/taskhistory", "/videoproviders", "/videoroute",
  "/videohistory", "/videostatus", "/videocancel", "/videodrafts",
  "/videorenders", "/videorendercheck", "/videorender",
  "/videorenderstatus", "/videorendercancel", "/videoscenes",
  "/videoscenestatus", "/videoscenecancel", "/videoscenehistory",
  "/videosceneexport",
]);


function getTelegramWebhookUrl(env) {
  const value = env.TELEGRAM_WEBHOOK_URL;
  if (
    typeof value !== "string" ||
    value.length > 2048 ||
    value !== value.trim()
  ) {
    return null;
  }
  try {
    const url = new URL(value);
    const hostname = url.hostname.toLowerCase();
    if (
      url.protocol !== "https:" ||
      !hostname ||
      !hostname.includes(".") ||
      hostname === "localhost" ||
      hostname.endsWith(".localhost") ||
      hostname.endsWith(".local") ||
      url.username ||
      url.password ||
      url.pathname !== TELEGRAM_WEBHOOK_PATH ||
      value.includes("?") ||
      value.includes("#")
    ) {
      return null;
    }
    return `${url.origin}${url.pathname}`;
  } catch {
    return null;
  }
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

export function constantTimeEqual(left, right) {
  if (typeof left !== "string" || typeof right !== "string") return false;
  const a = new TextEncoder().encode(left);
  const b = new TextEncoder().encode(right);
  let difference = a.length ^ b.length;
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i += 1) {
    difference |= (a[i] || 0) ^ (b[i] || 0);
  }
  return difference === 0;
}

export function parseTelegramCommand(text) {
  if (typeof text !== "string") return { command: "", args: [] };
  const parts = text.trim().split(/\s+/);
  const first = parts.shift() || "";
  if (!first.startsWith("/")) return { command: "", args: [] };
  return { command: first.split("@", 1)[0].toLowerCase(), args: parts };
}

function base64UrlToBytes(value) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(normalized + "=".repeat((4 - (normalized.length % 4)) % 4));
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function queueKey(env) {
  if (typeof env.QUEUE_ENCRYPTION_KEY !== "string") throw new Error("queue encryption is not configured");
  const raw = base64UrlToBytes(env.QUEUE_ENCRYPTION_KEY);
  if (raw.byteLength !== 32) throw new Error("queue encryption key must be 32 bytes");
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

async function encryptPayload(value, env) {
  const key = await queueKey(env);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const plaintext = new TextEncoder().encode(JSON.stringify(value));
  const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plaintext);
  return JSON.stringify({ iv: Array.from(iv), ciphertext: Array.from(new Uint8Array(ciphertext)) });
}

async function decryptPayload(value, env) {
  const record = JSON.parse(value);
  if (!Array.isArray(record.iv) || record.iv.length !== 12 || !Array.isArray(record.ciphertext)) {
    throw new Error("queued payload is invalid");
  }
  const plaintext = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: new Uint8Array(record.iv) },
    await queueKey(env),
    new Uint8Array(record.ciphertext),
  );
  return JSON.parse(new TextDecoder().decode(plaintext));
}

async function sha256Hex(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function readBoundedJson(request) {
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > MAX_BODY_BYTES || !request.body) return { ok: false, status: declared > MAX_BODY_BYTES ? 413 : 400 };
  const reader = request.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > MAX_BODY_BYTES) {
        await reader.cancel();
        return { ok: false, status: 413 };
      }
      chunks.push(part.value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    return value && typeof value === "object" && !Array.isArray(value)
      ? { ok: true, value, bytes }
      : { ok: false, status: 400 };
  } catch {
    return { ok: false, status: 400 };
  }
}

async function telegramCall(env, method, payload) {
  let response;
  try {
    response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(TELEGRAM_TIMEOUT_MS),
      redirect: "error",
    });
  } catch (error) {
    const failure = new Error("telegram_transport_failure");
    failure.stage = method;
    failure.kind = error?.name === "TimeoutError" ? "timeout" : "network";
    const cause = error && typeof error === "object" ? error.cause : null;
    failure.network_error_name =
      cause && ["Error", "TypeError", "DOMException", "TimeoutError"].includes(cause.name)
        ? cause.name
        : null;
    failure.network_error_code =
      cause && typeof cause.code === "string" && SAFE_NETWORK_ERROR_CODES.has(cause.code)
        ? cause.code
        : null;
    throw failure;
  }
  const data = await response.json().catch(() => null);
  if (!response.ok || !data?.ok) {
    const failure = new Error("telegram_api_rejected");
    failure.stage = method;
    failure.kind = "api_rejected";
    failure.http_status = response.status;
    failure.telegram_error_code = Number.isInteger(data?.error_code) ? data.error_code : null;
    throw failure;
  }
  return data.result;
}

async function sendMessage(env, chatId, text) {
  await telegramCall(env, "sendMessage", {
    chat_id: chatId,
    text: String(text).slice(0, 4000),
    disable_web_page_preview: true,
  });
}

async function registerWebhook(env) {
  const webhookUrl = getTelegramWebhookUrl(env);
  if (!webhookUrl) throw new Error("Telegram webhook URL is invalid");
  const registered = await telegramCall(env, "setWebhook", {
    url: webhookUrl,
    secret_token: env.TELEGRAM_WEBHOOK_SECRET,
    allowed_updates: ["message"],
    drop_pending_updates: false,
  });
  if (registered !== true) throw new Error("Telegram did not confirm webhook registration");
  const info = await telegramCall(env, "getWebhookInfo", {});
  if (info?.url !== webhookUrl) throw new Error("Telegram webhook read-back did not match");
  return info;
}

function validateTelegramEnvironment(env) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_WEBHOOK_SECRET) {
    return "Telegram bot credentials are missing";
  }
  if (!getTelegramWebhookUrl(env)) return "Telegram webhook URL is invalid";
  if (!env.DB || typeof env.DB.prepare !== "function") return "D1 database binding is missing";
  if (!/^\d{1,20}$/.test(String(env.ALLOWED_TELEGRAM_USER_ID || ""))) return "Owner Telegram ID is invalid";
  if (!/^[A-Za-z0-9_-]{32,128}$/.test(String(env.TELEGRAM_WEBHOOK_SECRET))) return "Telegram webhook secret format is invalid";
  return null;
}

function validateBridgeEnvironment(env) {
  if (!env.BRIDGE_SHARED_SECRET || String(env.BRIDGE_SHARED_SECRET).length < 32) {
    return "Bridge shared secret is missing or too short";
  }
  if (!/^[A-Za-z0-9_-]{43}$/.test(String(env.QUEUE_ENCRYPTION_KEY || ""))) {
    return "Queue encryption key format is invalid";
  }
  try {
    if (base64UrlToBytes(env.QUEUE_ENCRYPTION_KEY).byteLength !== 32) {
      return "Queue encryption key must encode exactly 32 bytes";
    }
  } catch {
    return "Queue encryption key format is invalid";
  }
  return null;
}

async function claimUpdate(env, updateId) {
  const result = await env.DB.prepare(
    "INSERT INTO telegram_processed_updates (update_id, state, lease_until, processed_at) VALUES (?, 'processing', unixepoch()+15, NULL) ON CONFLICT(update_id) DO UPDATE SET state='processing', lease_until=unixepoch()+15 WHERE telegram_processed_updates.state != 'done' AND telegram_processed_updates.lease_until < unixepoch() RETURNING update_id",
  ).bind(updateId).first();
  if (result) return "claimed";
  const existing = await env.DB.prepare(
    "SELECT state FROM telegram_processed_updates WHERE update_id = ?",
  ).bind(updateId).first();
  return existing?.state === "done" ? "done" : "busy";
}

async function completeUpdate(env, updateId) {
  await env.DB.prepare(
    "UPDATE telegram_processed_updates SET state='done', lease_until=0, processed_at=unixepoch() WHERE update_id=?",
  ).bind(updateId).run();
}

async function releaseUpdate(env, updateId) {
  await env.DB.prepare(
    "UPDATE telegram_processed_updates SET state='retry', lease_until=unixepoch() WHERE update_id=? AND state='processing'",
  ).bind(updateId).run();
}

async function enqueueJob(env, updateId, chatId, userId, payload) {
  const encrypted = await encryptPayload(payload, env);
  const jobId = crypto.randomUUID();
  const inserted = await env.DB.prepare(
    "INSERT OR IGNORE INTO telegram_jobs (job_id, update_id, chat_id, telegram_user_id, payload_ciphertext, state, lease_until, lease_token_hash, attempts, created_at, updated_at) SELECT ?, ?, ?, ?, ?, 'pending', 0, NULL, 0, unixepoch(), unixepoch() WHERE (SELECT COUNT(*) FROM telegram_jobs WHERE state IN ('pending','leased','sending')) < 1000",
  ).bind(jobId, updateId, chatId, userId, encrypted).run();
  if (Number(inserted.meta?.changes || 0) > 0) return true;
  const existing = await env.DB.prepare("SELECT job_id FROM telegram_jobs WHERE update_id = ?").bind(updateId).first();
  return Boolean(existing);
}

async function handleTelegramMessage(env, update) {
  const message = update.message;
  if (!message || typeof message !== "object") return;
  const userId = Number(message.from?.id);
  const chatId = Number(message.chat?.id);
  if (message.chat?.type !== "private" || userId !== Number(env.ALLOWED_TELEGRAM_USER_ID) || !Number.isSafeInteger(chatId)) {
    return;
  }
  const text = typeof message.text === "string" ? message.text.trim() : "";
  const { command, args } = parseTelegramCommand(text);
  if (command === "/start") {
    await sendMessage(env, chatId, "سلام ✨ ربات MR_SIAVASH_EL_IR_BOT آماده است. برای اطلاعات جفت‌سازی /pairinfo و برای اتصال /pair CODE را بفرست.");
    return;
  }
  if (command === "/help") {
    await sendMessage(env, chatId, "فرمان‌ها:\n/start — شروع\n/help — راهنما\n/id — شناسه تلگرام\n/pairinfo — اطلاعات اتصال\n/pair CODE — اتصال امن به HUB\n/status و /tasks — وضعیت و فهرست کارها\n/memory QUERY — جست‌وجوی حافظه\n/task TYPE | DESCRIPTION — ثبت وظیفه\n/taskstatus ID و /taskresult ID — وضعیت و نتیجه\n/taskplan TYPE | STEPS — ساخت طرح\n/agentplan local TYPE | GOAL — پیشنهاد طرح AI\n/taskplanrun ID و /taskplanstatus ID — اجرای تأییدشده و بررسی طرح\n/videoproviders و /videoroute — قابلیت‌ها و مسیر ویدئو\n/videohistory و /videostatus ID — تاریخچه و وضعیت تولید\nدسترسی هر فرمان به مجوزهای HUB وابسته است. اعلان پس‌زمینه و ارسال فایل در Bridge فعلاً فعال نیست.");
    return;
  }
  if (command === "/id") {
    await sendMessage(env, chatId, `شناسهٔ عددی تلگرام شما: ${userId}`);
    return;
  }
  if (command === "/pairinfo") {
    const clientId = String(env.HUB_CLIENT_ID || HUB_CLIENT_ID_DEFAULT);
    if (!CLIENT_ID_PATTERN.test(clientId)) {
      await sendMessage(env, chatId, "شناسهٔ کلاینت در Worker معتبر نیست؛ تنظیمات سرویس را بررسی کن.");
      return;
    }
    await sendMessage(env, chatId, `شناسه تلگرام: ${userId}\nشناسه کلاینت HUB: ${clientId}\nسطح دسترسی پیشنهادی: status:read`);
    return;
  }
  if (command === "/pair") {
    if (validateBridgeEnvironment(env)) {
      await sendMessage(env, chatId, "اتصال HUB هنوز پیکربندی نشده است؛ فرمان‌های ابری ربات مستقل از این بخش فعال می‌مانند.");
      return;
    }
    if (args.length !== 1 || args[0].length < 32 || args[0].length > 128) {
      await sendMessage(env, chatId, "فرمت صحیح: /pair کد_یک‌بارمصرف");
      return;
    }
    const queued = await enqueueJob(env, update.update_id, chatId, userId, { command: "pair", pairing_code: args[0] });
    if (!queued) await sendMessage(env, chatId, "صف درخواست‌ها موقتاً پر است؛ کمی بعد دوباره تلاش کن.");
    return;
  }
  if (BRIDGE_OWNER_COMMANDS.has(command)) {
    if (validateBridgeEnvironment(env)) {
      await sendMessage(env, chatId, "اتصال HUB هنوز پیکربندی نشده است؛ فرمان‌های ابری ربات مستقل از این بخش فعال می‌مانند.");
      return;
    }
    if (
      text.length > 4096 ||
      text !== text.trim() ||
      Array.from(text).some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
    ) {
      await sendMessage(env, chatId, "متن فرمان معتبر نیست یا بیش از حد طولانی است.");
      return;
    }
    const queued = await enqueueJob(env, update.update_id, chatId, userId, {
      command: "dispatch",
      text,
    });
    if (!queued) await sendMessage(env, chatId, "صف درخواست‌ها موقتاً پر است؛ کمی بعد دوباره تلاش کن.");
    return;
  }
  if (command === "/status") {
    if (validateBridgeEnvironment(env)) {
      await sendMessage(env, chatId, "اتصال HUB هنوز پیکربندی نشده است؛ فرمان‌های ابری ربات مستقل از این بخش فعال می‌مانند.");
      return;
    }
    const queued = await enqueueJob(env, update.update_id, chatId, userId, { command: "status" });
    if (!queued) await sendMessage(env, chatId, "صف درخواست‌ها موقتاً پر است؛ کمی بعد دوباره تلاش کن.");
    return;
  }
  await sendMessage(env, chatId, "این فرمان در نسخه فعلی پشتیبانی نمی‌شود. برای راهنما /help را بفرست.");
}

async function bridgeSignatureValid(request, bodyBytes, env) {
  const timestamp = request.headers.get("X-Bridge-Timestamp") || "";
  const nonce = request.headers.get("X-Bridge-Nonce") || "";
  const supplied = request.headers.get("X-Bridge-Signature") || "";
  if (!/^\d{10}$/.test(timestamp) || !/^[A-Za-z0-9_-]{20,80}$/.test(nonce) || !/^[a-f0-9]{64}$/i.test(supplied)) {
    return false;
  }
  if (Math.abs(Math.floor(Date.now() / 1000) - Number(timestamp)) > 60) return false;
  const bodyHash = await sha256Hex(new TextDecoder().decode(bodyBytes));
  const message = [timestamp, nonce, request.method, new URL(request.url).pathname, bodyHash].join("\n");
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(env.BRIDGE_SHARED_SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const signatureBytes = Uint8Array.from(supplied.match(/.{2}/g), (part) => parseInt(part, 16));
  const valid = await crypto.subtle.verify("HMAC", key, signatureBytes, new TextEncoder().encode(message));
  if (!valid) return false;
  const nonceHash = await sha256Hex(nonce);
  const inserted = await env.DB.prepare(
    "INSERT INTO bridge_nonces (nonce_hash, expires_at) VALUES (?, unixepoch()+120) ON CONFLICT(nonce_hash) DO NOTHING RETURNING nonce_hash",
  ).bind(nonceHash).first();
  return Boolean(inserted);
}

async function bridgePull(env) {
  const leaseToken = Array.from(crypto.getRandomValues(new Uint8Array(32)), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const leaseHash = await sha256Hex(leaseToken);
  const job = await env.DB.prepare(
    "UPDATE telegram_jobs SET state='leased', lease_until=unixepoch()+60, lease_token_hash=?, attempts=attempts+1, updated_at=unixepoch() WHERE job_id=(SELECT job_id FROM telegram_jobs WHERE state='pending' OR (state IN ('leased','sending') AND lease_until < unixepoch()) ORDER BY created_at LIMIT 1) RETURNING job_id, chat_id, telegram_user_id, payload_ciphertext, attempts",
  ).bind(leaseHash).first();
  if (!job) return { job: null };
  try {
    const payload = await decryptPayload(job.payload_ciphertext, env);
    return {
      job: {
        job_id: job.job_id,
        chat_id: Number(job.chat_id),
        telegram_user_id: Number(job.telegram_user_id),
        payload,
        attempts: Number(job.attempts),
        lease_token: leaseToken,
      },
    };
  } catch {
    await env.DB.prepare(
      "UPDATE telegram_jobs SET state='pending', lease_until=0, lease_token_hash=NULL, updated_at=unixepoch() WHERE job_id=?",
    ).bind(job.job_id).run();
    throw new Error("queued payload could not be decrypted");
  }
}

async function bridgeComplete(env, payload) {
  const jobId = typeof payload.job_id === "string" ? payload.job_id : "";
  const leaseToken = typeof payload.lease_token === "string" ? payload.lease_token : "";
  const reply = typeof payload.reply === "string" ? payload.reply.slice(0, 4000) : "";
  if (!jobId || leaseToken.length < 32 || !reply) return json({ error: "invalid_completion" }, 400);
  const leaseHash = await sha256Hex(leaseToken);
  const claimed = await env.DB.prepare(
    "UPDATE telegram_jobs SET state='sending', updated_at=unixepoch() WHERE job_id=? AND state='leased' AND lease_token_hash=? AND lease_until >= unixepoch() RETURNING chat_id",
  ).bind(jobId, leaseHash).first();
  if (!claimed) return json({ error: "lease_not_valid" }, 409);
  try {
    await sendMessage(env, Number(claimed.chat_id), reply);
  } catch {
    await env.DB.prepare(
      "UPDATE telegram_jobs SET state='pending', lease_until=0, lease_token_hash=NULL, updated_at=unixepoch() WHERE job_id=? AND state='sending' AND lease_token_hash=?",
    ).bind(jobId, leaseHash).run();
    return json({ error: "telegram_delivery_failed" }, 503);
  }
  await env.DB.prepare(
    "UPDATE telegram_jobs SET state='done', lease_until=0, lease_token_hash=NULL, updated_at=unixepoch() WHERE job_id=? AND state='sending' AND lease_token_hash=?",
  ).bind(jobId, leaseHash).run();
  return json({ ok: true });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ status: "ok", service: "mr-siavash-el-ir-bot-edge" });
    }
    if (request.method === "GET" && url.pathname === "/ready") {
      const ready = validateTelegramEnvironment(env) === null;
      return json(
        { status: ready ? "ready" : "not_ready", service: "mr-siavash-el-ir-bot-edge" },
        ready ? 200 : 503,
      );
    }

    if (url.pathname === "/admin/register-webhook") {
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
      if (!env.WEBHOOK_SETUP_SECRET || String(env.WEBHOOK_SETUP_SECRET).length < 32) {
        return json({ error: "setup_not_configured" }, 503);
      }
      const authorization = request.headers.get("Authorization") || "";
      if (!constantTimeEqual(authorization, `Bearer ${env.WEBHOOK_SETUP_SECRET}`)) {
        return json({ error: "unauthorized" }, 401);
      }
      if (validateTelegramEnvironment(env)) return json({ error: "service_not_configured" }, 503);
      try {
        const info = await registerWebhook(env);
        return json({
          ok: true,
          webhook_registered: true,
          webhook_url: getTelegramWebhookUrl(env),
          pending_update_count: Number(info.pending_update_count || 0),
        });
      } catch (error) {
        console.error(JSON.stringify({
          event: "webhook_registration_failed",
          stage: error?.stage || "verification",
          kind: error?.kind || "unexpected",
          http_status: error?.http_status || null,
          telegram_error_code: error?.telegram_error_code || null,
          network_error_name: error?.network_error_name || null,
          network_error_code: error?.network_error_code || null,
        }));
        return json({ error: "webhook_registration_failed", stage: error?.stage || "verification", reason: error?.kind || "unexpected" }, 502);
      }
    }

    if (url.pathname === "/telegram/webhook") {
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
      const configError = validateTelegramEnvironment(env);
      if (configError) return json({ error: "service_not_configured" }, 503);
      const receivedSecret = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
      if (!constantTimeEqual(receivedSecret, env.TELEGRAM_WEBHOOK_SECRET)) {
        return json({ error: "unauthorized" }, 403);
      }
      const parsed = await readBoundedJson(request);
      if (!parsed.ok) return json({ error: "invalid_payload" }, parsed.status);
      const update = parsed.value;
      if (!Number.isSafeInteger(update.update_id) || update.update_id < 0) return json({ ok: true });
      let claim;
      try {
        claim = await claimUpdate(env, update.update_id);
        if (claim === "done") return json({ ok: true, duplicate: true });
        if (claim === "busy") return json({ error: "update_in_progress" }, 503);
        await handleTelegramMessage(env, update);
        await completeUpdate(env, update.update_id);
        return json({ ok: true });
      } catch (error) {
        if (claim === "claimed") {
          try { await releaseUpdate(env, update.update_id); } catch { /* lease expires */ }
        }
        console.error(JSON.stringify({ event: "telegram_update_failed", update_id: update.update_id, reason: String(error?.message || "unknown").slice(0, 100) }));
        return json({ error: "temporary_failure" }, 503);
      }
    }

    if (url.pathname === "/bridge/pull" || url.pathname === "/bridge/complete") {
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
      if (validateBridgeEnvironment(env) || !env.DB || typeof env.DB.prepare !== "function") {
        return json({ error: "service_not_configured" }, 503);
      }
      const parsed = await readBoundedJson(request);
      if (!parsed.ok) return json({ error: "invalid_payload" }, parsed.status);
      try {
        if (!(await bridgeSignatureValid(request, parsed.bytes, env))) return json({ error: "unauthorized" }, 401);
        if (url.pathname === "/bridge/pull") return json(await bridgePull(env));
        return await bridgeComplete(env, parsed.value);
      } catch (error) {
        console.error(JSON.stringify({ event: "bridge_request_failed", route: url.pathname, reason: String(error?.message || "unknown").slice(0, 100) }));
        return json({ error: "temporary_failure" }, 503);
      }
    }

    return json({ error: "not_found" }, 404);
  },

  async scheduled(_event, env) {
    console.log(JSON.stringify({ event: "scheduled_handler_started", cron: _event?.cron || null }));
    if (!env.DB) {
      console.error(JSON.stringify({ event: "scheduled_handler_missing_d1" }));
      return;
    }
    const webhookUrl = getTelegramWebhookUrl(env);
    if (!webhookUrl) {
      console.error(JSON.stringify({
        event: "scheduled_handler_invalid_webhook_url"
      }));
      return;
    }
    await Promise.all([
      env.DB.prepare("DELETE FROM telegram_processed_updates WHERE state='done' AND processed_at < unixepoch()-604800").run(),
      env.DB.prepare("DELETE FROM telegram_jobs WHERE state='done' AND updated_at < unixepoch()-604800").run(),
      env.DB.prepare("DELETE FROM bridge_nonces WHERE expires_at < unixepoch()").run(),
    ]);
    try {
      const info = await telegramCall(env, "getWebhookInfo", {});
      if (info?.url !== webhookUrl) {
        await registerWebhook(env);
        console.log(JSON.stringify({ event: "telegram_webhook_auto_registered" }));
      } else {
        console.log(JSON.stringify({ event: "telegram_webhook_already_registered" }));
      }
    } catch (error) {
      console.error(JSON.stringify({
        event: "telegram_webhook_auto_registration_failed",
        stage: error?.stage || "verification",
        kind: error?.kind || "unexpected",
        http_status: error?.http_status || null,
        telegram_error_code: error?.telegram_error_code || null,
        network_error_name: error?.network_error_name || null,
        network_error_code: error?.network_error_code || null
      }));
    }
  },
};
