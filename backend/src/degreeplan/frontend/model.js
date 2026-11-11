// Pure presentation rules shared by all pages and the dependency-free Node tests.
export const STATUSES = ["Not Started", "In Progress", "Completed"];
export const GRADES = ["A", "B", "C", "D", "F"];

// The server owns weighting, eligibility and rounding; never derive GPA from the selected plan.
export function gpaDisplay(summary) {
  return {
    gpa: typeof summary?.gpa === "number" && Number.isFinite(summary.gpa)
      ? summary.gpa.toFixed(2) : "—",
    credits: String(summary?.graded_credits ?? 0),
    qualityPoints: Number(summary?.quality_points ?? 0).toFixed(2),
    courses: String(summary?.graded_courses ?? 0),
  };
}

export function courseStatus(items, courseId) {
  return items.find((item) => item.course_id === courseId)?.status ?? "Not Started";
}

export function progressSummary(requirements, items, requiredCredits) {
  const completed = requirements.filter((course) => courseStatus(items, course.id) === "Completed");
  const inProgress = requirements.filter((course) => courseStatus(items, course.id) === "In Progress");
  const completedCredits = completed.reduce((sum, course) => sum + course.credits, 0);
  return {
    completedCredits,
    completedCount: completed.length,
    inProgressCount: inProgress.length,
    remainingCount: requirements.length - completed.length - inProgress.length,
    remainingCredits: Math.max(0, requiredCredits - completedCredits),
    percent: requiredCredits > 0 ? Math.min(100, Math.round(completedCredits / requiredCredits * 100)) : 0,
  };
}

export function subjectOf(course) {
  return course.code.match(/^[A-Za-z]+/)?.[0]?.toUpperCase() ?? "Other";
}

export function semesterList(plan) {
  const terms = new Map((plan?.semesters ?? []).map((term) => [term.term_index, term]));
  for (const course of plan?.courses ?? []) {
    if (!terms.has(course.term_index)) {
      terms.set(course.term_index, { term_index: course.term_index, name: `Term ${course.term_index}` });
    }
  }
  return [...terms.values()].sort((a, b) => a.term_index - b.term_index);
}

export function semesterOrder(name) {
  const match = /^(Spring|Summer|Fall) (\d{4})$/.exec(name);
  return match ? Number(match[2]) * 3 + ["Spring", "Summer", "Fall"].indexOf(match[1]) : null;
}
