"""
GitHub Models chain for test generation.
"""

import asyncio
import json
import logging
import time
from datetime import date
from typing import Optional

import httpx

from app.config import get_settings
from app.models.request_models import GenerateRequest
from app.services.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)

TEST_SUITE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["user_story_summary", "test_cases"],
    "properties": {
        "user_story_summary": {"type": "string"},
        "test_cases": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "title",
                    "scenario_type",
                    "severity",
                    "preconditions",
                    "steps",
                    "tags",
                    "is_edge_case",
                ],
                "properties": {
                    "title": {"type": "string"},
                    "scenario_type": {
                        "type": "string",
                        "enum": [
                            "happy_path",
                            "negative",
                            "edge_case",
                            "boundary",
                            "security",
                            "performance",
                        ],
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "major", "minor", "trivial"],
                    },
                    "preconditions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["step_number", "action", "expected_result"],
                            "properties": {
                                "step_number": {"type": "integer", "minimum": 1},
                                "action": {"type": "string"},
                                "input_data": {"type": ["string", "null"]},
                                "expected_result": {"type": "string"},
                            },
                        },
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "is_edge_case": {"type": "boolean"},
                    "gherkin": {"type": ["string", "null"]},
                    "pytest_code": {"type": ["string", "null"]},
                },
            },
        },
    },
}

GAP_FILL_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["test_cases"],
    "properties": {
        "test_cases": TEST_SUITE_JSON_SCHEMA["properties"]["test_cases"],
    },
}


class RateLimiter:
    """Simple rate limiter for API calls (RPM pacing + daily cap)."""

    def __init__(self, rpm: int, rpd: int):
        self.rpm = max(rpm, 1)
        self.rpd = max(rpd, 1)
        self.interval = 60.0 / self.rpm
        self._last_request_time = 0.0
        self._window_day = date.today()
        self._requests_today = 0
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            today = date.today()
            if today != self._window_day:
                self._window_day = today
                self._requests_today = 0

            if self._requests_today >= self.rpd:
                raise RuntimeError(
                    f"GitHub Models daily request limit reached ({self.rpd} requests/day)."
                )

            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.interval:
                wait_time = self.interval - elapsed
                logger.debug(f"Rate limiter: waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
            self._last_request_time = time.monotonic()
            self._requests_today += 1


class GitHubModelsChain:
    _rate_limiter: Optional[RateLimiter] = None

    def __init__(self):
        self.settings = get_settings()
        self.prompt_builder = PromptBuilder()
        self.model_name = self.settings.github_models_model
        self.max_retries = max(self.settings.github_models_max_retries, 1)
        self.max_input_tokens = max(self.settings.github_models_max_input_tokens, 512)
        self.enable_gap_fill = bool(self.settings.github_models_enable_gap_fill)
        self.enable_json_schema = bool(self.settings.github_models_enable_json_schema)
        self.json_schema_strict = bool(self.settings.github_models_json_schema_strict)
        self.strict_quality_mode = bool(self.settings.github_models_strict_quality_mode)
        self.min_cases = max(self.settings.github_models_min_cases, 1)
        self.token = (self.settings.github_models_token or "").strip()
        if not self.token:
            raise RuntimeError("No GitHub Models token configured")

        base = self.settings.github_models_api_base.rstrip("/")
        org = (self.settings.github_models_org or "").strip()
        if org:
            self.endpoint = f"{base}/orgs/{org}/inference/chat/completions"
        else:
            self.endpoint = f"{base}/inference/chat/completions"

        if GitHubModelsChain._rate_limiter is None:
            GitHubModelsChain._rate_limiter = RateLimiter(
                rpm=self.settings.github_models_rpm_limit,
                rpd=self.settings.github_models_rpd_limit,
            )

        logger.info(f"GitHubModelsChain initialized with model: {self.model_name}")
        logger.info(
            "Rate limits: %s RPM, %s requests/day",
            self.settings.github_models_rpm_limit,
            self.settings.github_models_rpd_limit,
        )
        logger.info(
            "Generation options: retries=%s, gap_fill=%s, json_schema=%s, strict_mode=%s, max_input_tokens=%s",
            self.max_retries,
            self.enable_gap_fill,
            self.enable_json_schema,
            self.strict_quality_mode,
            self.max_input_tokens,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": self.settings.github_models_api_version,
            "Content-Type": "application/json",
        }

    def _token_limit_payload(self) -> dict[str, int]:
        if "gpt-5" in self.model_name.lower():
            return {"max_completion_tokens": self.settings.github_models_max_output_tokens}
        return {"max_tokens": self.settings.github_models_max_output_tokens}

    def _sampling_payload(self) -> dict[str, float]:
        if "gpt-5" in self.model_name.lower():
            return {}
        return {"temperature": self.settings.github_models_temperature}

    def _build_messages(self, prompt: str) -> list[dict[str, str]]:
        system_prompt = (
            "You are a senior QA automation engineer. "
            "Generate comprehensive, diverse test cases with strong negative and edge coverage. "
            "Return only JSON with no markdown wrappers."
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

    def _response_format_payload(self) -> dict:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "generated_test_suite",
                "strict": self.json_schema_strict,
                "schema": TEST_SUITE_JSON_SCHEMA,
            },
        }

    def _gap_fill_response_format_payload(self) -> dict:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "generated_gap_fill_cases",
                "strict": self.json_schema_strict,
                "schema": GAP_FILL_JSON_SCHEMA,
            },
        }

    def _is_response_format_error(self, message: str) -> bool:
        lowered = (message or "").lower()
        return any(
            key in lowered
            for key in [
                "response_format",
                "json_schema",
                "schema",
                "invalid parameter",
                "unsupported",
            ]
        )

    def _build_prompt_with_budget(
        self, request: GenerateRequest, context_code: Optional[str]
    ) -> str:
        prompt = self.prompt_builder.build(request, context_code=context_code)
        estimated_tokens = self.prompt_builder.estimate_tokens(prompt)
        if estimated_tokens <= self.max_input_tokens:
            return prompt

        base_prompt = self.prompt_builder.build(request, context_code=None)
        base_tokens = self.prompt_builder.estimate_tokens(base_prompt)
        if not context_code or base_tokens >= self.max_input_tokens:
            logger.warning(
                "Prompt estimate %s exceeds max input tokens %s with no truncatable context",
                estimated_tokens,
                self.max_input_tokens,
            )
            return base_prompt if base_tokens < estimated_tokens else prompt

        available_context_tokens = max(self.max_input_tokens - base_tokens - 128, 256)
        max_context_chars = max(1024, available_context_tokens * 4)
        trimmed_context = context_code[:max_context_chars]
        if len(context_code) > max_context_chars:
            trimmed_context += "\n\n# Context truncated to fit model token budget."

        trimmed_prompt = self.prompt_builder.build(request, context_code=trimmed_context)
        trimmed_estimate = self.prompt_builder.estimate_tokens(trimmed_prompt)
        logger.warning(
            "Prompt token estimate over budget (%s>%s). Context truncated from %s to %s chars (estimate=%s).",
            estimated_tokens,
            self.max_input_tokens,
            len(context_code),
            len(trimmed_context),
            trimmed_estimate,
        )
        return trimmed_prompt

    def _extract_text(self, response_data: dict) -> str:
        choices = response_data.get("choices", [])
        if not choices:
            return ""

        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    chunks.append(item["text"])
            return "".join(chunks)
        return ""

    def _error_message(self, response: httpx.Response) -> str:
        try:
            data = response.json()
            if isinstance(data, dict):
                if isinstance(data.get("error"), dict):
                    return str(data["error"].get("message", data["error"]))
                return str(data.get("message", data))
            return str(data)
        except Exception:
            return response.text[:500]

    async def _call_model(
        self,
        prompt: str,
        response_format: Optional[dict] = None,
        allow_response_format_fallback: bool = False,
    ) -> str:
        last_error = None
        for attempt in range(self.max_retries):
            try:
                await GitHubModelsChain._rate_limiter.acquire()
                payload = {
                    "model": self.model_name,
                    "messages": self._build_messages(prompt),
                    "stream": False,
                    **self._token_limit_payload(),
                    **self._sampling_payload(),
                }
                if response_format:
                    payload["response_format"] = response_format

                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        self.endpoint,
                        headers=self._headers(),
                        json=payload,
                    )

                if response.status_code == 200:
                    text = self._extract_text(response.json()).strip()
                    if not text:
                        raise RuntimeError("GitHub Models returned an empty response")
                    return text

                error_message = self._error_message(response)
                if (
                    response_format
                    and allow_response_format_fallback
                    and response.status_code in (400, 422)
                    and self._is_response_format_error(error_message)
                ):
                    logger.warning(
                        "response_format rejected by model/API (%s). Retrying without schema constraints.",
                        error_message,
                    )
                    return await self._call_model(
                        prompt,
                        response_format=None,
                        allow_response_format_fallback=False,
                    )

                last_error = RuntimeError(f"{response.status_code} {error_message}")
                logger.error(
                    "GitHub Models error (attempt %s/%s): %s",
                    attempt + 1,
                    self.max_retries,
                    last_error,
                )

                if response.status_code == 429 and attempt < self.max_retries - 1:
                    wait_time = (2 ** attempt) * 3
                    await asyncio.sleep(wait_time)
                    continue
                break

            except Exception as e:
                last_error = e
                logger.error(
                    "GitHub Models request failed (attempt %s/%s): %s",
                    attempt + 1,
                    self.max_retries,
                    e,
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                break

        raise RuntimeError(f"GitHub Models generation failed. Last error: {last_error}")

    async def generate(self, request: GenerateRequest, context_code: str = None) -> dict:
        prompt = self._build_prompt_with_budget(request, context_code)
        response_format = (
            self._response_format_payload() if self.enable_json_schema else None
        )

        raw_response = await self._call_model(
            prompt,
            response_format=response_format,
            allow_response_format_fallback=self.enable_json_schema,
        )
        parsed = self._extract_json(raw_response)

        if parsed is None:
            logger.warning("Turn 1 returned invalid JSON, attempting self-correction")
            correction_prompt = self._build_correction_prompt(raw_response)
            raw_response_2 = await self._call_model(
                correction_prompt,
                response_format=response_format,
                allow_response_format_fallback=self.enable_json_schema,
            )
            parsed = self._extract_json(raw_response_2)

            if parsed is None:
                raise ValueError(
                    "Model failed to produce valid JSON after 2 turns. "
                    f"Raw output: {raw_response_2[:500]}"
                )

        if self.enable_gap_fill:
            parsed = await self._fill_coverage_gaps(parsed, request)
        return parsed

    def _extract_json(self, raw: str) -> Optional[dict]:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

        return None

    def _build_correction_prompt(self, malformed_output: str) -> str:
        return f"""The following output was supposed to be valid JSON
matching a test case schema, but it has syntax errors.

Fix it and return ONLY valid JSON. Do not explain.

BROKEN OUTPUT:
{malformed_output[:3000]}

Return the corrected JSON:"""

    async def _fill_coverage_gaps(
        self, parsed: dict, request: GenerateRequest
    ) -> dict:
        if "test_cases" not in parsed:
            return parsed

        type_counts = {}
        for tc in parsed["test_cases"]:
            t = tc.get("scenario_type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        missing = []
        required_types = ["happy_path", "negative", "edge_case"]
        for rt in required_types:
            if type_counts.get(rt, 0) == 0:
                missing.append(rt)

        if not missing:
            return parsed

        logger.info(f"Coverage gap detected. Missing types: {missing}")
        gap_prompt = f"""Given this user story:
"{request.user_story}"

Generate exactly {len(missing)} additional test cases for these
MISSING scenario types: {', '.join(missing)}.

Return them in the same JSON format as before. Return ONLY a JSON
object with a "test_cases" array containing the new cases."""

        gap_response_format = (
            self._gap_fill_response_format_payload() if self.enable_json_schema else None
        )
        raw = await self._call_model(
            gap_prompt,
            response_format=gap_response_format,
            allow_response_format_fallback=self.enable_json_schema,
        )
        additional = self._extract_json(raw)
        if additional and "test_cases" in additional:
            parsed["test_cases"].extend(additional["test_cases"])

        return parsed
