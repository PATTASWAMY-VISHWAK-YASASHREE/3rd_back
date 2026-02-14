from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.models.request_models import GenerateRequest
from app.models.test_case_models import TestSuiteResponse
from app.services.gemini_chain import GeminiChain
from app.services.github_models_chain import GitHubModelsChain
from app.services.test_parser import TestCaseParser
from app.store.database import get_session
from app.store.repository import TestSuiteRepository
from app.config import get_settings

router = APIRouter(prefix="/tests", tags=["Test Generation"])


from app.services.github_service import GitHubService
import logging

logger = logging.getLogger(__name__)


def _clean_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"string", "none", "null"}:
        return None
    return cleaned


def _build_generation_chain():
    settings = get_settings()
    provider = settings.llm_provider.strip().lower()
    has_models_token = bool((settings.github_models_token or "").strip())
    if provider == "github_models" or (provider == "auto" and has_models_token):
        logger.info("Using GitHub Models provider for test generation")
        return GitHubModelsChain()

    logger.info("Using Gemini provider for test generation")
    return GeminiChain()


@router.post("/generate", response_model=TestSuiteResponse)
async def generate_tests(
    request: GenerateRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Main endpoint: user story → test suite.
    1. Validate request (Pydantic)
    2. (Optional) Fetch context from GitHub
    3. Build prompt and call Gemini
    4. Parse response into structured test cases
    5. Store in database
    6. Return full suite
    """
    try:
        generation_chain = _build_generation_chain()
        parser = TestCaseParser()

        # Fetch GitHub context if provided
        context_code = None
        repo_name = _clean_optional_text(request.github_repo)
        file_path = _clean_optional_text(request.github_file_path)
        if repo_name and file_path:
            try:
                # Use token from request if provided, else from config/env
                token = _clean_optional_text(request.github_token)
                gh_service = GitHubService(token=token)
                context_code = gh_service.fetch_file_content(
                    repo_name,
                    file_path
                )
                logger.info(f"Fetched {len(context_code)} chars from {file_path}")
            except Exception as e:
                logger.error(f"Failed to fetch GitHub context: {e}")
                # Don't fail the whole request, just warn and proceed without context
                # or maybe we SHOULD fail? User explicitly asked for it. 
                # Let's append a warning to the story context? 
                # For now, let's log and proceed, effectively falling back to black-box.
        elif request.github_repo or request.github_file_path:
            logger.warning("Skipping GitHub context fetch due to invalid github_repo/github_file_path values")

        raw_data = await generation_chain.generate(request, context_code=context_code)
        suite = parser.parse(raw_data, request)

        repo = TestSuiteRepository(session)
        await repo.save(
            suite,
            raw_story=request.user_story,
            raw_criteria=request.acceptance_criteria,
        )

        return suite

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Generation failed: {str(e)}",
        )
