from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from enum import Enum


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class TestFormat(str, Enum):
    GHERKIN = "gherkin"
    PLAIN_STEPS = "plain_steps"
    PYTEST = "pytest"


class GenerateRequest(BaseModel):
    """Exactly what the user submits from the React form."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_story": "As a user, I want to log in so that I can access my dashboard.",
                "acceptance_criteria": [
                    "Given valid credentials, login succeeds",
                    "Given invalid credentials, an error message is shown",
                ],
                "component_context": "Login Page",
                "priority": "P1",
                "target_format": "gherkin",
                "project_id": None,
                "task_id": None,
                "github_repo": None,
                "github_file_path": None,
                "github_token": None,
            }
        }
    )

    user_story: str = Field(
        ...,
        min_length=10,
        max_length=5000,
        description="Free-form English user story",
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="List of acceptance criteria strings",
    )
    component_context: str = Field(
        default="General",
        max_length=200,
        description="Page or component name, e.g. 'Login Page'",
    )
    priority: Priority = Field(
        default=Priority.P1,
        description="Priority level P0-P3",
    )
    target_format: TestFormat = Field(
        default=TestFormat.GHERKIN,
        description="Output format for test cases",
    )
    project_id: Optional[str] = Field(
        default=None,
        description="Link to existing project in store",
    )
    task_id: Optional[str] = Field(
        default=None,
        description="Link to specific task/ticket",
    )
    
    # GitHub Context Fields
    github_repo: Optional[str] = Field(
        default=None,
        description="GitHub repository (owner/repo)",
    )
    github_file_path: Optional[str] = Field(
        default=None,
        description="Path to file in repo to fetch context from",
    )
    github_token: Optional[str] = Field(
        default=None,
        description="Optional personal access token for private repos",
    )
