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
            if not isinstance(raw_case, dict):
                logger.warning(
                    "Skipping malformed test case %s: expected object, got %s",
                    i,
                    type(raw_case).__name__,
                )
                continue
            try:
                tc = self._parse_single_case(raw_case, request, i)
                parsed_cases.append(tc)
            except Exception as e:
                logger.warning(f"Skipping malformed test case {i}: {e}")

        parsed_cases = deduplicate_test_cases(parsed_cases)
        parsed_cases = self._ensure_required_coverage(parsed_cases, request)

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

    def _normalize_scenario_type(self, raw: dict) -> ScenarioType:
        raw_type = str(raw.get("scenario_type", "")).strip().lower().replace(" ", "_")
        aliases = {
            "happy": ScenarioType.HAPPY_PATH,
            "happy_path": ScenarioType.HAPPY_PATH,
            "positive": ScenarioType.HAPPY_PATH,
            "negative": ScenarioType.NEGATIVE,
            "error": ScenarioType.NEGATIVE,
            "failure": ScenarioType.NEGATIVE,
            "invalid": ScenarioType.NEGATIVE,
            "edge": ScenarioType.EDGE_CASE,
            "edge_case": ScenarioType.EDGE_CASE,
            "boundary": ScenarioType.BOUNDARY,
            "security": ScenarioType.SECURITY,
            "performance": ScenarioType.PERFORMANCE,
        }
        if raw_type in aliases:
            return aliases[raw_type]

        title_text = str(raw.get("title", "")).lower()
        tags_raw = raw.get("tags", [])
        tags_text = " ".join(tags_raw).lower() if isinstance(tags_raw, list) else str(tags_raw).lower()
        hint_text = f"{title_text} {tags_text}"

        if any(k in hint_text for k in ["edge", "boundary", "limit", "corner", "extreme"]):
            return ScenarioType.EDGE_CASE
        if any(k in hint_text for k in ["invalid", "error", "fail", "reject", "unauthorized"]):
            return ScenarioType.NEGATIVE
        if any(k in hint_text for k in ["security", "xss", "csrf", "injection"]):
            return ScenarioType.SECURITY
        if any(k in hint_text for k in ["performance", "load", "latency", "stress"]):
            return ScenarioType.PERFORMANCE

        return ScenarioType.HAPPY_PATH

    def _build_fallback_case(self, scenario_type: ScenarioType, request: GenerateRequest) -> TestCase:
        base_precondition = [f"User is on {request.component_context}"]

        if scenario_type == ScenarioType.NEGATIVE:
            title = "Reject invalid input and return clear error"
            steps = [
                TestStep(
                    step_number=1,
                    action="Submit invalid or unauthorized input",
                    expected_result="System rejects the request with a clear error message",
                ),
                TestStep(
                    step_number=2,
                    action="Check application state after rejection",
                    expected_result="No unintended data or state change is observed",
                ),
            ]
        elif scenario_type == ScenarioType.EDGE_CASE:
            title = "Handle boundary values without breaking flow"
            steps = [
                TestStep(
                    step_number=1,
                    action="Submit boundary or extreme input values",
                    expected_result="System handles input gracefully without crashing",
                ),
                TestStep(
                    step_number=2,
                    action="Verify feedback for out-of-range conditions",
                    expected_result="User receives deterministic and understandable validation feedback",
                ),
            ]
        else:
            title = "Complete primary user flow successfully"
            steps = [
                TestStep(
                    step_number=1,
                    action="Perform the primary action with valid input",
                    expected_result="Operation succeeds and expected output is produced",
                )
            ]

        return TestCase(
            title=title,
            scenario_type=scenario_type,
            severity=SEVERITY_DEFAULTS.get(scenario_type, Severity.MINOR),
            priority=request.priority.value,
            preconditions=base_precondition,
            steps=steps,
            tags=[scenario_type.value, "fallback"],
            is_edge_case=scenario_type in (ScenarioType.EDGE_CASE, ScenarioType.BOUNDARY),
            component=request.component_context,
        )

    def _ensure_required_coverage(
        self, parsed_cases: list[TestCase], request: GenerateRequest
    ) -> list[TestCase]:
        required_types = [ScenarioType.HAPPY_PATH, ScenarioType.NEGATIVE, ScenarioType.EDGE_CASE]
        existing_types = {tc.scenario_type for tc in parsed_cases}

        for required in required_types:
            if required not in existing_types:
                logger.warning(
                    "Coverage fallback: adding missing %s case",
                    required.value,
                )
                parsed_cases.append(self._build_fallback_case(required, request))
                existing_types.add(required)

        return parsed_cases

    def _parse_single_case(
        self, raw: dict, request: GenerateRequest, index: int
    ) -> TestCase:
        steps = []
        for j, raw_step in enumerate(raw.get("steps", [])):
            if isinstance(raw_step, dict):
                action = raw_step.get("action", f"Step {j+1}")
                input_data = raw_step.get("input_data")
                expected_result = raw_step.get(
                    "expected_result", "Result not specified"
                )
            else:
                action = str(raw_step)
                input_data = None
                expected_result = "Result not specified"

            step = TestStep(
                step_number=j + 1,
                action=action,
                input_data=input_data,
                expected_result=expected_result,
            )
            steps.append(step)

        scenario_type = self._normalize_scenario_type(raw)

        severity_str = raw.get("severity", "")
        try:
            severity = Severity(severity_str)
        except ValueError:
            severity = SEVERITY_DEFAULTS.get(scenario_type, Severity.MINOR)

        preconditions_raw = raw.get("preconditions", [])
        if isinstance(preconditions_raw, str):
            preconditions = [preconditions_raw]
        elif isinstance(preconditions_raw, list):
            preconditions = [str(item) for item in preconditions_raw]
        else:
            preconditions = []

        tags_raw = raw.get("tags", [])
        if isinstance(tags_raw, str):
            tags = [tags_raw]
        elif isinstance(tags_raw, list):
            tags = [str(item) for item in tags_raw]
        else:
            tags = []

        return TestCase(
            test_id=f"TC-{uuid.uuid4().hex[:8].upper()}",
            title=raw.get("title", f"Test Case {index + 1}"),
            scenario_type=scenario_type,
            severity=severity,
            priority=request.priority.value,
            preconditions=preconditions,
            steps=steps,
            tags=tags,
            is_edge_case=raw.get("is_edge_case", False)
            or scenario_type
            in (ScenarioType.EDGE_CASE, ScenarioType.BOUNDARY),
            component=request.component_context,
            gherkin=raw.get("gherkin"),
            pytest_code=raw.get("pytest_code"),
        )
