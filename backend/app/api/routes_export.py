from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.store.database import get_session
from app.store.repository import TestSuiteRepository
from app.models.test_case_models import TestSuiteResponse
from app.services.export_service import ExportService

router = APIRouter(prefix="/tests", tags=["Export"])

export_service = ExportService()


@router.get("/{suite_id}/export/{format}")
async def export_suite(
    suite_id: str,
    format: str,
    session: AsyncSession = Depends(get_session),
):
    repo = TestSuiteRepository(session)
    suite_db = await repo.get_by_suite_id(suite_id)
    if not suite_db:
        raise HTTPException(404, "Suite not found")

    # Reconstruct TestSuiteResponse from DB
    suite = TestSuiteResponse(
        suite_id=suite_db.suite_id,
        user_story_summary=suite_db.user_story[:100],
        component=suite_db.component,
        total_cases=suite_db.total_cases,
        breakdown=suite_db.breakdown,
        test_cases=suite_db.test_cases_json,
        format=suite_db.format,
    )

    if format == "json":
        content = export_service.to_json(suite)
        return Response(
            content=content,
            media_type="application/json",
            headers={
                "Content-Disposition":
                f'attachment; filename="{suite_id}.json"'
            },
        )
    elif format == "feature":
        content = export_service.to_feature(suite)
        return PlainTextResponse(
            content=content,
            headers={
                "Content-Disposition":
                f'attachment; filename="{suite_id}.feature"'
            },
        )
    elif format == "csv":
        content = export_service.to_csv(suite)
        return Response(
            content=content,
            media_type="text/csv",
            headers={
                "Content-Disposition":
                f'attachment; filename="{suite_id}.csv"'
            },
        )
    elif format == "pytest":
        content = export_service.to_pytest(suite)
        return PlainTextResponse(
            content=content,
            headers={
                "Content-Disposition":
                f'attachment; filename="test_{suite_id}.py"'
            },
        )
    else:
        raise HTTPException(400, f"Unknown format: {format}")
