"""Strict, bounded operator catalog format. Never deletes existing catalog records."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Connection

from ..db import tables as t

Code = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_. -]*$")]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Credits = Annotated[int, Field(strict=True, ge=0, le=1000)]
MAX_BYTES = 5 * 1024 * 1024


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Program(Record):
    code: Code
    name: Name
    total_credits: Credits


class Course(Record):
    code: Code
    title: Name
    credits: Credits
    description: Annotated[str, Field(max_length=10000)] | None = None


class Prerequisite(Record):
    course: Code
    prerequisite: Code


class Requirement(Record):
    program: Code
    course: Code


class Catalog(Record):
    version: Literal[1]
    programs: list[Program] = Field(max_length=1000)
    courses: list[Course] = Field(max_length=10000)
    prerequisites: list[Prerequisite] = Field(max_length=50000)
    requirements: list[Requirement] = Field(max_length=50000)


def _unique_pairs(pairs: list[tuple[str, str]], label: str) -> None:
    if len(pairs) != len(set(pairs)):
        raise ValueError(f"Duplicate {label}.")


def load(path: Path) -> Catalog:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Catalog exceeds 5 MiB.")

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key.")
            result[key] = value
        return result

    return Catalog.model_validate(json.loads(raw, object_pairs_hook=object_pairs))


def apply(conn: Connection, data: Catalog, *, update: bool) -> dict[str, int]:
    """Validate entire graph before writes; caller controls rollback/commit."""
    if type(data.version) is not int:
        raise ValueError("version must be integer 1.")
    for records in (data.programs, data.courses):
        codes = [record.code for record in records]
        if len(codes) != len(set(codes)):
            raise ValueError("Duplicate catalog code.")
    prereqs = [(edge.course, edge.prerequisite) for edge in data.prerequisites]
    requirements = [(edge.program, edge.course) for edge in data.requirements]
    _unique_pairs(prereqs, "prerequisite")
    _unique_pairs(requirements, "requirement")
    existing_courses = {row.code: row.id for row in conn.execute(sa.select(t.courses.c.code, t.courses.c.id))}
    existing_programs = {row.code: row.id for row in conn.execute(sa.select(t.programs.c.code, t.programs.c.id))}
    course_codes = set(existing_courses) | {row.code for row in data.courses}
    program_codes = set(existing_programs) | {row.code for row in data.programs}
    if any(a not in course_codes or b not in course_codes or a == b for a, b in prereqs):
        raise ValueError("Invalid prerequisite reference or self-reference.")
    if any(a not in program_codes or b not in course_codes for a, b in requirements):
        raise ValueError("Invalid requirement reference.")
    reverse_courses = {value: key for key, value in existing_courses.items()}
    graph: dict[str, set[str]] = {code: set() for code in course_codes}
    for row in conn.execute(sa.select(t.course_prerequisites)):
        graph[reverse_courses[row.course_id]].add(reverse_courses[row.prerequisite_id])
    for a, b in prereqs:
        graph[a].add(b)
    # Iterative topological traversal avoids recursion limits on a large valid catalog.
    pending = {code: len(edges) for code, edges in graph.items()}
    dependents: dict[str, list[str]] = {code: [] for code in graph}
    for code, edges in graph.items():
        for edge in edges:
            dependents[edge].append(code)
    ready = [code for code, count in pending.items() if count == 0]
    visited = 0
    while ready:
        visited += 1
        for dependent in dependents[ready.pop()]:
            pending[dependent] -= 1
            if pending[dependent] == 0:
                ready.append(dependent)
    if visited != len(graph):
        raise ValueError("Prerequisite graph contains a cycle.")
    counts = {"inserted": 0, "updated": 0, "edges_added": 0}
    for table, records, existing in (
        (t.programs, data.programs, existing_programs), (t.courses, data.courses, existing_courses)
    ):
        for record in records:
            values = record.model_dump()
            if record.code in existing:
                current = conn.execute(sa.select(table).where(table.c.id == existing[record.code])).mappings().one()
                changed = any(current[key] != value for key, value in values.items())
                if changed and not update:
                    raise ValueError(f"Existing code {record.code} differs; pass --update.")
                if changed:
                    conn.execute(sa.update(table).where(table.c.id == current["id"]).values(**values))
                    counts["updated"] += 1
            else:
                result = conn.execute(sa.insert(table).values(**values))
                key = result.inserted_primary_key
                if key is None:
                    raise ValueError("Catalog insert did not return a primary key.")
                existing[record.code] = int(key[0])
                counts["inserted"] += 1
    for table, pairs in (
        (t.course_prerequisites, [
            {"course_id": existing_courses[a], "prerequisite_id": existing_courses[b]} for a, b in prereqs]),
        (t.program_requirements, [
            {"program_id": existing_programs[a], "course_id": existing_courses[b]} for a, b in requirements]),
    ):
        for values in pairs:
            where = sa.and_(*(table.c[key] == value for key, value in values.items()))
            if not conn.execute(sa.select(table).where(where)).first():
                conn.execute(sa.insert(table).values(**values))
                counts["edges_added"] += 1
    return counts


def add_missing_reference(conn: Connection, data: Catalog) -> dict[str, int]:
    """Demo-only caller: add reference records but never overwrite edited programs/requirements."""
    programs = set(conn.execute(sa.select(t.programs.c.code)).scalars())
    courses = set(conn.execute(sa.select(t.courses.c.code)).scalars())
    new_programs = {program.code for program in data.programs} - programs
    new_courses = {course.code for course in data.courses} - courses
    missing = Catalog(
        version=data.version,
        programs=[program for program in data.programs if program.code in new_programs],
        courses=[course for course in data.courses if course.code in new_courses],
        requirements=[edge for edge in data.requirements if edge.program in new_programs],
        prerequisites=[edge for edge in data.prerequisites if edge.course in new_courses],
    )
    return apply(conn, missing, update=False)
