from pydantic import BaseModel, Field, field_validator
from typing import Optional
from enum import Enum
from datetime import datetime
import uuid


class Severity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    TRIVIAL = "trivial"


class ScenarioType(str, Enum):
    HAPPY_PATH = "happy_path"
    EDGE_CASE = "edge_case"
    NEGATIVE = "negative"
    BOUNDARY = "boundary"
    SECURITY = "security"
    PERFORMANCE = "performance"


class TestStep(BaseModel):
    step_number: int = Field(..., ge=1)
    action: str = Field(..., min_length=3)
    input_data: Optional[str] = None
    expected_result: str = Field(..., min_length=3)


class TestCase(BaseModel):
    test_id: str = Field(
        default_factory=lambda: f"TC-{uuid.uuid4().hex[:8].upper()}"
    )
    title: str = Field(..., min_length=5, max_length=300)
    scenario_type: ScenarioType
    severity: Severity
    priority: str = "P1"
    preconditions: list[str] = Field(default_factory=list)
    steps: list[TestStep] = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    is_edge_case: bool = False
    component: str = "General"
    gherkin: Optional[str] = None
    pytest_code: Optional[str] = None

    @field_validator("steps")
    @classmethod
    def steps_sequential(cls, v):
        for i, step in enumerate(v):
            if step.step_number != i + 1:
                step.step_number = i + 1
        return v


class TestSuiteResponse(BaseModel):
    suite_id: str = Field(
        default_factory=lambda: f"TS-{uuid.uuid4().hex[:8].upper()}"
    )
    user_story_summary: str
    component: str
    total_cases: int
    breakdown: dict[str, int] = Field(
        default_factory=lambda: {
            "happy_path": 0,
            "edge_case": 0,
            "negative": 0,
            "boundary": 0,
        }
    )
    test_cases: list[TestCase]
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    format: str = "gherkin"
    project_id: Optional[str] = None
    task_id: Optional[str] = None


class StoredTestSuite(TestSuiteResponse):
    """Extended model for database storage."""
    id: Optional[int] = None
    raw_user_story: str = ""
    raw_acceptance_criteria: list[str] = Field(default_factory=list)
    updated_at: Optional[datetime] = None
