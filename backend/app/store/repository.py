from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import Optional

from app.store.database import TestSuiteDB
from app.models.test_case_models import TestSuiteResponse


class TestSuiteRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(
        self, suite: TestSuiteResponse, raw_story: str, raw_criteria: list[str]
    ) -> str:
        db_obj = TestSuiteDB(
            suite_id=suite.suite_id,
            user_story=raw_story,
            acceptance_criteria=raw_criteria,
            component=suite.component,
            priority=suite.test_cases[0].priority if suite.test_cases else "P1",
            format=suite.format,
            total_cases=suite.total_cases,
            breakdown=suite.breakdown,
            test_cases_json=suite.model_dump()["test_cases"],
            project_id=suite.project_id,
            task_id=suite.task_id,
        )
        self.session.add(db_obj)
        await self.session.commit()
        return suite.suite_id

    async def get_by_suite_id(
        self, suite_id: str
    ) -> Optional[TestSuiteDB]:
        result = await self.session.execute(
            select(TestSuiteDB).where(TestSuiteDB.suite_id == suite_id)
        )
        return result.scalar_one_or_none()

    async def list_all(
        self, project_id: Optional[str] = None, limit: int = 50
    ) -> list[TestSuiteDB]:
        query = select(TestSuiteDB).order_by(
            TestSuiteDB.created_at.desc()
        ).limit(limit)
        if project_id:
            query = query.where(TestSuiteDB.project_id == project_id)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def delete_suite(self, suite_id: str) -> bool:
        result = await self.session.execute(
            delete(TestSuiteDB).where(TestSuiteDB.suite_id == suite_id)
        )
        await self.session.commit()
        return result.rowcount > 0
