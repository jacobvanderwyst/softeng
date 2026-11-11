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
    plans: { list: async () => ({ items: [] }) },
    progress: {
      list: async () => ({ items: [] }),
      summary: async () => ({ gpa: null, graded_credits: 0, quality_points: 0, graded_courses: 0 }),
    },
    ...overrides,
  };
  const context = {
    ...model, api, document, URLSearchParams, URL,
    configureApi() {},
    location: { pathname: "/degree.html", search: "", origin: "https://example.test" },
    history: { replaceState() {} },
  };
  vm.createContext(context);
  vm.runInContext(appSource.replace(/^import .+;\r?$/gm, "").replace(/\bmain\(\);\s*$/, "")
    + "\nglobalThis.subject = { state, refresh, renderGpa, renderDegreeList, addPasswordForm };", context);
  function element(id, tag = "div", value = "") {
    const target = new Element(tag); target.id = id; target.value = value;
    elements.set(id, target);
    return target;
  }
  return { ...context.subject, api, element, document };
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
