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

    async def _call_model(self, prompt: str, max_retries: int = 3) -> str:
        last_error = None
        for attempt in range(max_retries):
            try:
                await GitHubModelsChain._rate_limiter.acquire()
                payload = {
                    "model": self.model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    **self._token_limit_payload(),
                    **self._sampling_payload(),
                }

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
                last_error = RuntimeError(f"{response.status_code} {error_message}")
                logger.error(
                    "GitHub Models error (attempt %s/%s): %s",
                    attempt + 1,
                    max_retries,
                    last_error,
                )

                if response.status_code == 429 and attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 3
                    await asyncio.sleep(wait_time)
                    continue
                break

            except Exception as e:
                last_error = e
                logger.error(
                    "GitHub Models request failed (attempt %s/%s): %s",
                    attempt + 1,
                    max_retries,
                    e,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                break

        raise RuntimeError(f"GitHub Models generation failed. Last error: {last_error}")

    async def generate(self, request: GenerateRequest, context_code: str = None) -> dict:
        prompt = self.prompt_builder.build(request, context_code)

        raw_response = await self._call_model(prompt)
        parsed = self._extract_json(raw_response)

        if parsed is None:
            logger.warning("Turn 1 returned invalid JSON, attempting self-correction")
            correction_prompt = self._build_correction_prompt(raw_response)
            raw_response_2 = await self._call_model(correction_prompt)
            parsed = self._extract_json(raw_response_2)

            if parsed is None:
                raise ValueError(
                    "Model failed to produce valid JSON after 2 turns. "
                    f"Raw output: {raw_response_2[:500]}"
                )

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

        raw = await self._call_model(gap_prompt)
        additional = self._extract_json(raw)
        if additional and "test_cases" in additional:
            parsed["test_cases"].extend(additional["test_cases"])

        return parsed
