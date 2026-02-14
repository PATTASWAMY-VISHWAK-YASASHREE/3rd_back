"""
Gemini NLP Chain — the core AI engine.

Features:
  - Multi-key rotation (rotates API keys when quota is hit)
  - Token-bucket rate limiter (respects free-tier RPM limits)
  - Model fallback chain (tries primary → fallback models)
  - Multi-turn generation with self-correction
  - Coverage gap filling
"""

import json
import asyncio
import logging
import time
import warnings
from typing import Optional

# Upstream package emits a deprecation warning on import.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", FutureWarning)
    import google.generativeai as genai

from app.config import get_settings
from app.services.prompt_builder import PromptBuilder
from app.models.request_models import GenerateRequest

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple token-bucket rate limiter for API calls."""

    def __init__(self, rpm: int):
        self.rpm = max(rpm, 1)
        self.interval = 60.0 / self.rpm
        self._last_request_time = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.interval:
                wait_time = self.interval - elapsed
                logger.debug(f"Rate limiter: waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
            self._last_request_time = time.monotonic()


class GeminiChain:
    _rate_limiter: Optional[RateLimiter] = None

    def __init__(self):
        self.settings = get_settings()
        self.api_keys = self.settings.get_all_api_keys()
        self.prompt_builder = PromptBuilder()

        if not self.api_keys:
            raise RuntimeError("No Gemini API keys configured")

        # Configure with primary key
        genai.configure(api_key=self.api_keys[0])

        # Model names
        self.primary_model_name = self.settings.gemini_model
        fallback_names = [m.strip() for m in self.settings.gemini_fallback_models.split(",") if m.strip()]
        self.all_model_names = [self.primary_model_name] + [m for m in fallback_names if m != self.primary_model_name]

        # Shared rate limiter
        if GeminiChain._rate_limiter is None:
            GeminiChain._rate_limiter = RateLimiter(rpm=self.settings.gemini_rpm_limit)

        masked_keys = [k[:4] + "***" + k[-4:] if len(k) > 8 else "***" for k in self.api_keys]
        logger.info(f"GeminiChain initialized with {len(self.api_keys)} keys: {masked_keys}")
        logger.info(f"Models: {self.all_model_names}")

    def _create_model(self, model_name: str):
        return genai.GenerativeModel(
            model_name=model_name,
            generation_config=genai.GenerationConfig(
                temperature=self.settings.gemini_temperature,
                top_p=self.settings.gemini_top_p,
                max_output_tokens=self.settings.gemini_max_output_tokens,
            ),
        )

    async def generate(self, request: GenerateRequest, context_code: str = None) -> dict:
        """Main entry: story → test cases dict."""
        prompt = self.prompt_builder.build(request, context_code)

        # TURN 1: Primary generation
        raw_response = await self._call_gemini(prompt)
        parsed = self._extract_json(raw_response)

        if parsed is None:
            # TURN 2: Self-correction
            logger.warning("Turn 1 returned invalid JSON, attempting self-correction")
            correction_prompt = self._build_correction_prompt(raw_response)
            raw_response_2 = await self._call_gemini(correction_prompt)
            parsed = self._extract_json(raw_response_2)

            if parsed is None:
                raise ValueError(
                    "Gemini failed to produce valid JSON after 2 turns. "
                    f"Raw output: {raw_response_2[:500]}"
                )

        # TURN 3 (optional): Coverage gap fill
        parsed = await self._fill_coverage_gaps(parsed, request)
        return parsed

    async def _call_gemini(self, prompt: str, max_retries: int = 3) -> str:
        """
        Calls Gemini with key rotation + model fallback + rate limiting.
        
        Strategy:
          For each API key →
            For each model →
              retry up to max_retries with backoff
              on quota exceeded → try next key/model combo
        """
        all_combos = []
        for key in self.api_keys:
            for model_name in self.all_model_names:
                all_combos.append((key, model_name))

        last_error = None

        for combo_idx, (api_key, model_name) in enumerate(all_combos):
            key_label = f"key-{combo_idx // len(self.all_model_names) + 1}"

            # Switch API key
            genai.configure(api_key=api_key)
            model = self._create_model(model_name)

            for attempt in range(max_retries):
                try:
                    await GeminiChain._rate_limiter.acquire()

                    logger.info(f"Calling {model_name} [{key_label}] "
                                f"(attempt {attempt + 1}/{max_retries})")

                    response = await asyncio.to_thread(
                        model.generate_content, prompt
                    )
                    return response.text  # ← success!

                except Exception as e:
                    last_error = e
                    error_str = str(e)
                    logger.error(f"Error [{model_name}/{key_label}] "
                                 f"(attempt {attempt + 1}): {error_str[:200]}")

                    is_rate_limit = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str
                    is_quota = "quota" in error_str.lower() or "billing" in error_str.lower()

                    if is_rate_limit and is_quota:
                        # Quota exhausted for this key — skip to next combo
                        logger.warning(f"Quota exceeded for {model_name} [{key_label}], "
                                       f"trying next key/model...")
                        break
                    elif is_rate_limit:
                        # Transient rate limit — wait and retry
                        wait_time = (2 ** attempt) * 3  # 3s, 6s, 12s
                        logger.warning(f"Rate limited. Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    elif "404" in error_str or "not found" in error_str.lower():
                        # Model doesn't exist — skip to next model
                        logger.warning(f"Model {model_name} not available, skipping...")
                        break
                    elif attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        logger.warning(f"Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        break

        # All combos failed
        key_count = len(self.api_keys)
        raise RuntimeError(
            f"All {key_count} API key(s) × {len(self.all_model_names)} model(s) exhausted. "
            f"Last error: {last_error}. "
            f"Add more API keys via GEMINI_API_KEYS in .env or wait for quota reset. "
            f"Check: https://ai.google.dev/gemini-api/docs/rate-limits"
        )

    def _extract_json(self, raw: str) -> Optional[dict]:
        """Extracts JSON from Gemini response, handling markdown fences."""
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
        """Checks if all scenario types are covered and fills gaps."""
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

        raw = await self._call_gemini(gap_prompt)
        additional = self._extract_json(raw)

        if additional and "test_cases" in additional:
            parsed["test_cases"].extend(additional["test_cases"])

        return parsed
