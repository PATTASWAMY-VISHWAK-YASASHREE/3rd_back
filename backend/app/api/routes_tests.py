from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.store.database import get_session
from app.store.repository import TestSuiteRepository

router = APIRouter(prefix="/tests", tags=["Test Management"])


@router.get("/{suite_id}")
async def get_test_suite(
    suite_id: str,
    session: AsyncSession = Depends(get_session),
):
    repo = TestSuiteRepository(session)
    suite = await repo.get_by_suite_id(suite_id)
    if not suite:
        raise HTTPException(404, "Suite not found")
    return suite


@router.get("/")
async def list_suites(
    project_id: Optional[str] = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    repo = TestSuiteRepository(session)
    return await repo.list_all(project_id=project_id, limit=limit)


@router.delete("/{suite_id}")
async def delete_suite(
    suite_id: str,
    session: AsyncSession = Depends(get_session),
):
    repo = TestSuiteRepository(session)
    deleted = await repo.delete_suite(suite_id)
    if not deleted:
        raise HTTPException(404, "Suite not found")
    return {"status": "deleted", "suite_id": suite_id}
