"""
Builds the complete few-shot prompt for Gemini.

Uses Jinja2 templates to separate prompt logic from Python code.
Templates are version-controllable and easy to A/B test.
"""

from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from app.models.request_models import GenerateRequest, TestFormat


TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


class PromptBuilder:
    def __init__(self):
        self.env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.base_template = self.env.get_template("base_prompt.j2")
        self.format_templates = {
            TestFormat.GHERKIN: self.env.get_template("gherkin_format.j2"),
            TestFormat.PLAIN_STEPS: self.env.get_template("plain_steps_format.j2"),
            TestFormat.PYTEST: self.env.get_template("pytest_format.j2"),
        }
        self.examples_template = self.env.get_template("few_shot_examples.j2")

    def build(self, request: GenerateRequest, context_code: str = None) -> str:
        """Constructs the full prompt string ready to send to Gemini."""
        format_schema = self.format_templates[request.target_format].render()

        examples_module = self.examples_template.module
        few_shot_1 = examples_module.example_1()
        few_shot_2 = getattr(examples_module, "example_2", lambda: "{}")()

        prompt = self.base_template.render(
            format_schema=format_schema,
            few_shot_example_1=few_shot_1,
            few_shot_example_2=few_shot_2,
            component_context=request.component_context,
            priority=request.priority.value,
            target_format=request.target_format.value,
            user_story=request.user_story,
            acceptance_criteria=request.acceptance_criteria,
            context_code=context_code,
        )

        return prompt

    def estimate_tokens(self, prompt: str) -> int:
        """Rough token estimation: 1 token ≈ 4 characters for English."""
        return len(prompt) // 4
