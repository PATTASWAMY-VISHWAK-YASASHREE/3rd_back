"""
Test Runner API routes.
- POST /tests/{suite_id}/run   → triggers test execution via GitHub Actions
- GET  /tests/runs/{run_id}/status → polls for run status + results
"""

from fastapi import APIRouter, HTTPException, Query
from app.services.test_runner_service import TestRunnerService
from app.services.export_service import ExportService
from app.store.database import get_session
from app.store.repository import TestSuiteRepository
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tests", tags=["Test Runner"])


@router.post("/{suite_id}/run")
async def run_tests(
    suite_id: str,
    repo: str = Query(..., description="GitHub repo (owner/repo)"),
    token: str = Query(..., description="GitHub access token"),
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
        runner = TestRunnerService(token=token)
        result = await runner.run_tests(repo, test_code, suite_id)

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Test run failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}/status")
async def get_run_status(
    run_id: int,
    repo: str = Query(..., description="GitHub repo (owner/repo)"),
    token: str = Query(..., description="GitHub access token"),
):
    """Returns the current status and results of a GitHub Actions workflow run."""
    try:
        runner = TestRunnerService(token=token)
        result = await runner.get_run_status(repo, run_id)
        return result
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
