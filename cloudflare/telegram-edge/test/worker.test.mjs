import assert from "node:assert/strict";
import test from "node:test";
import { createHash, createHmac, randomUUID } from "node:crypto";

import worker from "../src/index.js";

const WEBHOOK_URL =
  "https://mr-siavash-el-ai-edge-dev.mrsiavashirbot.workers.dev/telegram/webhook";

function createEnv() {
  return {
    TELEGRAM_BOT_TOKEN: "123456789:TEST_TOKEN_VALUE",
    TELEGRAM_WEBHOOK_SECRET: "W".repeat(32),
    TELEGRAM_WEBHOOK_URL: WEBHOOK_URL,
    WEBHOOK_SETUP_SECRET: "setup-secret-for-unit-tests-only-123456",
    ALLOWED_TELEGRAM_USER_ID: "123456789",
    DB: {
      prepare() {
        throw new Error("D1 should not be queried by webhook registration");
      },
    },
  };
}



function createMemoryD1() {
  const processedUpdates = new Map();
  const jobs = new Map();
  const nonces = new Map();
  const now = () => Math.floor(Date.now() / 1000);

  const DB = {
    prepare(sql) {
      const query = sql.replace(/\s+/g, " ").trim();

      return {
        bind(...values) {
          return {
            async first() {
              if (query.startsWith("INSERT INTO telegram_processed_updates")) {
                const [updateId] = values;
                const existing = processedUpdates.get(updateId);
                if (
                  !existing ||
                  (existing.state !== "done" && existing.lease_until < now())
                ) {
                  processedUpdates.set(updateId, {
                    update_id: updateId,
                    state: "processing",
                    lease_until: now() + 15,
                    processed_at: null,
                  });
                  return { update_id: updateId };
                }
                return null;
              }

              if (query.startsWith("SELECT state FROM telegram_processed_updates")) {
                const [updateId] = values;
                const existing = processedUpdates.get(updateId);
                return existing ? { state: existing.state } : null;
              }

              if (query.startsWith("INSERT INTO bridge_nonces")) {
                const [nonceHash] = values;
                if (nonces.has(nonceHash)) return null;
                nonces.set(nonceHash, now() + 120);
                return { nonce_hash: nonceHash };
              }

              if (query.startsWith("UPDATE telegram_jobs SET state='leased'")) {
                const [leaseHash] = values;
                const job = [...jobs.values()]
                  .filter(item =>
                    item.state === "pending" ||
                    (["leased", "sending"].includes(item.state) && item.lease_until < now()),
                  )
                  .sort((left, right) => left.created_at - right.created_at)[0];

                if (!job) return null;
                job.state = "leased";
                job.lease_until = now() + 60;
                job.lease_token_hash = leaseHash;
                job.attempts += 1;
                job.updated_at = now();
                return {
                  job_id: job.job_id,
                  chat_id: job.chat_id,
                  telegram_user_id: job.telegram_user_id,
                  payload_ciphertext: job.payload_ciphertext,
                  attempts: job.attempts,
                };
              }

              if (query.startsWith("UPDATE telegram_jobs SET state='sending'")) {
                const [jobId, leaseHash] = values;
                const job = jobs.get(jobId);
                if (
                  !job ||
                  job.state !== "leased" ||
                  job.lease_token_hash !== leaseHash ||
                  job.lease_until < now()
                ) {
                  return null;
                }
                job.state = "sending";
                job.updated_at = now();
                return { chat_id: job.chat_id };
              }

              throw new Error(`Unsupported test D1 first() query: ${query}`);
            },

            async run() {
              if (query.startsWith("UPDATE telegram_processed_updates SET state='done'")) {
                const [updateId] = values;
                const row = processedUpdates.get(updateId);
                if (row) Object.assign(row, { state: "done", lease_until: 0, processed_at: now() });
                return { meta: { changes: row ? 1 : 0 } };
              }

              if (query.startsWith("UPDATE telegram_processed_updates SET state='retry'")) {
                const [updateId] = values;
                const row = processedUpdates.get(updateId);
                if (row?.state === "processing") Object.assign(row, { state: "retry", lease_until: now() });
                return { meta: { changes: row ? 1 : 0 } };
              }

              if (query.startsWith("INSERT OR IGNORE INTO telegram_jobs")) {
                const [jobId, updateId, chatId, userId, ciphertext] = values;
                const alreadyQueued = [...jobs.values()].some(job => job.update_id === updateId);
                const activeCount = [...jobs.values()].filter(job =>
                  ["pending", "leased", "sending"].includes(job.state),
                ).length;
                if (alreadyQueued || activeCount >= 1000) return { meta: { changes: 0 } };
                jobs.set(jobId, {
                  job_id: jobId,
                  update_id: updateId,
                  chat_id: chatId,
                  telegram_user_id: userId,
                  payload_ciphertext: ciphertext,
                  state: "pending",
                  lease_until: 0,
                  lease_token_hash: null,
                  attempts: 0,
                  created_at: now(),
                  updated_at: now(),
                });
                return { meta: { changes: 1 } };
              }

              if (query.startsWith("UPDATE telegram_jobs SET state='pending'")) {
                const [jobId, leaseHash] = values;
                const job = jobs.get(jobId);
                if (
                  job &&
                  (values.length === 1 ||
                    (job.state === "sending" && job.lease_token_hash === leaseHash))
                ) {
                  Object.assign(job, {
                    state: "pending",
                    lease_until: 0,
                    lease_token_hash: null,
                    updated_at: now(),
                  });
                  return { meta: { changes: 1 } };
                }
                return { meta: { changes: 0 } };
              }

              if (query.startsWith("UPDATE telegram_jobs SET state='done'")) {
                const [jobId, leaseHash] = values;
                const job = jobs.get(jobId);
                if (job?.state === "sending" && job.lease_token_hash === leaseHash) {
                  Object.assign(job, {
                    state: "done",
                    lease_until: 0,
                    lease_token_hash: null,
                    updated_at: now(),
                  });
                  return { meta: { changes: 1 } };
                }
                return { meta: { changes: 0 } };
              }

              throw new Error(`Unsupported test D1 run() query: ${query}`);
            },
          };
        },
      };
    },
  };

  return { DB, processedUpdates, jobs, nonces };
}

async function signedBridgeRequest(env, path, body, options = {}) {
  const bodyText = JSON.stringify(body);
  const timestamp = options.timestamp || String(Math.floor(Date.now() / 1000));
  const nonce = options.nonce || `nonce-${randomUUID()}`;
  const bodyHash = createHash("sha256").update(bodyText).digest("hex");
  const message = [timestamp, nonce, "POST", path, bodyHash].join("\n");
  const signature = createHmac("sha256", env.BRIDGE_SHARED_SECRET)
    .update(message)
    .digest("hex");

  return {
    timestamp,
    nonce,
    request: new Request(`https://edge.test${path}`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "X-Bridge-Timestamp": timestamp,
        "X-Bridge-Nonce": nonce,
        "X-Bridge-Signature": signature,
      },
      body: bodyText,
    }),
  };
}

function registrationRequest(env) {
  return new Request(
    "https://edge.test/admin/register-webhook",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.WEBHOOK_SETUP_SECRET}`,
      },
    },
  );
}

async function withRuntimeMocks({ fetchImpl, logImpl }, callback) {
  const previousFetch = globalThis.fetch;
  const previousError = console.error;
  const previousLog = console.log;

  globalThis.fetch = fetchImpl;
  console.error = logImpl;
  console.log = logImpl;

  try {
    return await callback();
  } finally {
    globalThis.fetch = previousFetch;
    console.error = previousError;
    console.log = previousLog;
  }
}

test("webhook registration logs allowlisted network cause metadata without secrets", async () => {
  const env = createEnv();
  const logs = [];
  const cause = Object.assign(new Error("do not log this message"), {
    code: "EAI_AGAIN",
  });

  await withRuntimeMocks({
    fetchImpl: async () => {
      throw new TypeError("fetch failed", { cause });
    },
    logImpl: (...parts) => logs.push(parts.map(String).join(" ")),
  }, async () => {
    const response = await worker.fetch(registrationRequest(env), env);
    const body = await response.json();

    assert.equal(response.status, 502);
    assert.equal(body.error, "webhook_registration_failed");
    assert.equal(body.stage, "setWebhook");
    assert.equal(body.reason, "network");
  });

  const diagnosticLine = logs.find(line => line.includes("webhook_registration_failed"));
  assert.ok(diagnosticLine, "expected a structured webhook failure event");
  const diagnostic = JSON.parse(diagnosticLine);

  assert.equal(diagnostic.stage, "setWebhook");
  assert.equal(diagnostic.kind, "network");
  assert.equal(diagnostic.network_error_name, "Error");
  assert.equal(diagnostic.network_error_code, "EAI_AGAIN");

  const serialized = JSON.stringify(diagnostic);
  assert.equal(serialized.includes(env.TELEGRAM_BOT_TOKEN), false);
  assert.equal(serialized.includes(env.TELEGRAM_WEBHOOK_SECRET), false);
  assert.equal(serialized.includes(env.WEBHOOK_SETUP_SECRET), false);
  assert.equal(serialized.includes("do not log this message"), false);
});

test("webhook registration omits unrecognized network cause codes", async () => {
  const env = createEnv();
  const logs = [];
  const cause = Object.assign(new Error("sensitive provider detail"), {
    code: "TELEGRAM_BOT_TOKEN_SECRET",
  });

  await withRuntimeMocks({
    fetchImpl: async () => {
      throw new TypeError("fetch failed", { cause });
    },
    logImpl: (...parts) => logs.push(parts.map(String).join(" ")),
  }, async () => {
    const response = await worker.fetch(registrationRequest(env), env);
    assert.equal(response.status, 502);
  });

  const diagnosticLine = logs.find(line => line.includes("webhook_registration_failed"));
  assert.ok(diagnosticLine);
  const diagnostic = JSON.parse(diagnosticLine);

  assert.equal(diagnostic.network_error_code, null);
  assert.equal(JSON.stringify(diagnostic).includes("TELEGRAM_BOT_TOKEN_SECRET"), false);
  assert.equal(JSON.stringify(diagnostic).includes("sensitive provider detail"), false);
});

test("webhook registration verifies Telegram's read-back before returning success", async () => {
  const env = createEnv();
  const requests = [];

  await withRuntimeMocks({
    fetchImpl: async (input, init) => {
      const url = String(input);
      requests.push({ url, body: JSON.parse(init.body) });

      if (url.endsWith("/setWebhook")) {
        return Response.json({ ok: true, result: true });
      }

      if (url.endsWith("/getWebhookInfo")) {
        return Response.json({
          ok: true,
          result: {
            url: WEBHOOK_URL,
            pending_update_count: 0,
          },
        });
      }

      throw new Error("unexpected Telegram API method");
    },
    logImpl: () => {},
  }, async () => {
    const response = await worker.fetch(registrationRequest(env), env);
    const body = await response.json();

    assert.equal(response.status, 200);
    assert.equal(body.webhook_registered, true);
    assert.equal(body.webhook_url, WEBHOOK_URL);
    assert.equal(body.pending_update_count, 0);
  });

  assert.equal(requests.length, 2);
  assert.ok(requests[0].url.endsWith("/setWebhook"));
  assert.ok(requests[1].url.endsWith("/getWebhookInfo"));
  assert.equal(requests[0].body.url, WEBHOOK_URL);
  assert.equal(requests[0].body.drop_pending_updates, false);
});

test("queues allowlisted owner Hub commands and rejects bridge-only unsupported commands", async () => {
  const env = createEnv();
  const d1 = createMemoryD1();
  env.DB = d1.DB;
  env.BRIDGE_SHARED_SECRET = "bridge-shared-secret-for-tests-only-0123456789";
  env.QUEUE_ENCRYPTION_KEY = Buffer.alloc(32, 7).toString("base64url");

  const allowedUpdate = {
    update_id: 51002,
    message: {
      from: { id: 123456789 },
      chat: { id: 123456789, type: "private" },
      text: "/memory بررسی طرح‌های قبلی",
    },
  };
  const allowedResponse = await worker.fetch(
    new Request("https://edge.test/telegram/webhook", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "X-Telegram-Bot-Api-Secret-Token": env.TELEGRAM_WEBHOOK_SECRET,
      },
      body: JSON.stringify(allowedUpdate),
    }),
    env,
  );

  assert.equal(allowedResponse.status, 200);
  assert.equal(d1.jobs.size, 1);
  const storedJob = [...d1.jobs.values()][0];
  assert.equal(storedJob.payload_ciphertext.includes("memory"), false);
  assert.equal(storedJob.payload_ciphertext.includes("بررسی"), false);

  const pullAuth = await signedBridgeRequest(env, "/bridge/pull", {});
  const pullResponse = await worker.fetch(pullAuth.request, env);
  assert.equal(pullResponse.status, 200);
  const pulled = await pullResponse.json();
  assert.deepEqual(pulled.job.payload, {
    command: "dispatch",
    text: "/memory بررسی طرح‌های قبلی",
  });

  const unsupportedUpdate = {
    update_id: 51003,
    message: {
      from: { id: 123456789 },
      chat: { id: 123456789, type: "private" },
      text: "/taskwatch 12345678-1234-4234-8234-123456789abc",
    },
  };
  const telegramRequests = [];
  await withRuntimeMocks({
    fetchImpl: async (input, init) => {
      telegramRequests.push({ url: String(input), payload: JSON.parse(init.body) });
      return Response.json({ ok: true, result: { message_id: 78 } });
    },
    logImpl: () => {},
  }, async () => {
    const unsupportedResponse = await worker.fetch(
      new Request("https://edge.test/telegram/webhook", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "X-Telegram-Bot-Api-Secret-Token": env.TELEGRAM_WEBHOOK_SECRET,
        },
        body: JSON.stringify(unsupportedUpdate),
      }),
      env,
    );
    assert.equal(unsupportedResponse.status, 200);
  });

  assert.equal(d1.jobs.size, 1, "background-notification commands must not enter the bridge queue");
  assert.equal(telegramRequests.length, 1);
  assert.equal(telegramRequests[0].payload.text.includes("پشتیبانی نمی‌شود"), true);
});

test("queues the read-only doctor command for the configured owner", async () => {
  const env = createEnv();
  const d1 = createMemoryD1();
  env.DB = d1.DB;
  env.BRIDGE_SHARED_SECRET = "bridge-shared-secret-for-tests-only-0123456789";
  env.QUEUE_ENCRYPTION_KEY = Buffer.alloc(32, 7).toString("base64url");

  const response = await worker.fetch(
    new Request("https://edge.test/telegram/webhook", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "X-Telegram-Bot-Api-Secret-Token": env.TELEGRAM_WEBHOOK_SECRET,
      },
      body: JSON.stringify({
        update_id: 51005,
        message: {
          from: { id: 123456789 },
          chat: { id: 123456789, type: "private" },
          text: "/doctor",
        },
      }),
    }),
    env,
  );

  assert.equal(response.status, 200);
  assert.equal(d1.jobs.size, 1);
  const storedJob = [...d1.jobs.values()][0];
  assert.equal(storedJob.payload_ciphertext.includes("doctor"), false);

  const pullAuth = await signedBridgeRequest(env, "/bridge/pull", {});
  const pullResponse = await worker.fetch(pullAuth.request, env);
  assert.equal(pullResponse.status, 200);
  const pulled = await pullResponse.json();
  assert.deepEqual(pulled.job.payload, {
    command: "dispatch",
    text: "/doctor",
  });
});

test("help advertises supported owner commands and excludes unavailable bridge features", async () => {
  const env = createEnv();
  const database = createMemoryD1();
  assert.equal(typeof database.DB.prepare, "function");
  env.DB = database.DB;
  const telegramRequests = [];

  await withRuntimeMocks({
    fetchImpl: async (input, init) => {
      const url = String(input);
      const payload = JSON.parse(init.body);
      telegramRequests.push({ url, payload });
      assert.ok(url.endsWith("/sendMessage"));
      return Response.json({ ok: true, result: { message_id: 79 } });
    },
    logImpl: () => {},
  }, async () => {
    const response = await worker.fetch(
      new Request("https://edge.test/telegram/webhook", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "X-Telegram-Bot-Api-Secret-Token": env.TELEGRAM_WEBHOOK_SECRET,
        },
        body: JSON.stringify({
          update_id: 51004,
          message: {
            from: { id: 123456789 },
            chat: { id: 123456789, type: "private" },
            text: "/help",
          },
        }),
      }),
      env,
    );
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ok: true });
  });

  assert.equal(telegramRequests.length, 1);
  const help = telegramRequests[0].payload.text;
  for (const command of ["/doctor", "/memory", "/task", "/agentplan", "/taskplanrun", "/videoproviders"]) {
    assert.ok(help.includes(command), `help should advertise ${command}`);
  }
  for (const command of ["/taskwatch", "/taskplanwatch", "/videorenderdownload"]) {
    assert.equal(help.includes(command), false, `help must not advertise unavailable ${command}`);
  }
});

test("readiness rejects a missing or invalid environment-specific webhook URL", async () => {
  const invalidValues = [
    undefined,
    "not-a-url",
    "http://edge-dev.example.test/telegram/webhook",
    "https://edge-dev.example.test/wrong-path",
    "https://edge-dev.example.test/telegram/webhook?unexpected=1",
    "https://edge-dev.example.test/telegram/webhook?",
    "https://edge-dev.example.test/telegram/webhook#fragment",
    "https://edge-dev.example.test/telegram/webhook#",
  ];

  for (const value of invalidValues) {
    const env = createEnv();
    if (value === undefined) {
      delete env.TELEGRAM_WEBHOOK_URL;
    } else {
      env.TELEGRAM_WEBHOOK_URL = value;
    }

    const response = await worker.fetch(
      new Request("https://edge.test/ready"),
      env,
    );

    assert.equal(response.status, 503, `expected URL to be rejected: ${String(value)}`);
    assert.equal((await response.json()).status, "not_ready");
  }
});

test("queues an owner command, authenticates bridge pulls, and completes a fenced Telegram reply", async () => {
  const env = createEnv();
  const d1 = createMemoryD1();
  env.DB = d1.DB;
  env.BRIDGE_SHARED_SECRET = "bridge-shared-secret-for-tests-only-0123456789";
  env.QUEUE_ENCRYPTION_KEY = Buffer.alloc(32, 7).toString("base64url");

  const telegramRequests = [];
  await withRuntimeMocks({
    fetchImpl: async (input, init) => {
      const url = String(input);
      const payload = JSON.parse(init.body);
      telegramRequests.push({ url, payload });
      assert.ok(url.endsWith("/sendMessage"), "only the final Telegram reply should leave the Worker");
      return Response.json({ ok: true, result: { message_id: 77 } });
    },
    logImpl: () => {},
  }, async () => {
    const update = {
      update_id: 51001,
      message: {
        from: { id: 123456789 },
        chat: { id: 123456789, type: "private" },
        text: "/status",
      },
    };
    const webhookResponse = await worker.fetch(
      new Request("https://edge.test/telegram/webhook", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "X-Telegram-Bot-Api-Secret-Token": env.TELEGRAM_WEBHOOK_SECRET,
        },
        body: JSON.stringify(update),
      }),
      env,
    );

    assert.equal(webhookResponse.status, 200);
    assert.deepEqual(await webhookResponse.json(), { ok: true });
    assert.equal(d1.processedUpdates.get(51001).state, "done");
    assert.equal(d1.jobs.size, 1);

    const duplicateWebhookResponse = await worker.fetch(
      new Request("https://edge.test/telegram/webhook", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "X-Telegram-Bot-Api-Secret-Token": env.TELEGRAM_WEBHOOK_SECRET,
        },
        body: JSON.stringify(update),
      }),
      env,
    );
    assert.equal(duplicateWebhookResponse.status, 200);
    assert.deepEqual(await duplicateWebhookResponse.json(), { ok: true, duplicate: true });
    assert.equal(d1.jobs.size, 1, "a repeated Telegram update must not create another job");

    const storedJob = [...d1.jobs.values()][0];
    assert.equal(storedJob.state, "pending");
    assert.equal(storedJob.payload_ciphertext.includes("status"), false);
    assert.equal(storedJob.payload_ciphertext.includes("command"), false);

    const firstPullAuth = await signedBridgeRequest(env, "/bridge/pull", {});
    const firstPullResponse = await worker.fetch(firstPullAuth.request, env);
    assert.equal(firstPullResponse.status, 200);
    const firstPullBody = await firstPullResponse.json();
    assert.equal(firstPullBody.job.payload.command, "status");
    assert.equal(firstPullBody.job.job_id, storedJob.job_id);
    assert.equal(firstPullBody.job.chat_id, 123456789);
    assert.equal(firstPullBody.job.telegram_user_id, 123456789);
    assert.equal(firstPullBody.job.attempts, 1);
    assert.equal(typeof firstPullBody.job.lease_token, "string");
    assert.equal("payload_ciphertext" in firstPullBody.job, false);

    const replayAuth = await signedBridgeRequest(env, "/bridge/pull", {}, {
      timestamp: firstPullAuth.timestamp,
      nonce: firstPullAuth.nonce,
    });
    const replayResponse = await worker.fetch(replayAuth.request, env);
    assert.equal(replayResponse.status, 401, "a signed nonce must be single-use");

    const wrongLeaseAuth = await signedBridgeRequest(env, "/bridge/complete", {
      job_id: storedJob.job_id,
      lease_token: "wrong-lease-token-".repeat(4),
      reply: "This must not be delivered",
    });
    const wrongLeaseResponse = await worker.fetch(wrongLeaseAuth.request, env);
    assert.equal(wrongLeaseResponse.status, 409);
    assert.equal(telegramRequests.length, 0);

    storedJob.lease_until = 0;
    const expiredLeaseAuth = await signedBridgeRequest(env, "/bridge/complete", {
      job_id: storedJob.job_id,
      lease_token: firstPullBody.job.lease_token,
      reply: "An expired lease must not deliver",
    });
    const expiredLeaseResponse = await worker.fetch(expiredLeaseAuth.request, env);
    assert.equal(expiredLeaseResponse.status, 409);
    assert.equal(telegramRequests.length, 0);

    const secondPullAuth = await signedBridgeRequest(env, "/bridge/pull", {});
    const secondPullResponse = await worker.fetch(secondPullAuth.request, env);
    assert.equal(secondPullResponse.status, 200);
    const secondPullBody = await secondPullResponse.json();
    assert.equal(secondPullBody.job.attempts, 2);
    assert.notEqual(secondPullBody.job.lease_token, firstPullBody.job.lease_token);

    const completionAuth = await signedBridgeRequest(env, "/bridge/complete", {
      job_id: storedJob.job_id,
      lease_token: secondPullBody.job.lease_token,
      reply: "HUB is operational",
    });
    const completionResponse = await worker.fetch(completionAuth.request, env);
    assert.equal(completionResponse.status, 200);
    assert.deepEqual(await completionResponse.json(), { ok: true });
    assert.equal(storedJob.state, "done");
    assert.equal(storedJob.lease_token_hash, null);
    assert.equal(telegramRequests.length, 1);
    assert.deepEqual(telegramRequests[0].payload, {
      chat_id: 123456789,
      text: "HUB is operational",
      disable_web_page_preview: true,
    });

    const duplicateCompletionAuth = await signedBridgeRequest(env, "/bridge/complete", {
      job_id: storedJob.job_id,
      lease_token: secondPullBody.job.lease_token,
      reply: "This must not be sent twice",
    });
    const duplicateCompletionResponse = await worker.fetch(duplicateCompletionAuth.request, env);
    assert.equal(duplicateCompletionResponse.status, 409);
    assert.equal(telegramRequests.length, 1);

    const emptyPullAuth = await signedBridgeRequest(env, "/bridge/pull", {});
    const emptyPullResponse = await worker.fetch(emptyPullAuth.request, env);
    assert.equal(emptyPullResponse.status, 200);
    assert.deepEqual(await emptyPullResponse.json(), { job: null });
  });
});

test("does not enqueue commands from a Telegram user other than the configured owner", async () => {
  const env = createEnv();
  const d1 = createMemoryD1();
  env.DB = d1.DB;
  env.BRIDGE_SHARED_SECRET = "bridge-shared-secret-for-tests-only-0123456789";
  env.QUEUE_ENCRYPTION_KEY = Buffer.alloc(32, 7).toString("base64url");

  const response = await worker.fetch(
    new Request("https://edge.test/telegram/webhook", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "X-Telegram-Bot-Api-Secret-Token": env.TELEGRAM_WEBHOOK_SECRET,
      },
      body: JSON.stringify({
        update_id: 51002,
        message: {
          from: { id: 987654321 },
          chat: { id: 987654321, type: "private" },
          text: "/status",
        },
      }),
    }),
    env,
  );

  assert.equal(response.status, 200);
  assert.equal(d1.processedUpdates.get(51002).state, "done");
  assert.equal(d1.jobs.size, 0);
});

test("requeues a job after Telegram reply delivery fails and accepts a fresh lease retry", async () => {
  const env = createEnv();
  const d1 = createMemoryD1();
  env.DB = d1.DB;
  env.BRIDGE_SHARED_SECRET = "bridge-shared-secret-for-tests-only-0123456789";
  env.QUEUE_ENCRYPTION_KEY = Buffer.alloc(32, 7).toString("base64url");

  const telegramRequests = [];
  let failNextDelivery = true;
  await withRuntimeMocks({
    fetchImpl: async (input, init) => {
      telegramRequests.push({
        url: String(input),
        payload: JSON.parse(init.body),
      });
      if (failNextDelivery) {
        failNextDelivery = false;
        return Response.json({ ok: false, error_code: 503 }, { status: 503 });
      }
      return Response.json({ ok: true, result: { message_id: 78 } });
    },
    logImpl: () => {},
  }, async () => {
    const update = {
      update_id: 51003,
      message: {
        from: { id: 123456789 },
        chat: { id: 123456789, type: "private" },
        text: "/tasks",
      },
    };
    const webhookResponse = await worker.fetch(
      new Request("https://edge.test/telegram/webhook", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "X-Telegram-Bot-Api-Secret-Token": env.TELEGRAM_WEBHOOK_SECRET,
        },
        body: JSON.stringify(update),
      }),
      env,
    );
    assert.equal(webhookResponse.status, 200);

    const job = [...d1.jobs.values()][0];
    assert.ok(job);

    const firstPullAuth = await signedBridgeRequest(env, "/bridge/pull", {});
    const firstPullResponse = await worker.fetch(firstPullAuth.request, env);
    assert.equal(firstPullResponse.status, 200);
    const firstPull = await firstPullResponse.json();
    assert.equal(firstPull.job.attempts, 1);

    const failedCompletionAuth = await signedBridgeRequest(env, "/bridge/complete", {
      job_id: job.job_id,
      lease_token: firstPull.job.lease_token,
      reply: "Retry this response",
    });
    const failedCompletionResponse = await worker.fetch(failedCompletionAuth.request, env);
    assert.equal(failedCompletionResponse.status, 503);
    assert.deepEqual(await failedCompletionResponse.json(), { error: "telegram_delivery_failed" });
    assert.equal(job.state, "pending");
    assert.equal(job.lease_until, 0);
    assert.equal(job.lease_token_hash, null);
    assert.equal(telegramRequests.length, 1);

    const retryPullAuth = await signedBridgeRequest(env, "/bridge/pull", {});
    const retryPullResponse = await worker.fetch(retryPullAuth.request, env);
    assert.equal(retryPullResponse.status, 200);
    const retryPull = await retryPullResponse.json();
    assert.equal(retryPull.job.attempts, 2);
    assert.notEqual(retryPull.job.lease_token, firstPull.job.lease_token);

    const retryCompletionAuth = await signedBridgeRequest(env, "/bridge/complete", {
      job_id: job.job_id,
      lease_token: retryPull.job.lease_token,
      reply: "Retry this response",
    });
    const retryCompletionResponse = await worker.fetch(retryCompletionAuth.request, env);
    assert.equal(retryCompletionResponse.status, 200);
    assert.deepEqual(await retryCompletionResponse.json(), { ok: true });
    assert.equal(job.state, "done");
    assert.equal(telegramRequests.length, 2);
    assert.deepEqual(telegramRequests[1].payload, {
      chat_id: 123456789,
      text: "Retry this response",
      disable_web_page_preview: true,
    });
  });
});

test("rejects stale timestamps and tampered HMAC signatures before consuming bridge nonces", async () => {
  const env = createEnv();
  const d1 = createMemoryD1();
  env.DB = d1.DB;
  env.BRIDGE_SHARED_SECRET = "bridge-shared-secret-for-tests-only-0123456789";
  env.QUEUE_ENCRYPTION_KEY = Buffer.alloc(32, 7).toString("base64url");

  const stale = await signedBridgeRequest(env, "/bridge/pull", {}, {
    timestamp: String(Math.floor(Date.now() / 1000) - 61),
    nonce: "nonce-stale-timestamp-00000001",
  });
  const staleResponse = await worker.fetch(stale.request, env);
  assert.equal(staleResponse.status, 401);
  assert.equal(d1.nonces.size, 0, "stale timestamps must be rejected before nonce storage");

  const valid = await signedBridgeRequest(env, "/bridge/pull", {}, {
    nonce: "nonce-tampered-signature-000001",
  });
  const tamperedHeaders = new Headers(valid.request.headers);
  tamperedHeaders.set("X-Bridge-Signature", "0".repeat(64));
  const tamperedRequest = new Request("https://edge.test/bridge/pull", {
    method: "POST",
    headers: tamperedHeaders,
    body: JSON.stringify({}),
  });
  const tamperedResponse = await worker.fetch(tamperedRequest, env);
  assert.equal(tamperedResponse.status, 401);
  assert.equal(d1.nonces.size, 0, "invalid signatures must be rejected before nonce storage");

  const legitimate = await signedBridgeRequest(env, "/bridge/pull", {}, {
    nonce: "nonce-legitimate-signature-00001",
  });
  const legitimateResponse = await worker.fetch(legitimate.request, env);
  assert.equal(legitimateResponse.status, 200);
  assert.deepEqual(await legitimateResponse.json(), { job: null });
  assert.equal(d1.nonces.size, 1, "only a valid, fresh signature may consume a nonce");
});

