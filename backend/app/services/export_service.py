"""
Export test suites to various formats:
  - .feature (Gherkin/Cucumber)
  - .json (structured)
  - .csv (spreadsheet-friendly)
  - .py (pytest test file)
"""

import json
import csv
import io
from app.models.test_case_models import TestSuiteResponse


class ExportService:
    @staticmethod
    def _single_line(text: str) -> str:
        return " ".join(str(text).splitlines()).strip()

    def to_json(self, suite: TestSuiteResponse) -> str:
        """Export as formatted JSON string."""
        return suite.model_dump_json(indent=2)

    def to_feature(self, suite: TestSuiteResponse) -> str:
        """Export as .feature file (Gherkin/Cucumber format)."""
        lines = [f"Feature: {suite.component}"]
        lines.append(f"  # Generated from: {suite.user_story_summary}")
        lines.append(f"  # Total cases: {suite.total_cases}")
        lines.append("")

        for tc in suite.test_cases:
            if tc.gherkin:
                gherkin_lines = tc.gherkin.strip().split("\n")
                for gl in gherkin_lines:
                    stripped = gl.strip()
                    if stripped.startswith("Feature:"):
                        continue
                    lines.append(f"  {gl}")
                lines.append("")
            else:
                tags = " ".join(tc.tags) if tc.tags else f"@{tc.scenario_type.value}"
                lines.append(f"  {tags}")
                lines.append(f"  Scenario: {tc.title}")

                for pre in tc.preconditions:
                    lines.append(f"    Given {pre}")

                for i, step in enumerate(tc.steps):
                    keyword = "When" if i == 0 and not tc.preconditions else (
                        "When" if i == 0 else (
                            "Then" if i == len(tc.steps) - 1 else "And"
                        )
                    )
                    action = step.action
                    if step.input_data:
                        action += f' "{step.input_data}"'
                    lines.append(f"    {keyword} {action}")

                lines.append("")

        return "\n".join(lines)

    def to_csv(self, suite: TestSuiteResponse) -> str:
        """Export as CSV for spreadsheets."""
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "Test ID", "Title", "Scenario Type", "Severity",
            "Priority", "Preconditions", "Step #", "Action",
            "Input Data", "Expected Result", "Edge Case", "Tags",
        ])

        for tc in suite.test_cases:
            for step in tc.steps:
                writer.writerow([
                    tc.test_id,
                    tc.title,
                    tc.scenario_type.value,
                    tc.severity.value,
                    tc.priority,
                    "; ".join(tc.preconditions),
                    step.step_number,
                    step.action,
                    step.input_data or "",
                    step.expected_result,
                    "Yes" if tc.is_edge_case else "No",
                    ", ".join(tc.tags),
                ])

        return output.getvalue()

    def to_pytest(self, suite: TestSuiteResponse) -> str:
        """Export as pytest file with test functions."""
        lines = [
            '"""',
            f"Auto-generated test suite for: {suite.component}",
            f"Story: {suite.user_story_summary}",
            f"Generated: {suite.generated_at.isoformat()}",
            '"""',
            "",
            "import pytest",
            "",
            "",
        ]

        for tc in suite.test_cases:
            func_name = (
                "test_"
                + tc.title.lower()
                .replace(" ", "_")
                .replace("-", "_")
                .replace("'", "")
                .replace('"', "")
            )
            func_name = "".join(
                c for c in func_name if c.isalnum() or c == "_"
            )[:80]

            markers = []
            if tc.scenario_type.value == "happy_path":
                markers.append("@pytest.mark.smoke")
            if tc.is_edge_case:
                markers.append("@pytest.mark.edge_case")
            if tc.severity.value == "critical":
                markers.append("@pytest.mark.critical")

            for marker in markers:
                lines.append(marker)

            lines.append(f"def {func_name}():")
            lines.append(f'    """')
            lines.append(f"    {tc.title}")
            lines.append(f"    Scenario Type: {tc.scenario_type.value}")
            lines.append(f"    Severity: {tc.severity.value}")
            if tc.preconditions:
                lines.append(f"    Preconditions:")
                for pre in tc.preconditions:
                    lines.append(f"      - {pre}")
            lines.append(f'    """')

            for step in tc.steps:
                action = self._single_line(step.action)
                expected = self._single_line(step.expected_result)
                lines.append(f"    # Step {step.step_number}: {action}")
                if step.input_data:
                    input_data = self._single_line(step.input_data)
                    lines.append(f"    # Input: {input_data}")
                lines.append(f"    # Expected: {expected}")
                lines.append(f"    pass  # TODO: implement")
                lines.append("")

            lines.append("")
            lines.append("")

        return "\n".join(lines)
