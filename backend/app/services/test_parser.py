"""
Parses raw Gemini JSON output into validated Pydantic models.

Validates structure, generates IDs, fills default severity,
deduplicates, and computes suite statistics.
"""

from app.models.test_case_models import (
    TestCase, TestStep, TestSuiteResponse,
    ScenarioType, Severity,
)
from app.models.request_models import GenerateRequest
from app.services.deduplicator import deduplicate_test_cases
import uuid
import logging

logger = logging.getLogger(__name__)

SEVERITY_DEFAULTS = {
    ScenarioType.HAPPY_PATH: Severity.CRITICAL,
    ScenarioType.NEGATIVE: Severity.MAJOR,
    ScenarioType.EDGE_CASE: Severity.MAJOR,
    ScenarioType.BOUNDARY: Severity.MINOR,
    ScenarioType.SECURITY: Severity.CRITICAL,
    ScenarioType.PERFORMANCE: Severity.MINOR,
}


class TestCaseParser:
    def parse(self, raw_data: dict, request: GenerateRequest) -> TestSuiteResponse:
        """Transforms raw Gemini JSON → validated TestSuiteResponse."""
        raw_cases = raw_data.get("test_cases", [])
        parsed_cases: list[TestCase] = []

        for i, raw_case in enumerate(raw_cases):
            try:
                tc = self._parse_single_case(raw_case, request, i)
                parsed_cases.append(tc)
            except Exception as e:
                logger.warning(f"Skipping malformed test case {i}: {e}")

        parsed_cases = deduplicate_test_cases(parsed_cases)

        breakdown = {}
        for tc in parsed_cases:
            t = tc.scenario_type.value
            breakdown[t] = breakdown.get(t, 0) + 1

        return TestSuiteResponse(
            user_story_summary=raw_data.get(
                "user_story_summary",
                request.user_story[:100],
            ),
            component=request.component_context,
            total_cases=len(parsed_cases),
            breakdown=breakdown,
            test_cases=parsed_cases,
            format=request.target_format.value,
            project_id=request.project_id,
            task_id=request.task_id,
        )

    def _parse_single_case(
        self, raw: dict, request: GenerateRequest, index: int
    ) -> TestCase:
        steps = []
        for j, raw_step in enumerate(raw.get("steps", [])):
            step = TestStep(
                step_number=j + 1,
                action=raw_step.get("action", f"Step {j+1}"),
                input_data=raw_step.get("input_data"),
                expected_result=raw_step.get(
                    "expected_result", "Result not specified"
                ),
            )
            steps.append(step)

        scenario_type_str = raw.get("scenario_type", "happy_path")
        try:
            scenario_type = ScenarioType(scenario_type_str)
        except ValueError:
            scenario_type = ScenarioType.HAPPY_PATH

        severity_str = raw.get("severity", "")
        try:
            severity = Severity(severity_str)
        except ValueError:
            severity = SEVERITY_DEFAULTS.get(scenario_type, Severity.MINOR)

        return TestCase(
            test_id=f"TC-{uuid.uuid4().hex[:8].upper()}",
            title=raw.get("title", f"Test Case {index + 1}"),
            scenario_type=scenario_type,
            severity=severity,
            priority=request.priority.value,
            preconditions=raw.get("preconditions", []),
            steps=steps,
            tags=raw.get("tags", []),
            is_edge_case=raw.get("is_edge_case", False)
            or scenario_type
            in (ScenarioType.EDGE_CASE, ScenarioType.BOUNDARY),
            component=request.component_context,
            gherkin=raw.get("gherkin"),
            pytest_code=raw.get("pytest_code"),
        )
