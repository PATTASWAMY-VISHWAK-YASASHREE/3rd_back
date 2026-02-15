"""
Test Runner API routes.
- POST /tests/{suite_id}/run   → triggers test execution via GitHub Actions
- GET  /tests/runs/{run_id}/status → polls for run status + results
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from app.services.test_runner_service import TestRunnerService
from app.services.export_service import ExportService
from app.store.database import get_session
from app.store.repository import TestSuiteRepository
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.config import get_settings
from app.services.github_service import resolve_github_token

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tests", tags=["Test Runner"])


@router.post("/{suite_id}/run")
async def run_tests(
    suite_id: str,
    request: Request,
    repo: str = Query(..., description="GitHub repo (owner/repo)"),
    token: Optional[str] = Query(default=None, description="GitHub access token"),
    x_github_token: Optional[str] = Header(default=None, alias="X-GitHub-Token"),
    session: AsyncSession = Depends(get_session),
):
    """
    Triggers test execution via GitHub Actions.
    1. Loads the test suite from DB
    2. Exports it as pytest code
    3. Creates branch, commits tests, triggers workflow
    4. Returns run_id for polling
    """
    try:
        settings = get_settings()
        cookie_token = request.cookies.get(settings.github_token_cookie_name)
        github_token = resolve_github_token(
            x_github_token,
            cookie_token,
            token,
            settings.github_token,
        )
        if not github_token:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Missing GitHub token. Send query 'token', header 'X-GitHub-Token', "
                    "or authenticate once via /auth/github/callback cookie."
                ),
            )

        # Load suite from DB
        repo_db = TestSuiteRepository(session)
        suite_record = await repo_db.get_by_suite_id(suite_id)
        if not suite_record:
            raise HTTPException(status_code=404, detail="Test suite not found")

        # Reconstruct TestSuiteResponse from DB data for export
        from app.models.test_case_models import TestSuiteResponse
        suite_obj = TestSuiteResponse(
            suite_id=suite_record.suite_id,
            component=suite_record.component or "Test Suite",
            user_story_summary=suite_record.user_story or "",
            format=suite_record.format or "plain_steps",
            total_cases=suite_record.total_cases or 0,
            breakdown=suite_record.breakdown or {},
            test_cases=suite_record.test_cases_json or [],
        )

        # Export as pytest format
        export_svc = ExportService()
        test_code = export_svc.to_pytest(suite_obj)

        # Run via GitHub Actions
        runner = TestRunnerService(token=github_token)
        result = await runner.run_tests(repo, test_code, suite_id)

        return result

    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Test run failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}/status")
async def get_run_status(
    run_id: int,
    request: Request,
    repo: str = Query(..., description="GitHub repo (owner/repo)"),
    token: Optional[str] = Query(default=None, description="GitHub access token"),
    x_github_token: Optional[str] = Header(default=None, alias="X-GitHub-Token"),
):
    """Returns the current status and results of a GitHub Actions workflow run."""
    try:
        settings = get_settings()
        cookie_token = request.cookies.get(settings.github_token_cookie_name)
        github_token = resolve_github_token(
            x_github_token,
            cookie_token,
            token,
            settings.github_token,
        )
        if not github_token:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Missing GitHub token. Send query 'token', header 'X-GitHub-Token', "
                    "or authenticate once via /auth/github/callback cookie."
                ),
            )

        runner = TestRunnerService(token=github_token)
        result = await runner.get_run_status(repo, run_id)
        return result
    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
