// Minimal browser client for the degree plan API (plain ES module, no dependencies).
//
// What it handles:
//   * credentials: "include"  -> the session cookie is sent on every request
//   * the CSRF token          -> kept in memory (NEVER in localStorage) and sent as X-CSRF-Token on writes
//   * JSON bodies/headers, a uniform ApiError, and a single hook for "session expired" (401)
//
// Usage:
//   import { api, configureApi } from "./api-client.js";
//   configureApi({ baseUrl: "/api/v1", onUnauthorized: () => showLoginPage() });
//   const user = await api.restoreSession() ?? await api.login(username, password);   // username/password from the login form
//   const plan = await api.plans.create({ name: "My plan", program_id: 1 });
//   const result = await api.plans.setCourse(plan.id, { course_id: 1, term_index: 1 });   // result.issues lists problems

let baseUrl = "/api/v1";
let onUnauthorized = () => {};
let csrfToken = null; // lives only in memory: it is re-fetched with restoreSession() after a page reload

export function configureApi(options = {}) {
  if (options.baseUrl) baseUrl = options.baseUrl.replace(/\/$/, "");
  if (options.onUnauthorized) onUnauthorized = options.onUnauthorized;
}

export class ApiError extends Error {
  constructor(status, body) {
    const err = body && body.error ? body.error : {};
    super(err.message || `Request failed (${status})`);
    this.status = status; // 401, 403, 404, 409, 415, 422, 429, 503 ...
    this.code = err.code; // for example "validation_error", "too_many_requests"
    this.details = err.details; // validation: [{ field, message }]
    this.requestId = err.request_id; // quote this when reporting a problem: it matches the server logs
    this.retryAfter = null; // seconds, set for 429
  }
}

async function request(method, path, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && method !== "HEAD" && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  const response = await fetch(`${baseUrl}${path}`, {
    method,
    headers,
    credentials: "include",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 204) return null;
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!response.ok) {
    const error = new ApiError(response.status, payload);
    const retry = response.headers.get("Retry-After");
    if (retry) error.retryAfter = Number(retry);
    // A 401 on a protected call means the session ended (logout elsewhere, idle timeout, deactivation).
    // Not for login ("wrong username or password": the caller shows that) nor for /auth/me, which
    // restoreSession() uses on page load where "not logged in yet" is a normal answer.
    if (response.status === 401 && path !== "/auth/login" && path !== "/auth/me") {
      csrfToken = null;
      onUnauthorized(error);
    }
    throw error;
  }
  return payload;
}

const qs = (params) => {
  const clean = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return clean.length ? `?${new URLSearchParams(clean)}` : "";
};

export const api = {
  /** Log in. Returns the user: { username, role, profile }. */
  async login(username, password) {
    const data = await request("POST", "/auth/login", { username, password });
    csrfToken = data.csrf_token;
    return data.user;
  },

  /** Call on page load: returns the user if the cookie session is still valid, otherwise null. */
  async restoreSession() {
    try {
      const data = await request("GET", "/auth/me");
      csrfToken = data.csrf_token;
      return data.user;
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return null;
      throw error;
    }
  },

  async logout() {
    await request("POST", "/auth/logout");
    csrfToken = null;
  },

  changePassword: (currentPassword, newPassword) =>
    request("POST", "/auth/change-password", { current_password: currentPassword, new_password: newPassword }),

  courses: {
    list: ({ q, limit = 25, offset = 0 } = {}) => request("GET", `/courses${qs({ q, limit, offset })}`),
    get: (id) => request("GET", `/courses/${id}`), // includes prerequisites
  },

  programs: {
    list: () => request("GET", "/programs"),
    get: (id) => request("GET", `/programs/${id}`), // includes required courses
  },

  plans: {
    /** Students: their own plans. Teachers/admins: pass the student's id. */
    list: (studentId) => request("GET", `/plans${qs({ student_id: studentId })}`),
    get: (id) => request("GET", `/plans/${id}`),
    create: ({ name, program_id }) => request("POST", "/plans", { name, program_id }),
    rename: (id, name) => request("PATCH", `/plans/${id}`, { name }),
    remove: (id) => request("DELETE", `/plans/${id}`),
    /** Add a course to a term, or move it. Resolves to the plan plus `issues` (prerequisites, requirements...). */
    setCourse: (id, { course_id, term_index }) => request("PUT", `/plans/${id}/courses`, { course_id, term_index }),
    removeCourse: (id, courseId) => request("DELETE", `/plans/${id}/courses/${courseId}`),
    validate: (id) => request("GET", `/plans/${id}/validation`), // { valid, issues: [...] }
  },
};
