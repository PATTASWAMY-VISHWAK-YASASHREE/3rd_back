from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.request_models import GenerateRequest
from app.models.test_case_models import TestSuiteResponse
from app.services.gemini_chain import GeminiChain
from app.services.test_parser import TestCaseParser
from app.store.database import get_session
from app.store.repository import TestSuiteRepository

router = APIRouter(prefix="/tests", tags=["Test Generation"])


from app.services.github_service import GitHubService
import logging

logger = logging.getLogger(__name__)

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
        gemini_chain = GeminiChain()
        parser = TestCaseParser()

        # Fetch GitHub context if provided
        context_code = None
        if request.github_repo and request.github_file_path:
            try:
                # Use token from request if provided, else from config/env
                token = request.github_token
                gh_service = GitHubService(token=token)
                context_code = gh_service.fetch_file_content(
                    request.github_repo, 
                    request.github_file_path
                )
                logger.info(f"Fetched {len(context_code)} chars from {request.github_file_path}")
            except Exception as e:
                logger.error(f"Failed to fetch GitHub context: {e}")
                # Don't fail the whole request, just warn and proceed without context
                # or maybe we SHOULD fail? User explicitly asked for it. 
                # Let's append a warning to the story context? 
                # For now, let's log and proceed, effectively falling back to black-box.

        raw_data = await gemini_chain.generate(request, context_code=context_code)
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
