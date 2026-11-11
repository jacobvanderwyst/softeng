import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../../backend/src/degreeplan/frontend/model.js", import.meta.url), "utf8");
const { GRADES, courseStatus, gpaDisplay, progressSummary, semesterList, semesterOrder, subjectOf } =
  await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

test("progress only counts completed requirements, not unrelated courses or planned credits", () => {
  const requirements = [{ id: 1, credits: 4 }, { id: 2, credits: 3 }, { id: 3, credits: 4 }];
  const statuses = [
    { course_id: 1, status: "Completed" }, { course_id: 2, status: "In Progress" },
    { course_id: 999, status: "Completed" },
  ];
  assert.deepEqual(progressSummary(requirements, statuses, 120), {
    completedCredits: 4, completedCount: 1, inProgressCount: 1,
    remainingCount: 1, remainingCredits: 116, percent: 3,
  });
  assert.equal(courseStatus(statuses, 3), "Not Started");
});

test("empty degrees and excess credits never yield invalid percentages", () => {
  assert.equal(progressSummary([], [], 0).percent, 0);
  const summary = progressSummary([{ id: 1, credits: 6 }], [{ course_id: 1, status: "Completed" }], 3);
  assert.equal(summary.percent, 100);
  assert.equal(summary.remainingCredits, 0);
});

test("named empty semesters survive and legacy occupied terms remain visible", () => {
  assert.deepEqual(semesterList({
    semesters: [{ term_index: 3, name: "Fall 2027" }, { term_index: 1, name: "Spring 2027" }],
    courses: [{ term_index: 2 }, { term_index: 2 }, { term_index: 3 }],
  }), [
    { term_index: 1, name: "Spring 2027" }, { term_index: 2, name: "Term 2" },
    { term_index: 3, name: "Fall 2027" },
  ]);
  assert.deepEqual(semesterList(null), []);
});

test("subjects work with both backend and EduNav course code formats", () => {
  assert.equal(subjectOf({ code: "CS101" }), "CS");
  assert.equal(subjectOf({ code: "COSC 1436" }), "COSC");
  assert.equal(subjectOf({ code: "123" }), "Other");
});

test("calendar terms sort chronologically across years", () => {
  assert.ok(semesterOrder("Fall 2026") < semesterOrder("Spring 2027"));
  assert.ok(semesterOrder("Spring 2027") < semesterOrder("Summer 2027"));
  assert.ok(semesterOrder("Summer 2027") < semesterOrder("Fall 2027"));
  assert.equal(semesterOrder("Term 1"), null);
});

test("GPA presentation formats server values without recalculating or treating null as zero", () => {
  assert.deepEqual(GRADES, ["A", "B", "C", "D", "F"]);
  assert.deepEqual(gpaDisplay(null), { gpa: "—", credits: "0", qualityPoints: "0.00", courses: "0" });
  assert.deepEqual(gpaDisplay({ gpa: null, graded_credits: 0, quality_points: 0, graded_courses: 0 }),
    { gpa: "—", credits: "0", qualityPoints: "0.00", courses: "0" });
  assert.deepEqual(gpaDisplay({ gpa: 0, graded_credits: 4, quality_points: 0, graded_courses: 1 }),
    { gpa: "0.00", credits: "4", qualityPoints: "0.00", courses: "1" });
  assert.deepEqual(gpaDisplay({ gpa: 2.86, graded_credits: 7, quality_points: 20, graded_courses: 2 }),
    { gpa: "2.86", credits: "7", qualityPoints: "20.00", courses: "2" });
  assert.equal(gpaDisplay({ gpa: NaN }).gpa, "—");
});
