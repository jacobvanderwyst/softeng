import { api, configureApi } from "./api-client.js";

const $ = (id) => document.getElementById(id);
const state = { busy: false, program: null, courses: [], required: new Set(), offset: 0, total: 0, shown: 0, dirty: false };
const signIn = () => location.replace("login.html?next=/admin.html");
configureApi({ onUnauthorized: signIn });

function node(tag, text) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  return element;
}

function notice(value, error = false) {
  $("adminMessage").textContent = value;
  $("adminMessage").classList.toggle("error", error);
}

function failure(error) {
  const details = error.details?.map((item) => `${item.field}: ${item.message}`).join("; ");
  return `${error.message}${details ? ` (${details})` : ""}${error.retryAfter ? ` Retry in ${error.retryAfter} seconds.` : ""}`;
}

function pagination() {
  $("instructorPrevious").disabled = state.busy || state.offset === 0;
  $("instructorNext").disabled = state.busy || state.offset + state.shown >= state.total;
  $("reloadProgram").disabled = state.busy || !state.program;
}

async function run(work, success) {
  if (state.busy) return;
  state.busy = true;
  const controls = [...document.querySelectorAll("#adminContent input, #adminContent select, #adminContent button")];
  controls.forEach((element) => { element.disabled = true; });
  notice("Working…");
  try {
    await work();
    notice(typeof success === "function" ? success() : success);
  } catch (error) {
    notice(`${failure(error)} Reload to verify server state before retrying.`, true);
  } finally {
    state.busy = false;
    controls.forEach((element) => { if (element.isConnected) element.disabled = false; });
    renderCourses();
    pagination();
  }
}

async function loadInstructors() {
  const data = await api.admin.instructors.list({ q: $("instructorSearch").value.trim(), offset: state.offset });
  state.total = data.total; state.shown = data.items.length;
  $("instructorPassword").minLength = data.password_min_length;
  $("instructorPasswordHelp").textContent =
    `At least ${data.password_min_length} characters; avoid common passwords and the username.`;
  $("instructorCount").textContent = `${data.total} instructors found.`;
  $("instructorList").replaceChildren();
  for (const teacher of data.items) {
    const row = node("p", `${teacher.username} — ${teacher.first_name} ${teacher.last_name}; `
      + `${teacher.department || "No department"}; ID ${teacher.id}; ${teacher.is_active ? "active" : "disabled"}`);
    $("instructorList").append(row);
  }
  pagination();
}

async function loadPrograms(selectedId = state.program?.id) {
  const programs = (await api.programs.list()).items;
  const select = $("adminProgramSelect");
  select.replaceChildren();
  const blank = node("option", "Create a new degree program"); blank.value = "";
  select.append(blank);
  for (const program of programs) {
    const item = node("option", `${program.code} — ${program.name}`);
    item.value = program.id;
    select.append(item);
  }
  select.value = selectedId ?? "";
}

function renderCourses() {
  const query = $("requiredCourseSearch").value.trim().toLowerCase();
  const list = $("requiredCourseList");
  list.replaceChildren();
  for (const course of state.courses) {
    if (!`${course.code} ${course.title}`.toLowerCase().includes(query)) continue;
    const label = node("label");
    const checkbox = node("input");
    checkbox.type = "checkbox"; checkbox.value = course.id;
    checkbox.checked = state.required.has(course.id); checkbox.disabled = state.busy;
    checkbox.setAttribute("aria-label", `Require ${course.code}`);
    checkbox.addEventListener("change", () => {
      if (state.busy) return;
      if (checkbox.checked) state.required.add(course.id);
      else state.required.delete(course.id);
      state.dirty = true;
      $("requiredCourseCount").textContent = `${state.required.size} required courses selected.`;
    });
    label.append(checkbox, node("span", `${course.code} — ${course.title} (${course.credits} credits)`));
    list.append(label);
  }
  if (!state.courses.length) list.append(node("p", "No courses available. Import the catalog through the operator CLI first."));
  $("requiredCourseCount").textContent = `${state.required.size} required courses selected.`;
}

function displayProgram(program) {
  state.program = program;
  state.required = new Set(program?.requirements.map((course) => course.id) ?? []);
  $("adminProgramSelect").value = program?.id ?? "";
  $("programCode").value = program?.code ?? "";
  $("programName").value = program?.name ?? "";
  $("programCredits").value = program?.total_credits ?? "";
  $("requiredCourseSearch").value = "";
  state.dirty = false;
  renderCourses();
  pagination();
}

function discardChanges() {
  return !state.dirty || confirm("Discard unsaved degree-program edits?");
}

function bind() {
  const password = $("instructorPassword"), confirmation = $("instructorConfirmPassword");
  for (const input of [password, confirmation]) {
    input.addEventListener("input", () => confirmation.setCustomValidity(""));
  }
  $("instructorForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    confirmation.setCustomValidity(password.value === confirmation.value ? "" : "Passwords do not match.");
    if (!$("instructorForm").reportValidity() || state.busy) return;
    let created;
    await run(async () => {
      try {
        created = await api.admin.instructors.create({
          username: $("instructorUsername").value.trim(), password: password.value,
          first_name: $("instructorFirstName").value.trim(), last_name: $("instructorLastName").value.trim(),
          department: $("instructorDepartment").value.trim() || null,
        });
        $("instructorForm").reset();
        state.offset = 0;
        try { await loadInstructors(); }
        catch { throw new Error(`Instructor ${created.username} was created, but the list could not refresh.`); }
      } finally {
        password.value = ""; confirmation.value = ""; confirmation.setCustomValidity("");
      }
    }, () => `Instructor ${created.username} created. Share their initial password securely.`);
  });
  $("instructorSearchForm").addEventListener("submit", (event) => {
    event.preventDefault(); state.offset = 0;
    run(loadInstructors, "Instructor list loaded.");
  });
  $("instructorPrevious").addEventListener("click", () => {
    state.offset = Math.max(0, state.offset - 25); run(loadInstructors, "Instructor list loaded.");
  });
  $("instructorNext").addEventListener("click", () => {
    state.offset += 25; run(loadInstructors, "Instructor list loaded.");
  });
  $("requiredCourseSearch").addEventListener("input", renderCourses);
  for (const id of ["programCode", "programName", "programCredits"]) {
    $(id).addEventListener("input", () => { state.dirty = true; });
  }
  $("adminProgramSelect").addEventListener("change", () => {
    const previous = state.program?.id ?? "";
    if (state.busy || !discardChanges()) { $("adminProgramSelect").value = previous; return; }
    const id = Number($("adminProgramSelect").value);
    run(async () => {
      try { displayProgram(id ? await api.admin.programs.get(id) : null); }
      catch (error) { $("adminProgramSelect").value = previous; throw error; }
    }, "Degree program loaded.");
  });
  $("newProgram").addEventListener("click", () => { if (!state.busy && discardChanges()) displayProgram(null); });
  $("reloadProgram").addEventListener("click", () => {
    if (state.program && discardChanges()) run(async () => {
      displayProgram(await api.admin.programs.get(state.program.id));
    }, "Degree program reloaded.");
  });
  $("programForm").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!$("programForm").reportValidity() || state.busy) return;
    if (!state.required.size) return notice("Choose at least one required course.", true);
    if (state.program && !confirm("Save these degree-program requirements? Existing plans will use the new requirements for validation.")) return;
    const data = {
      code: $("programCode").value.trim(), name: $("programName").value.trim(),
      total_credits: Number($("programCredits").value), course_ids: [...state.required].sort((a, b) => a - b),
    };
    const current = state.program;
    run(async () => {
      const saved = current
        ? await api.admin.programs.update(current.id, { ...data, expected_revision: current.revision })
        : await api.admin.programs.create(data);
      displayProgram(saved);
      await loadPrograms(saved.id);
    }, "Degree program saved. Existing student plans and grades were not rewritten.");
  });
}

async function main() {
  $("adminLogout").addEventListener("click", async () => {
    try { await api.logout(); location.replace("login.html"); }
    catch (error) { notice(failure(error), true); }
  });
  try {
    const user = await api.restoreSession();
    if (!user) return signIn();
    $("adminIdentity").textContent = `${user.username} (${user.role})`;
    $("adminLogout").hidden = false;
    if (user.role !== "admin") return notice("Administrator access is required. Return to your dashboard or sign out.", true);
    let offset = 0;
    while (true) {
      const batch = await api.courses.list({ limit: 100, offset });
      state.courses.push(...batch.items); offset += batch.items.length;
      if (offset >= batch.total || batch.items.length === 0) break;
    }
    await Promise.all([loadInstructors(), loadPrograms()]);
    bind(); displayProgram(null);
    $("adminContent").hidden = false;
    notice("Administrator access verified.");
  } catch (error) {
    notice(`${failure(error)} Reload to retry.`, true);
  }
}

main();
