import assert from "node:assert/strict";
import test from "node:test";
import { randomBytes } from "node:crypto";

import worker from "../src/index.js";

const OWNER_ID = 123456789;
const BRIDGE_SECRET = "bridge-development-secret-material-123456";

class InMemoryD1 {
  constructor() {
    this.processedUpdates = new Map();
    this.jobs = [];
    this.nonces = new Set();
  }

  prepare(statement) {
    const sql = statement.replace(/\\s+/g, " ").trim();
    return {
      bind: (...values) => ({
        first: () => this.first(sql, values),
        run: () => this.run(sql, values),
      }),
    };
  }

  async first(sql, values) {
    const now = Math.floor(Date.now() / 1000);

    if (sql.startsWith("INSERT INTO telegram_processed_updates")) {
      const updateId = values[0];
      const current = this.processedUpdates.get(updateId);
      if (!current || (current.state !== "done" && current.leaseUntil < now)) {
        this.processedUpdates.set(updateId, {
          state: "processing",
          leaseUntil: now + 15,
        });
        return { update_id: updateId };
      }
      return null;
    }

    if (sql.startsWith("SELECT state FROM telegram_processed_updates")) {
      const current = this.processedUpdates.get(values[0]);
      return current ? { state: current.state } : null;
    }

    if (sql.startsWith("SELECT job_id FROM telegram_jobs")) {
      const job = this.jobs.find((item) => item.update_id === values[0]);
      return job ? { job_id: job.job_id } : null;
    }

    if (sql.startsWith("INSERT INTO bridge_nonces")) {
      const nonceHash = values[0];
      if (this.nonces.has(nonceHash)) return null;
      this.nonces.add(nonceHash);
      return { nonce_hash: nonceHash };
    }

    if (sql.startsWith("UPDATE telegram_jobs SET state='leased'")) {
      const leaseHash = values[0];
      const job = this.jobs.find((item) =>
        item.state === "pending" ||
        ((item.state === "leased" || item.state === "sending") && item.leaseUntil < now)
      );
      if (!job) return null;
      job.state = "leased";
      job.leaseUntil = now + 60;
      job.lease_token_hash = leaseHash;
      job.attempts += 1;
      job.updated_at = now;
      return {
        job_id: job.job_id,
        chat_id: job.chat_id,
        telegram_user_id: job.telegram_user_id,
        payload_ciphertext: job.payload_ciphertext,
        attempts: job.attempts,
      };
    }

    if (sql.startsWith("UPDATE telegram_jobs SET state='sending'")) {
      const [jobId, leaseHash] = values;
      const job = this.jobs.find((item) => item.job_id === jobId);
      if (
        !job ||
        job.state !== "leased" ||
        job.lease_token_hash !== leaseHash ||
        job.leaseUntil < now
      ) {
        return null;
      }
      job.state = "sending";
      job.updated_at = now;
      return { chat_id: job.chat_id };
    }

    throw new Error("Unexpected D1 first() query: " + sql);
  }

  async run(sql, values) {
    const now = Math.floor(Date.now() / 1000);

    if (sql.startsWith("INSERT OR IGNORE INTO telegram_jobs")) {
      const [jobId, updateId, chatId, userId, ciphertext] = values;
      const duplicate = this.jobs.some((item) => item.update_id === updateId);
      const active = this.jobs.filter((item) =>
        item.state === "pending" || item.state === "leased" || item.state === "sending"
      ).length;
      if (duplicate || active >= 1000) return { meta: { changes: 0 } };
      this.jobs.push({
        job_id: jobId,
        update_id: updateId,
        chat_id: chatId,
        telegram_user_id: userId,
        payload_ciphertext: ciphertext,
        state: "pending",
        leaseUntil: 0,
        lease_token_hash: null,
        attempts: 0,
        created_at: now,
        updated_at: now,
      });
      return { meta: { changes: 1 } };
    }

    if (sql.startsWith("UPDATE telegram_processed_updates SET state='done'")) {
      const current = this.processedUpdates.get(values[0]);
      if (current) {
        current.state = "done";
        current.leaseUntil = 0;
      }
      return { meta: { changes: current ? 1 : 0 } };
    }

    if (sql.startsWith("UPDATE telegram_processed_updates SET state='retry'")) {
      const current = this.processedUpdates.get(values[0]);
      if (current && current.state === "processing") {
        current.state = "retry";
        current.leaseUntil = 0;
      }
      return { meta: { changes: current ? 1 : 0 } };
    }

    if (sql.startsWith("UPDATE telegram_jobs SET state='pending'")) {
      const [jobId, leaseHash] = values;
      const job = this.jobs.find((item) => item.job_id === jobId);
      if (job && (values.length === 1 || job.lease_token_hash === leaseHash)) {
        job.state = "pending";
        job.leaseUntil = 0;
        job.lease_token_hash = null;
        job.updated_at = now;
      }
      return { meta: { changes: job ? 1 : 0 } };
    }

    if (sql.startsWith("UPDATE telegram_jobs SET state='done'")) {
      const [jobId, leaseHash] = values;
      const job = this.jobs.find((item) => item.job_id === jobId);
      if (job && job.state === "sending" && job.lease_token_hash === leaseHash) {
        job.state = "done";
        job.leaseUntil = 0;
        job.lease_token_hash = null;
        job.updated_at = now;
      }
      return { meta: { changes: job ? 1 : 0 } };
    }

    throw new Error("Unexpected D1 run() query: " + sql);
  }
}

async function signedBridgeRequest(path, payload, nonce, secret = BRIDGE_SECRET) {
  const body = JSON.stringify(payload);
  const bodyBytes = new TextEncoder().encode(body);
  const digest = await crypto.subtle.digest("SHA-256", bodyBytes);
  const bodyHash = Array.from(new Uint8Array(digest), (value) =>
    value.toString(16).padStart(2, "0")
  ).join("");
  const timestamp = String(Math.floor(Date.now() / 1000));
  const message = [timestamp, nonce, "POST", path, bodyHash].join("\n");
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(message),
  );
  const signatureHex = Array.from(new Uint8Array(signature), (value) =>
    value.toString(16).padStart(2, "0")
  ).join("");

  return new Request("https://edge.test" + path, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "X-Bridge-Timestamp": timestamp,
      "X-Bridge-Nonce": nonce,
      "X-Bridge-Signature": signatureHex,
    },
    body,
  });
}

async function withTelegramFetchMock(callback) {
  const previousFetch = globalThis.fetch;
  const sentMessages = [];
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input));
    assert.equal(url.hostname, "api.telegram.org");
    assert.ok(url.pathname.endsWith("/sendMessage"));
    sentMessages.push(JSON.parse(init.body));
    return Response.json({ ok: true, result: { message_id: 991 } });
  };
  try {
    return await callback(sentMessages);
  } finally {
    globalThis.fetch = previousFetch;
  }
}

test("private owner update is deduplicated, encrypted, leased, and delivered through the signed bridge", async () => {
  const db = new InMemoryD1();
  const env = {
    TELEGRAM_BOT_TOKEN: "DUMMY_BOT_TOKEN_FOR_LOCAL_E2E_TESTS",
    TELEGRAM_WEBHOOK_SECRET: "W".repeat(32),
    TELEGRAM_WEBHOOK_URL: "https://edge.example.test/telegram/webhook",
    ALLOWED_TELEGRAM_USER_ID: String(OWNER_ID),
    BRIDGE_SHARED_SECRET: BRIDGE_SECRET,
    QUEUE_ENCRYPTION_KEY: randomBytes(32).toString("base64url"),
    DB: db,
  };

  await withTelegramFetchMock(async (sentMessages) => {
    const update = {
      update_id: 9001,
      message: {
        message_id: 42,
        from: { id: OWNER_ID },
        chat: { id: OWNER_ID, type: "private" },
        text: "/status",
      },
    };
    const webhookRequest = () => new Request(
      "https://edge.test/telegram/webhook",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "X-Telegram-Bot-Api-Secret-Token": env.TELEGRAM_WEBHOOK_SECRET,
        },
        body: JSON.stringify(update),
      },
    );

    const ingress = await worker.fetch(webhookRequest(), env);
    assert.equal(ingress.status, 200);
    assert.deepEqual(await ingress.json(), { ok: true });
    assert.equal(db.jobs.length, 1);
    assert.equal(db.jobs[0].state, "pending");
    assert.equal(db.jobs[0].payload_ciphertext.includes('"status"'), false);

    const duplicate = await worker.fetch(webhookRequest(), env);
    assert.equal(duplicate.status, 200);
    assert.deepEqual(await duplicate.json(), { ok: true, duplicate: true });
    assert.equal(db.jobs.length, 1);

    const pullResponse = await worker.fetch(
      await signedBridgeRequest("/bridge/pull", {}, "pull-nonce-00000000000001"),
      env,
    );
    assert.equal(pullResponse.status, 200);
    const pulled = await pullResponse.json();
    assert.equal(pulled.job.telegram_user_id, OWNER_ID);
    assert.equal(pulled.job.chat_id, OWNER_ID);
    assert.deepEqual(pulled.job.payload, { command: "status" });
    assert.match(pulled.job.lease_token, /^[a-f0-9]{64}$/);
    assert.equal(pulled.job.attempts, 1);
    assert.equal(db.jobs[0].state, "leased");

    const completion = await worker.fetch(
      await signedBridgeRequest(
        "/bridge/complete",
        {
          job_id: pulled.job.job_id,
          lease_token: pulled.job.lease_token,
          reply: "Hub owner status: ready",
        },
        "complete-nonce-00000000001",
      ),
      env,
    );
    assert.equal(completion.status, 200);
    assert.deepEqual(await completion.json(), { ok: true });
    assert.equal(db.jobs[0].state, "done");
    assert.equal(db.jobs[0].lease_token_hash, null);
    assert.deepEqual(sentMessages, [{
      chat_id: OWNER_ID,
      text: "Hub owner status: ready",
      disable_web_page_preview: true,
    }]);

    const replay = await worker.fetch(
      await signedBridgeRequest("/bridge/pull", {}, "pull-nonce-00000000000001"),
      env,
    );
    assert.equal(replay.status, 401);
    assert.equal(db.jobs[0].state, "done");
    assert.equal(sentMessages.length, 1);
  });
});
