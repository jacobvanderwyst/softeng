import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../../backend/src/degreeplan/frontend/api-client.js", import.meta.url), "utf8");
const { api, ApiError, configureApi } =
  await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const response = (body, status = 200, headers = {}) =>
  new Response(status === 204 ? null : JSON.stringify(body), { status, headers });

test("browser API contract", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  let calls = [];
  let result = response({ user: { username: "student", role: "student" }, csrf_token: "test-csrf" });
  globalThis.fetch = async (url, options) => { calls.push({ url, ...options }); return result; };

  await t.test("login uses cookies and acquires an in-memory CSRF token", async () => {
    await api.login("student", "generated-by-test");
    assert.equal(calls.at(-1).credentials, "include");
    assert.equal(calls.at(-1).headers["X-CSRF-Token"], undefined);
    assert.equal(calls.at(-1).headers["Content-Type"], "application/json");
  });
  await t.test("writes include CSRF and match semester/progress endpoints", async () => {
    result = response({ ok: true });
    await api.plans.setSemester(5, 2, "Fall 2027");
    assert.equal(calls.at(-1).url, "/api/v1/plans/5/semesters");
    assert.deepEqual(JSON.parse(calls.at(-1).body), { term_index: 2, name: "Fall 2027" });
    assert.equal(calls.at(-1).headers["X-CSRF-Token"], "test-csrf");
    await api.progress.set(4, "Completed");
    assert.equal(calls.at(-1).url, "/api/v1/progress/4");
    assert.deepEqual(JSON.parse(calls.at(-1).body), { status: "Completed" });
    result = response(null, 204);
    assert.equal(await api.plans.removeSemester(5, 2), null);
  });
  await t.test("query parameters encode search and staff selection", async () => {
    result = response({ items: [] });
    await api.courses.list({ q: "C++ & math", limit: 100, offset: 100 });
    const url = new URL(calls.at(-1).url, "https://localhost");
    assert.equal(url.searchParams.get("q"), "C++ & math");
    assert.equal(url.searchParams.get("offset"), "100");
    await api.progress.list(7);
    assert.equal(calls.at(-1).url, "/api/v1/progress?student_id=7");
  });
  await t.test("structured errors expose validation and retry information", async () => {
    result = response({ error: { code: "too_many_requests", message: "Wait", request_id: "req-1" } },
      429, { "Retry-After": "30" });
    await assert.rejects(api.courses.list(), (error) =>
      error instanceof ApiError && error.status === 429 && error.retryAfter === 30 && error.requestId === "req-1");
  });
  await t.test("only protected 401s trigger the sign-in hook", async () => {
    let expired = 0;
    configureApi({ onUnauthorized: () => { expired++; } });
    result = response({ error: { message: "Sign in" } }, 401);
    assert.equal(await api.restoreSession(), null);
    await assert.rejects(api.progress.set(1, "Completed"), ApiError);
    assert.equal(calls.at(-1).headers["X-CSRF-Token"], undefined);
    expired = 0;
    await assert.rejects(api.login("student", "invalid"), ApiError);
    assert.equal(expired, 0);
    await assert.rejects(api.plans.list(), ApiError);
    assert.equal(expired, 1);
  });
  await t.test("logout clears the CSRF token", async () => {
    result = response({ user: {}, csrf_token: "fresh-test-token" });
    await api.restoreSession();
    result = response({ ok: true });
    await api.logout();
    await api.progress.set(1, "Not Started");
    assert.equal(calls.at(-1).headers["X-CSRF-Token"], undefined);
  });
});
