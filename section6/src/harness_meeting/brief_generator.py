"""Structured EngineeringBrief generator from chat transcripts."""

import uuid

from harness_contracts.brief import BriefStatus, EngineeringBrief


class BriefGenerator:
    """Extracts structured brief data from a meeting chat transcript."""

    def __init__(self) -> None:
        self.briefs: dict[str, EngineeringBrief] = {}

    def generate_brief(
        self,
        meeting_id: str,
        project_id: str,
        transcript: str,
    ) -> EngineeringBrief:
        """Generates a DRAFT EngineeringBrief from the chat transcript.

        In production this would use Section 3 callModel to extract fields.
        For now, uses rule-based heuristic extraction.
        """
        brief_id = f"brief-{uuid.uuid4().hex[:8]}"

        requirements = self._extract_field(transcript, "requirement")
        constraints = self._extract_field(transcript, "constraint")
        acceptance = self._extract_field(transcript, "acceptance")
        risks = self._extract_field(transcript, "risk")
        questions = self._extract_field(transcript, "question")

        brief = EngineeringBrief(
            id=brief_id,
            meetingId=meeting_id,
            projectId=project_id,
            requirements=requirements or ["Extracted from meeting discussion"],
            constraints=constraints or [],
            acceptanceCriteria=acceptance or ["All tests pass"],
            risks=risks or [],
            openQuestions=questions or [],
            status=BriefStatus.DRAFT,
        )
        self.briefs[brief_id] = brief
        return brief

    def approve_brief(self, brief_id: str, user_id: str) -> EngineeringBrief:
        """Transitions brief from DRAFT to APPROVED."""
        if brief_id not in self.briefs:
            raise KeyError(f"Brief '{brief_id}' not found.")
        brief = self.briefs[brief_id]
        if brief.status != BriefStatus.DRAFT:
            raise ValueError(f"Brief '{brief_id}' is not in DRAFT state (current: {brief.status}).")
        brief.status = BriefStatus.APPROVED
        return brief

    def reject_brief(self, brief_id: str, user_id: str) -> EngineeringBrief:
        """Keeps brief in DRAFT state for revision."""
        if brief_id not in self.briefs:
            raise KeyError(f"Brief '{brief_id}' not found.")
        brief = self.briefs[brief_id]
        brief.status = BriefStatus.DRAFT
        return brief

    def get_brief(self, brief_id: str) -> EngineeringBrief:
        if brief_id not in self.briefs:
            raise KeyError(f"Brief '{brief_id}' not found.")
        return self.briefs[brief_id]

    @staticmethod
    def _extract_field(transcript: str, keyword: str) -> list[str]:
        """Simple heuristic: find lines containing the keyword."""
        results = []
        for line in transcript.splitlines():
            if keyword.lower() in line.lower():
                content = line.split(":", 1)[-1].strip() if ":" in line else line.strip()
                if content:
                    results.append(content)
        return results
