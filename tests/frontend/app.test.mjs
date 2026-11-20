import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const root = new URL("../../backend/src/degreeplan/frontend/", import.meta.url);
const modelSource = await readFile(new URL("model.js", root), "utf8");
const model = await import(`data:text/javascript;base64,${Buffer.from(modelSource).toString("base64")}`);
const appSource = await readFile(new URL("app.js", root), "utf8");

// This is a small DOM fixture, not a real-browser substitute. The smoke script covers browser behavior.
class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.listeners = new Map();
    this.attributes = {};
    this.value = "";
    this.disabled = false;
    this.style = {};
    this.classList = { toggle() {} };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  querySelectorAll(selector) {
    const tags = selector.split(",").map((tag) => tag.trim());
    return this.children.flatMap((child) =>
      [...(tags.includes(child.tag) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  setCustomValidity(value) { this.validityMessage = value; }
  reportValidity() { return !this.querySelectorAll("input").some((item) => item.validityMessage); }
  reset() { this.querySelectorAll("input").forEach((input) => { input.value = ""; }); }
  focus() {}
}

function harness(overrides = {}) {
  const elements = new Map();
  const document = {
    createElement: (tag) => new Element(tag),
    getElementById(id) {
      if (elements.has(id)) return elements.get(id);
      return [...elements.values()].flatMap((element) =>
        element.querySelectorAll("form, details, summary, input, select, button, p"))
        .find((element) => element.id === id);
    },
    querySelectorAll: () => [],
  };
  const api = {
    restoreSession: async () => null,
    registration: {
      options: async () => ({ enabled: true, password_min_length: 12, programs: [] }),
      create: async () => ({ ok: true }),
    },
    plans: { list: async () => ({ items: [] }) },
    progress: {
      list: async () => ({ items: [] }),
      summary: async () => ({ gpa: null, graded_credits: 0, quality_points: 0, graded_courses: 0 }),
    },
    ...overrides,
  };
  const navigation = [];
  const context = {
    ...model, api, document, URLSearchParams, URL,
    configureApi() {},
    location: {
      pathname: "/degree.html", search: "", origin: "https://example.test",
      replace: (destination) => navigation.push(destination),
    },
    history: { replaceState() {} },
  };
  vm.createContext(context);
  vm.runInContext(appSource.replace(/^import .+;\r?$/gm, "").replace(/\bmain\(\);\s*$/, "")
    + "\nglobalThis.subject = { state, params, loginDestination, refresh, renderGpa, renderDegreeList, addPasswordForm, registrationPage };", context);
  function element(id, tag = "div", value = "") {
    const target = new Element(tag); target.id = id; target.value = value;
    elements.set(id, target);
    return target;
  }
  return { ...context.subject, api, element, document, navigation };
}

test("GPA cards display the server snapshot even when plan requirements differ", () => {
  const h = harness();
  for (const id of ["dashboardGPA", "degreeGPA", "summaryGPA", "dashboardGradedCredits",
    "degreeGPACredits", "dashboardQualityPoints", "degreeQualityPoints", "summaryGPADescription"]) h.element(id);
  h.state.program = { requirements: [] };
  h.state.summary = { gpa: 2.86, graded_credits: 7, quality_points: 20, graded_courses: 2 };
  h.renderGpa();
  assert.equal(h.document.getElementById("degreeGPA").textContent, "2.86");
  assert.equal(h.document.getElementById("dashboardGPA").textContent, "2.86");
  assert.equal(h.document.getElementById("degreeQualityPoints").textContent, "20.00");
  assert.match(h.document.getElementById("summaryGPADescription").textContent, /not just this plan/);
});

test("summary fetch failures retain the entire previous snapshot", async () => {
  const h = harness();
  h.state.user = { role: "student", profile: { program_id: 1 } };
  const oldProgress = [{ course_id: 1, status: "Completed", grade: "A" }];
  const oldSummary = { gpa: 4, graded_credits: 4 };
  h.state.progress = oldProgress; h.state.summary = oldSummary;
  h.api.progress.summary = async () => { throw new Error("summary unavailable"); };
  await assert.rejects(h.refresh(), /summary unavailable/);
  assert.equal(h.state.progress, oldProgress);
  assert.equal(h.state.summary, oldSummary);
});

test("grade selectors retain non-completed saved grades and staff has no editing controls", () => {
  const h = harness();
  h.element("degreeSearch", "input");
  h.element("degreeCategoryFilter", "select", "all");
  h.element("degreeStatusFilter", "select", "all");
  const list = h.element("degreeRequirementsList");
  h.element("degreeResultCount"); h.element("degreeEmptyState");
  h.state.user = { role: "student", profile: {} };
  h.state.program = { requirements: [{ id: 1, code: "<img src=x>", title: "<script>", credits: 4 }] };
  h.state.progress = [{ course_id: 1, status: "In Progress", grade: "F" }];
  h.renderDegreeList();
  const grade = list.querySelectorAll("select").find((item) => item.id === "grade-1");
  assert.equal(grade.value, "F");
  assert.deepEqual(grade.children.map((item) => item.value), ["", "A", "B", "C", "D", "F"]);
  assert.equal(grade.attributes["aria-label"], "Self-reported grade for <img src=x>");
  assert.equal(list.children[0].children[0].children[0].textContent, "<img src=x>");
  h.state.user = { role: "teacher" };
  h.renderDegreeList();
  assert.equal(list.querySelectorAll("select").length, 0);
  assert.match(list.querySelectorAll("span").at(-1).textContent, /Self-reported grade: F/);
});

test("password form validates confirmation, uses the client and clears all password fields", async () => {
  const h = harness();
  const account = h.element("account");
  h.addPasswordForm(account);
  const current = h.document.getElementById("currentPassword");
  const next = h.document.getElementById("newPassword");
  const confirm = h.document.getElementById("confirmPassword");
  const form = h.document.getElementById("passwordChangeForm");
  const feedback = h.document.getElementById("passwordChangeMessage");
  assert.equal(current.autocomplete, "current-password");
  assert.equal(next.autocomplete, "new-password");
  assert.equal(current.type, "password");
  let calls = 0;
  h.api.changePassword = async (old, replacement) => {
    calls++;
    assert.equal(old, "test-current");
    assert.equal(replacement, "test-new");
    throw new Error("<unsafe server text>");
  };
  current.value = "test-current"; next.value = "test-new"; confirm.value = "different";
  await form.listeners.get("submit")({ preventDefault() {} });
  assert.equal(calls, 0);
  assert.equal(confirm.validityMessage, "New passwords do not match.");
  confirm.value = "test-new";
  await form.listeners.get("submit")({ preventDefault() {} });
  assert.equal(calls, 1);
  assert.equal(feedback.textContent, "<unsafe server text>");
  assert.equal(current.value, "");
  assert.equal(next.value, "");
  assert.equal(confirm.value, "");
  assert.equal(h.state.busy, false);
});

function registrationFixture() {
  const h = harness();
  const form = h.element("registrationForm", "form"); form.hidden = true;
  for (const id of ["registrationUsername", "registrationFirstName", "registrationLastName",
    "registrationPassword", "registrationConfirmPassword"]) form.append(h.element(id, "input"));
  form.append(h.element("registrationProgram", "select"));
  form.append(new Element("button"));
  for (const id of ["registrationStatus", "registrationError", "registrationPasswordHelp"]) h.element(id, "p");
  return { ...h, form };
}

test("registration loads only minimal program choices and renders names literally", async () => {
  const h = registrationFixture();
  h.api.registration.options = async () => ({
    enabled: true, password_min_length: 14, programs: [{ id: 3, code: "CS", name: "<unsafe>" }],
  });
  await h.registrationPage();
  assert.equal(h.form.hidden, false);
  assert.equal(h.document.getElementById("registrationPassword").minLength, 14);
  assert.equal(h.document.getElementById("registrationProgram").children[0].textContent, "CS — <unsafe>");
});

test("registration stays closed when disabled or options fail", async () => {
  const h = registrationFixture();
  h.api.registration.options = async () => ({ enabled: false });
  await h.registrationPage();
  assert.equal(h.form.hidden, true);
  assert.match(h.document.getElementById("registrationStatus").textContent, /disabled/);
  const failing = registrationFixture();
  failing.api.registration.options = async () => { throw new Error("Options unavailable"); };
  await failing.registrationPage();
  assert.equal(failing.form.hidden, true);
  assert.equal(failing.document.getElementById("registrationError").textContent, "Options unavailable");
});

test("registration confirms passwords, preserves whitespace, clears secrets and returns to sign in", async () => {
  const h = registrationFixture();
  await h.registrationPage();
  const get = h.document.getElementById.bind(h.document);
  get("registrationUsername").value = " new-user ";
  get("registrationFirstName").value = " First ";
  get("registrationLastName").value = " Last ";
  get("registrationProgram").value = "3";
  get("registrationPassword").value = "  test-only-value  ";
  get("registrationConfirmPassword").value = "different";
  const submitted = [];
  h.api.registration.create = async (body) => { submitted.push(body); return { ok: true }; };
  await h.form.listeners.get("submit")({ preventDefault() {} });
  assert.equal(submitted.length, 0);
  assert.match(get("registrationConfirmPassword").validityMessage, /do not match/);
  get("registrationConfirmPassword").value = "  test-only-value  ";
  get("registrationPassword").listeners.get("input")();
  assert.equal(get("registrationConfirmPassword").validityMessage, "");
  await h.form.listeners.get("submit")({ preventDefault() {} });
  assert.deepEqual(JSON.parse(JSON.stringify(submitted[0])), {
    username: "new-user", first_name: "First", last_name: "Last", program_id: 3, password: "  test-only-value  ",
  });
  assert.equal(get("registrationPassword").value, "");
  assert.equal(get("registrationConfirmPassword").value, "");
  assert.deepEqual(h.navigation, ["login.html?registered=1"]);
});

test("registration reports errors as text, clears passwords and blocks an already signed-in user", async () => {
  const h = registrationFixture();
  await h.registrationPage();
  h.api.registration.create = async () => { throw new Error("<unsafe duplicate>"); };
  await h.form.listeners.get("submit")({ preventDefault() {} });
  assert.equal(h.document.getElementById("registrationError").textContent, "<unsafe duplicate>");
  assert.equal(h.document.getElementById("registrationPassword").value, "");
  assert.equal(h.state.busy, false);
  const signedIn = registrationFixture();
  signedIn.api.restoreSession = async () => ({ role: "teacher" });
  await signedIn.registrationPage();
  assert.equal(signedIn.form.hidden, true);
  assert.deepEqual(signedIn.navigation, ["/index.html"]);
});

test("administrators land in management but login redirects cannot escape the application", () => {
  const h = harness();
  assert.equal(h.loginDestination({ role: "admin" }), "/admin.html");
  assert.equal(h.loginDestination({ role: "student" }), "/index.html");
  h.params.set("next", "https://foreign.example/admin.html");
  assert.equal(h.loginDestination({ role: "admin" }), "/admin.html");
  h.params.set("next", "/admin.html");
  assert.equal(h.loginDestination({ role: "teacher" }), "/index.html");
  assert.equal(h.loginDestination({ role: "admin" }), "/admin.html");
});
