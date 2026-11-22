import assert from "node:assert/strict";
import test from "node:test";

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

test("readiness rejects a missing or invalid environment-specific webhook URL", async () => {
  const invalidValues = [
    undefined,
    "not-a-url",
    "http://edge-dev.example.test/telegram/webhook",
    "https://edge-dev.example.test/wrong-path",
    "https://edge-dev.example.test/telegram/webhook?unexpected=1",
    "https://edge-dev.example.test/telegram/webhook#fragment",
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
