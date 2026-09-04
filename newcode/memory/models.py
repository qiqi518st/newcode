from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class NoteType(str, Enum):
    USER_PREFERENCE = "user_preference"
    CORRECTION_FEEDBACK = "correction_feedback"
    PROJECT_KNOWLEDGE = "project_knowledge"
    REFERENCE_MATERIAL = "reference_material"


TYPE_SCOPE = {
    NoteType.USER_PREFERENCE.value: "user",
    NoteType.CORRECTION_FEEDBACK.value: "user",
    NoteType.PROJECT_KNOWLEDGE.value: "project",
    NoteType.REFERENCE_MATERIAL.value: "project",
}


@dataclass
class MemoryNote:
    type: str
    title: str
    content: str
    scope: str
    created: str = ""
    updated: str = ""
    source_session: str = ""
    status: str = "active"
    filename: str | None = None


@dataclass
class MemoryOperation:
    action: str
    level: str
    type: str | None = None
    title: str | None = None
    slug: str | None = None
    filename: str | None = None
    content: str | None = None
