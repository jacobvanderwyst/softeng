-- Example: load the degree catalog from another SQLite database into the backend's plans database.
-- Run it with backend/scripts/run_sql_import.py (steps: docs/DEVELOPMENT.md, section 4.2).
--
-- The source database is attached as `src`; the plans database is `main`. EDIT THE SOURCE TABLE AND COLUMN
-- NAMES BELOW to match the real schema. The example assumes this source layout:
--   src.Program(ProgramID, Code, Name, TotalUnits)
--   src.Course(CourseID, CourseCode, Title, Units, Description)
--   src.Prerequisite(CourseID, PrereqID)
--   src.ProgramCourse(ProgramID, CourseID)
--
-- Order matters (parents before children). Keeping the source ids as the new ids makes the relationships carry over.

INSERT INTO programs (id, code, name, total_credits)
SELECT ProgramID, Code, Name, TotalUnits
FROM src.Program;

INSERT INTO courses (id, code, title, credits, description)
SELECT CourseID, CourseCode, Title, Units, Description
FROM src.Course;

INSERT INTO course_prerequisites (course_id, prerequisite_id)
SELECT CourseID, PrereqID
FROM src.Prerequisite;

INSERT INTO program_requirements (program_id, course_id)
SELECT ProgramID, CourseID
FROM src.ProgramCourse;

-- Tips:
--  * Transform values inline, for example:  SELECT ..., TRIM(Title), CAST(Units AS INTEGER) ...
--  * Skip junk rows with WHERE, for example:  WHERE Active = 1
--  * If the source uses text ids (UUIDs), do not copy them into `id`: let the backend generate ids and JOIN on a code column
--    instead, for example  INSERT INTO course_prerequisites SELECT c.id, p.id FROM src.Prereq x
--    JOIN courses c ON c.code = x.CourseCode JOIN courses p ON p.code = x.PrereqCode;
