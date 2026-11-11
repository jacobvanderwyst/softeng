import { api, configureApi } from "./api-client.js";
import { GRADES, STATUSES, courseStatus, gpaDisplay, progressSummary, semesterList, semesterOrder, subjectOf } from "./model.js";

const $ = (id) => document.getElementById(id);
const page = location.pathname.split("/").pop() || "index.html";
const params = new URLSearchParams(location.search);
const state = {
  user: null, programs: [], program: null, courses: [], plans: [], plan: null,
  progress: [], summary: null, issues: [], studentId: null, busy: false, courseTarget: null,
};
const editable = () => state.user?.role === "student" && Boolean(state.user.profile);
const statusOf = (id) => courseStatus(state.progress, id);
const requirements = () => state.program?.requirements ?? [];

function node(tag, className = "", text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function text(id, value) {
  if ($(id)) $(id).textContent = value;
}

function message(value = "", error = false) {
  const target = $("appMessage");
  if (!target) return;
  target.textContent = value;
  target.classList.toggle("error", error);
}

function errorMessage(error) {
  const details = error.details?.map((item) => `${item.field}: ${item.message}`).join("; ");
  const retry = error.retryAfter ? ` Try again in ${error.retryAfter} seconds.` : "";
  return `${error.message}${details ? ` (${details})` : ""}${retry}`;
}

function signIn() {
  const next = new URLSearchParams({ next: location.pathname + location.search });
  location.replace(`login.html?${next}`);
}

function loginDestination() {
  const destination = new URL(params.get("next") || "/index.html", location.origin);
  const pages = ["/", "/index.html", "/degree.html", "/courses.html", "/planner.html"];
  return destination.origin === location.origin && pages.includes(destination.pathname)
    ? destination.pathname + destination.search : "/index.html";
}

configureApi({ onUnauthorized: signIn });

// Serialize mutations: never display a successful save before the server accepts it.
async function perform(work, success = "") {
  if (state.busy) return;
  state.busy = true;
  const controls = [...document.querySelectorAll("button, input, select")].map((element) => ({
    element, disabled: element.disabled,
  }));
  controls.forEach(({ element }) => { element.disabled = true; });
  message("Saving…");
  try {
    await work();
    await refresh();
    render();
    message(success);
  } catch (error) {
    // Multi-request resets may partially complete: reload the actual server state.
    try { await refresh(); render(); } catch { /* retain the original actionable error */ }
    message(errorMessage(error), true);
  } finally {
    state.busy = false;
    controls.forEach(({ element, disabled }) => {
      if (element.isConnected) element.disabled = disabled;
    });
    render();
  }
}

function button(label, onClick, className = "") {
  const element = node("button", className, label);
  element.type = "button";
  element.addEventListener("click", onClick);
  return element;
}

function option(value, label) {
  const element = node("option", "", label);
  element.value = value;
  return element;
}

function badge(status, prefix = "degree") {
  const classes = {
    Completed: "completed", "In Progress": "in-progress", "Not Started": "not-started",
  };
  return node("span", prefix === "catalog"
    ? `catalog-status ${classes[status]}`
    : `degree-status-badge degree-${classes[status]}`, status);
}

async function loadCatalog() {
  const courses = [];
  let offset = 0;
  while (true) {
    const batch = await api.courses.list({ limit: 100, offset });
    courses.push(...batch.items);
    offset += batch.items.length;
    if (offset >= batch.total || batch.items.length === 0) break;
  }
  return courses;
}

function updateNavigation() {
  for (const anchor of document.querySelectorAll("nav a")) {
    const url = new URL(anchor.getAttribute("href"), location.origin);
    if (state.studentId) url.searchParams.set("student_id", state.studentId);
    else url.searchParams.delete("student_id");
    if (state.plan) url.searchParams.set("plan", state.plan.id);
    else url.searchParams.delete("plan");
    anchor.href = url.pathname + url.search;
  }
}

async function refresh() {
  let nextPlans = [], nextProgress = [], nextPlan = null, nextIssues = [], nextSummary = null;
  if (editable() || state.studentId) {
    const [plans, progress, summary] = await Promise.all([
      api.plans.list(state.studentId), api.progress.list(state.studentId), api.progress.summary(state.studentId),
    ]);
    nextPlans = plans.items;
    nextProgress = progress.items;
    nextSummary = summary;
    const requestedId = Number(params.get("plan"));
    let selected = nextPlans.find((plan) => plan.id === requestedId) ?? nextPlans[0];
    try {
      nextPlan = selected ? await api.plans.get(selected.id) : null;
      nextIssues = nextPlan ? (await api.plans.validate(nextPlan.id)).issues : [];
    } catch (error) {
      if (error.status !== 404) throw error;
      // Another tab may have deleted the selected plan between listing and loading it.
      nextPlans = (await api.plans.list(state.studentId)).items;
      selected = nextPlans.find((plan) => plan.id === requestedId) ?? nextPlans[0];
      nextPlan = selected ? await api.plans.get(selected.id) : null;
      nextIssues = nextPlan ? (await api.plans.validate(nextPlan.id)).issues : [];
    }
  }
  const programId = nextPlan?.program_id ?? (editable() ? state.user.profile.program_id : null);
  const nextProgram = programId ? await api.programs.get(programId) : null;
  // Publish only complete snapshots: failed fetches must not mix two plans' data.
  Object.assign(state, {
    plans: nextPlans, progress: nextProgress, summary: nextSummary,
    plan: nextPlan, issues: nextIssues, program: nextProgram,
  });
  closeCourseModal();
  if (state.plan) params.set("plan", state.plan.id);
  else params.delete("plan");
  if (state.studentId) params.set("student_id", state.studentId);
  else params.delete("student_id");
  const query = params.toString();
  history.replaceState(null, "", location.pathname + (query ? `?${query}` : ""));
  updateNavigation();
}

function renderWorkspace() {
  const container = $("workspace");
  container.replaceChildren();
  const note = node("p", "workspace-note",
    editable() ? "Changes are saved to your account. Course progress is self-reported, not an official transcript."
      : "Staff view is read-only. Enter a student ID to view an authorized student's plans and progress.");
  container.append(note);
  if (!editable()) {
    const form = node("form", "workspace-form");
    const label = node("label", "", "Student ID");
    const input = node("input");
    input.type = "number"; input.min = "1"; input.max = String(2 ** 31);
    input.required = true; input.id = "studentId"; input.value = state.studentId ?? "";
    label.htmlFor = input.id;
    const submit = node("button", "", "View Student"); submit.type = "submit";
    form.append(label, input, submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const url = new URL(location.href);
      url.searchParams.set("student_id", input.value); url.searchParams.delete("plan");
      location.assign(url.pathname + url.search);
    });
    container.append(form);
  }
  if (state.plans.length && state.plan) {
    const label = node("label", "", "Saved plan"); label.htmlFor = "planSelect";
    const select = node("select"); select.id = "planSelect";
    for (const plan of state.plans) select.append(option(plan.id, plan.name));
    select.value = state.plan.id;
    select.addEventListener("change", () => {
      closeCourseModal();
      params.set("plan", select.value);
      perform(async () => {}, "Plan loaded.");
    });
    container.append(label, select);
    if (editable()) {
      container.append(button("Rename Plan", () => {
        const name = prompt("Plan name", state.plan.name)?.trim();
        if (name) perform(() => api.plans.rename(state.plan.id, name), "Plan renamed.");
      }));
      container.append(button("Delete Plan", () => {
        if (confirm(`Delete "${state.plan.name}" and all of its planned courses? Course progress is kept.`)) {
          perform(() => api.plans.remove(state.plan.id), "Plan deleted.");
        }
      }));
    }
  }
  if (editable() && state.programs.length) {
    const form = node("form", "workspace-form");
    const name = node("input"); name.id = "planName"; name.placeholder = "My degree plan";
    name.maxLength = 120; name.required = true;
    const nameLabel = node("label", "", "New plan name"); nameLabel.htmlFor = name.id;
    const program = node("select"); program.id = "planProgram";
    for (const item of state.programs) program.append(option(item.id, item.name));
    program.value = state.program?.id ?? state.programs[0].id;
    const programLabel = node("label", "", "Degree program"); programLabel.htmlFor = program.id;
    const submit = node("button", "", "Create Plan"); submit.type = "submit";
    form.append(nameLabel, name, programLabel, program, submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      perform(async () => {
        const plan = await api.plans.create({ name: name.value.trim(), program_id: Number(program.value) });
        params.set("plan", plan.id);
      }, "Plan created. Add a semester to begin planning.");
    });
    container.append(form);
  }
  if (!state.plans.length && (editable() || state.studentId)) {
    container.append(node("p", "workspace-note", "No saved plans yet."));
  }
}

function renderDashboard() {
  if (page !== "index.html") return;
  document.querySelector(".welcome h2").textContent = state.program?.name ?? "No degree program";
  const summary = progressSummary(requirements(), state.progress, state.program?.total_credits ?? 0);
  text("dashboardProgressPercent", `${summary.percent}%`);
  $("dashboardProgressFill").style.width = `${summary.percent}%`;
  text("dashboardCompletedCredits", summary.completedCredits);
  text("dashboardRequiredCredits", state.program?.total_credits ?? 0);
  const list = $("dashboardRequirements");
  list.replaceChildren();
  for (const course of requirements()) {
    const status = statusOf(course.id);
    const row = node("div", `course ${status === "Completed" ? "completed" : status === "In Progress" ? "in-progress" : "remaining"}`);
    const info = node("div");
    info.append(node("h3", "", course.code), node("p", "", course.title));
    row.append(info, node("span", "", status));
    list.append(row);
  }
  if (!requirements().length) list.append(node("p", "empty-note", "No degree requirements available."));
}

function renderGpa() {
  const display = gpaDisplay(state.summary);
  for (const id of ["dashboardGPA", "degreeGPA", "summaryGPA"]) text(id, display.gpa);
  for (const id of ["dashboardGradedCredits", "degreeGPACredits"]) text(id, display.credits);
  for (const id of ["dashboardQualityPoints", "degreeQualityPoints"]) text(id, display.qualityPoints);
  text("summaryGPADescription",
    `${display.courses} completed graded courses; ${display.credits} GPA credit hours. `
    + "All saved progress is included, not just this plan. Self-reported planning GPA, not official.");
}

function filtered(courses, search, subject, status, credits) {
  const query = search.trim().toLowerCase();
  return courses.filter((course) =>
    `${course.code} ${course.title} ${course.description ?? ""}`.toLowerCase().includes(query)
    && (subject === "all" || subjectOf(course) === subject)
    && (status === "all" || statusOf(course.id) === status)
    && (!credits || credits === "all" || course.credits === Number(credits)));
}

function populateSubjects(select, courses) {
  const previous = select.value;
  select.replaceChildren(option("all", "All Subjects"));
  for (const subject of [...new Set(courses.map(subjectOf))].sort()) {
    select.append(option(subject, subject));
  }
  select.value = [...select.options].some((item) => item.value === previous) ? previous : "all";
}

function renderDegree() {
  if (page !== "degree.html") return;
  const program = state.program;
  const summary = progressSummary(requirements(), state.progress, program?.total_credits ?? 0);
  text("degreeProgramName", program?.name ?? "No degree program");
  $("degreeSelect").replaceChildren(option(program?.id ?? "", program?.name ?? "No program"));
  $("degreeSelect").disabled = true;
  text("degreeRequiredCredits", program?.total_credits ?? 0);
  text("degreeCompletedCredits", summary.completedCredits);
  text("degreeProgressPercent", `${summary.percent}%`);
  $("degreeProgressFill").style.width = `${summary.percent}%`;
  text("totalRequirements", requirements().length);
  text("completedRequirements", summary.completedCount);
  text("inProgressRequirements", summary.inProgressCount);
  text("remainingRequirements", summary.remainingCount);
  text("summaryCompletedCredits", summary.completedCredits);
  text("summaryRemainingCredits", summary.remainingCredits);
  text("summaryRemainingCourses", requirements().length - summary.completedCount);
  populateSubjects($("degreeCategoryFilter"), requirements());
  $("resetDegreeButton").hidden = !editable();
  renderDegreeList();
}

function renderDegreeList() {
  const courses = filtered(requirements(), $("degreeSearch").value,
    $("degreeCategoryFilter").value, $("degreeStatusFilter").value);
  const list = $("degreeRequirementsList");
  list.replaceChildren();
  text("degreeResultCount", `${courses.length} requirements found`);
  $("degreeEmptyState").hidden = courses.length !== 0;
  for (const course of courses) {
    const card = node("article", "degree-requirement-card");
    const info = node("div");
    info.append(node("div", "degree-course-code", course.code), node("h3", "", course.title),
      node("p", "", subjectOf(course)));
    const actions = node("div", "degree-course-actions");
    actions.append(node("span", "degree-credit-label", `${course.credits} Credits`), badge(statusOf(course.id)));
    const savedGrade = state.progress.find((item) => item.course_id === course.id)?.grade ?? null;
    if (editable()) {
      const select = node("select", "degree-status-select");
      for (const status of STATUSES) select.append(option(status, status));
      select.value = statusOf(course.id);
      select.setAttribute("aria-label", `Student-reported status for ${course.code}`);
      select.addEventListener("change", () => perform(
        () => api.progress.set(course.id, select.value), "Course progress saved."));
      actions.append(select);
      const gradeControl = node("div", "degree-grade-control");
      const grade = node("select", "degree-grade-select"); grade.id = `grade-${course.id}`;
      const gradeLabel = node("label", "", "Self-reported grade"); gradeLabel.htmlFor = grade.id;
      grade.append(option("", "No grade"));
      for (const value of GRADES) grade.append(option(value, value));
      grade.value = savedGrade ?? "";
      grade.setAttribute("aria-label", `Self-reported grade for ${course.code}`);
      grade.disabled = state.busy;
      grade.addEventListener("change", () => perform(
        () => api.progress.set(course.id, statusOf(course.id), grade.value || null), "Course grade saved."));
      gradeControl.append(gradeLabel, grade);
      if (statusOf(course.id) !== "Completed") {
        gradeControl.append(node("small", "", "Saved grade excluded from GPA until Completed."));
      }
      actions.append(gradeControl);
    } else {
      actions.append(node("span", "degree-grade-readonly", `Self-reported grade: ${savedGrade ?? "None"}`));
    }
    card.append(info, actions); list.append(card);
  }
}

function renderCatalog() {
  if (page !== "courses.html") return;
  populateSubjects($("subjectFilter"), state.courses);
  const courses = filtered(state.courses, $("courseSearch").value, $("subjectFilter").value,
    $("statusFilter").value, $("creditFilter").value);
  const list = $("catalogCourseList");
  list.replaceChildren();
  text("courseCount", `${courses.length} courses found`);
  $("catalogEmptyState").hidden = courses.length !== 0;
  for (const course of courses) {
    const card = node("article", "catalog-course-card");
    const info = node("div");
    info.append(node("div", "catalog-course-code", course.code), node("h4", "", course.title));
    const actions = node("div", "catalog-course-actions");
    actions.append(node("span", "catalog-credit-label", `${course.credits} Credits`),
      badge(statusOf(course.id), "catalog"), button("View Details", async () => {
        try {
          const detail = await api.courses.get(course.id);
          text("detailCourseCode", detail.code); text("detailCourseName", detail.title);
          text("detailCourseDescription", detail.description || "No description available.");
          text("detailCourseCredits", `${detail.credits} Credits`);
          text("detailCoursePrerequisites", detail.prerequisites.map((item) => item.code).join(", ") || "None listed");
          $("detailCourseStatus").replaceChildren(badge(statusOf(course.id), "catalog"));
          $("catalogModal").hidden = false; $("closeCatalogModal").focus();
        } catch (error) { message(errorMessage(error), true); }
      }, "catalog-details-button"));
    card.append(info, actions); list.append(card);
  }
}

function closeCourseModal() {
  if ($("courseModal")) $("courseModal").style.display = "none";
  state.courseTarget = null;
}

function renderPlanner() {
  if (page !== "planner.html") return;
  const plan = state.plan;
  const terms = semesterList(plan);
  $("addSemesterButton").hidden = !editable();
  $("resetPlannerButton").hidden = !editable();
  $("addSemesterButton").disabled = !plan;
  $("resetPlannerButton").disabled = !plan;
  $("addSemesterForm").hidden = true;
  const container = $("semesterContainer");
  container.replaceChildren();
  if (!plan || !terms.length) {
    container.append(node("p", "planner-empty", !plan
      ? "Create or select a saved plan above to begin." : "No semesters yet. Add a semester to begin."));
  }
  for (const term of terms) {
    const courses = plan.courses.filter((course) => course.term_index === term.term_index);
    const card = node("section", "semester-card");
    const header = node("div", "semester-header");
    const info = node("div"); info.append(node("h2", "", term.name), node("p", "", `Term ${term.term_index}`));
    const credits = node("div", "semester-credits");
    credits.append(node("strong", "", courses.reduce((sum, course) => sum + course.credits, 0)),
      node("span", "", "Credit Hours"));
    header.append(info, credits); card.append(header);
    if (!courses.length) card.append(node("div", "empty-semester", "No courses added yet."));
    for (const course of courses) {
      const row = node("div", "planned-course");
      const courseInfo = node("div");
      courseInfo.append(node("h3", "", course.code), node("p", "", course.title));
      const right = node("div", "course-right");
      right.append(node("span", "", `${course.credits} Credits`));
      if (editable()) right.append(button("Remove", () => perform(
        () => api.plans.removeCourse(plan.id, course.course_id), "Course removed."), "remove-course-button"));
      row.append(courseInfo, right); card.append(row);
    }
    if (editable()) {
      const actions = node("div", "semester-buttons");
      actions.append(button("+ Add / Move Course", () => {
        state.courseTarget = { planId: plan.id, termIndex: term.term_index };
        $("courseSelect").replaceChildren(option("", "Choose a course…"));
        for (const course of state.courses) {
          if (courses.some((item) => item.course_id === course.id)) continue;
          const existing = plan.courses.find((item) => item.course_id === course.id);
          $("courseSelect").append(option(course.id,
            `${course.code} - ${course.title} (${course.credits} Credits)${existing ? " — move from another term" : ""}`));
        }
        $("courseModal").style.display = "flex"; $("courseSelect").focus();
      }, "add-course-button"));
      actions.append(button("Rename Semester", () => {
        const name = prompt("Semester name (renaming does not change its term order)", term.name)?.trim();
        if (name) perform(() => api.plans.setSemester(plan.id, term.term_index, name), "Semester renamed.");
      }));
      actions.append(button("Delete Semester", () => {
        if (confirm(`Delete ${term.name} and its planned courses?`)) perform(
          () => api.plans.removeSemester(plan.id, term.term_index), "Semester deleted.");
      }, "delete-semester-button"));
      card.append(actions);
    }
    container.append(card);
  }
  let validation = $("planValidation");
  if (!validation) {
    validation = node("section", "validation-issues"); validation.id = "planValidation";
    container.after(validation);
  }
  validation.replaceChildren(node("h2", "", "Plan Validation"));
  validation.hidden = !plan;
  validation.append(node("p", "", "Checks use planned course order. Student-reported completion does not override prerequisites."));
  const list = node("ul");
  for (const issue of state.issues) {
    const course = state.courses.find((item) => item.id === issue.course_id);
    list.append(node("li", "", `${course ? `${course.code}: ` : ""}${issue.message}`));
  }
  if (!state.issues.length) list.append(node("li", "", "No planning issues found."));
  validation.append(list);
}

function render() {
  renderWorkspace();
  renderDashboard();
  renderDegree();
  renderCatalog();
  renderPlanner();
  renderGpa();
}

function addPasswordForm(account) {
  const details = node("details", "password-settings");
  const summary = node("summary", "", "Change Password");
  const form = node("form", "password-form"); form.id = "passwordChangeForm";
  for (const [id, labelText, autocomplete] of [
    ["currentPassword", "Current password", "current-password"],
    ["newPassword", "New password", "new-password"],
    ["confirmPassword", "Confirm new password", "new-password"],
  ]) {
    const label = node("label", "", labelText); label.htmlFor = id;
    const input = node("input"); input.id = id; input.type = "password";
    input.required = true; input.autocomplete = autocomplete; input.maxLength = 256;
    form.append(label, input);
  }
  const note = node("p", "", "Use a unique password. Server password policy applies; other sessions will be signed out.");
  const feedback = node("p"); feedback.id = "passwordChangeMessage"; feedback.setAttribute("role", "status");
  const submit = node("button", "", "Save Password"); submit.type = "submit";
  const clear = () => {
    form.reset(); $("confirmPassword").setCustomValidity(""); feedback.textContent = "";
  };
  const cancel = button("Cancel", () => { clear(); details.open = false; summary.focus(); });
  form.append(note, submit, cancel, feedback); details.append(summary, form); account.append(details);
  details.addEventListener("toggle", () => { if (!details.open) clear(); });
  for (const id of ["newPassword", "confirmPassword"]) {
    $(id).addEventListener("input", () => $("confirmPassword").setCustomValidity(""));
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const current = $("currentPassword"), next = $("newPassword"), confirm = $("confirmPassword");
    confirm.setCustomValidity(next.value === confirm.value ? "" : "New passwords do not match.");
    if (!form.reportValidity() || state.busy) return;
    state.busy = true;
    const controls = [...form.querySelectorAll("button, input")];
    controls.forEach((control) => { control.disabled = true; });
    feedback.textContent = "Changing password…";
    try {
      await api.changePassword(current.value, next.value);
      form.reset();
      feedback.textContent = "Password changed. Other sessions have been signed out.";
    } catch (error) {
      feedback.textContent = errorMessage(error);
    } finally {
      // Never retain password fields after a request, successful or not.
      current.value = ""; next.value = ""; confirm.value = "";
      confirm.setCustomValidity("");
      controls.forEach((control) => { control.disabled = false; });
      state.busy = false;
    }
  });
}

function bindPages() {
  if (page === "degree.html") {
    for (const id of ["degreeSearch", "degreeCategoryFilter", "degreeStatusFilter"]) {
      $(id).addEventListener(id === "degreeSearch" ? "input" : "change", renderDegreeList);
    }
    $("resetDegreeButton").addEventListener("click", () => {
      if (confirm("Set all student-reported course statuses to Not Started? This affects every plan.")) {
        perform(async () => {
          for (const item of state.progress) await api.progress.set(item.course_id, "Not Started");
        }, "Course progress reset.");
      }
    });
  }
  if (page === "courses.html") {
    for (const id of ["courseSearch", "subjectFilter", "creditFilter", "statusFilter"]) {
      $(id).addEventListener(id === "courseSearch" ? "input" : "change", renderCatalog);
    }
    const close = () => { $("catalogModal").hidden = true; };
    $("closeCatalogModal").addEventListener("click", close);
    $("closeCatalogDetails").addEventListener("click", close);
    $("catalogModal").addEventListener("click", (event) => {
      if (event.target === $("catalogModal")) close();
    });
  }
  if (page === "planner.html") {
    $("semesterYear").value = new Date().getFullYear();
    $("semesterYear").min = 2000;
    $("semesterYear").max = 2100;
    $("courseModal").setAttribute("role", "dialog");
    $("courseModal").setAttribute("aria-modal", "true");
    $("courseModal").setAttribute("aria-label", "Add or move a course");
    $("addSemesterButton").addEventListener("click", () => { $("addSemesterForm").hidden = false; });
    $("cancelSemesterButton").addEventListener("click", () => { $("addSemesterForm").hidden = true; });
    $("createSemesterButton").addEventListener("click", () => {
      if (!$("semesterYear").reportValidity()) return;
      const name = `${$("semesterTerm").value} ${$("semesterYear").value}`;
      const terms = semesterList(state.plan);
      const index = Math.max(0, ...terms.map((term) => term.term_index)) + 1;
      if (index > 16) return message("Plans support terms 1–16. Rename/reuse an existing semester or create another plan.", true);
      if (terms.some((term) => term.name === name)) return message("This semester already exists.", true);
      const datedTerms = terms.map((term) => semesterOrder(term.name)).filter((order) => order !== null);
      if (datedTerms.some((order) => order >= semesterOrder(name))) {
        return message("Add semesters in chronological order, after the latest named semester.", true);
      }
      perform(() => api.plans.setSemester(state.plan.id, index, name), "Semester saved.");
    });
    $("confirmCourseButton").addEventListener("click", () => {
      const courseId = Number($("courseSelect").value);
      if (!courseId) return message("Please select a course.", true);
      const target = state.courseTarget;
      if (!target || target.planId !== state.plan?.id) {
        closeCourseModal();
        return message("The selected plan changed. Open Add / Move Course again.", true);
      }
      perform(async () => {
        await api.plans.setCourse(target.planId, { course_id: courseId, term_index: target.termIndex });
        closeCourseModal();
      }, "Course saved.");
    });
    for (const id of ["closeModalButton", "cancelCourseButton"]) $(id).addEventListener("click", closeCourseModal);
    $("courseModal").addEventListener("click", (event) => {
      if (event.target === $("courseModal")) closeCourseModal();
    });
    $("resetPlannerButton").addEventListener("click", () => {
      if (confirm("Remove all semesters and planned courses from this plan? Course progress is kept.")) {
        perform(async () => {
          for (const term of semesterList(state.plan)) await api.plans.removeSemester(state.plan.id, term.term_index);
        }, "Planner reset.");
      }
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeCourseModal();
      if ($("catalogModal")) $("catalogModal").hidden = true;
    }
  });
}

async function main() {
  if (page === "login.html") {
    const form = $("loginForm");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = form.querySelector("button"); submit.disabled = true;
      text("loginError", "");
      try {
        await api.login($("username").value, $("password").value);
        $("password").value = "";
        location.replace(loginDestination());
      } catch (error) {
        text("loginError", errorMessage(error));
      } finally { submit.disabled = false; }
    });
    try {
      if (await api.restoreSession()) location.replace(loginDestination());
    } catch (error) { text("loginError", errorMessage(error)); }
    return;
  }
  const messageBox = node("div", "app-message", "Loading your account…");
  messageBox.id = "appMessage"; messageBox.setAttribute("role", "status");
  document.querySelector("header").after(messageBox);
  try {
    state.user = await api.restoreSession();
    if (!state.user) return signIn();
    const account = node("div", "account-controls");
    account.append(node("span", "", `${state.user.profile?.first_name || state.user.username} (${state.user.role})`),
      button("Sign Out", async () => {
        try { await api.logout(); location.replace("login.html"); }
        catch (error) { message(errorMessage(error), true); }
      }));
    document.querySelector("header").append(account);
    addPasswordForm(account);
    const workspace = node("section", "workspace-controls"); workspace.id = "workspace";
    document.querySelector("main").prepend(workspace);
    if (!editable() && params.has("student_id")) {
      const value = Number(params.get("student_id"));
      if (!Number.isSafeInteger(value) || value < 1 || value > 2 ** 31) throw new Error("Invalid student ID.");
      state.studentId = value;
    }
    const [programs, courses] = await Promise.all([api.programs.list(), loadCatalog()]);
    state.programs = programs.items; state.courses = courses;
    await refresh();
    bindPages();
    render();
    document.querySelector("main").hidden = false;
    message();
  } catch (error) {
    message(`${errorMessage(error)} Reload the page to retry.`, true);
    // Keep staff student selection usable if a student is missing or unauthorized.
    if (state.user && !editable() && $("workspace")) {
      renderWorkspace();
      for (const child of document.querySelector("main").children) child.hidden = child.id !== "workspace";
      document.querySelector("main").hidden = false;
    }
  }
}

main();
