"""
Test Runner Service — uses GitHub API to:
1. Create a branch in the user's repo
2. Commit generated test file + GitHub Actions workflow YAML
3. Trigger the workflow via workflow_dispatch
4. Poll for run completion
5. Fetch run logs (pass/fail)
"""

import httpx
import logging
import time
import base64
from typing import Optional

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# Workflow YAML template committed to the repo
WORKFLOW_YAML = """name: Octus Test Run
on:
  workflow_dispatch:
    inputs:
      suite_id:
        description: 'Test Suite ID'
        required: true

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: |
          pip install pytest

      - name: Run tests
        run: |
          pytest octus_tests/ --tb=short -v --junitxml=results.xml 2>&1 | tee test_output.txt

      - name: Upload results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: test-results
          path: |
            results.xml
            test_output.txt
"""


class TestRunnerService:
    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def run_tests(self, repo: str, test_code: str, suite_id: str) -> dict:
        """
        Full pipeline: ensure workflow on default branch → create test branch →
        commit test file → trigger workflow → return run info.
        """
        import asyncio
        branch_name = f"octus/test-run-{suite_id[:8]}-{int(time.time())}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Get default branch SHA
            default_branch = await self._get_default_branch(client, repo)
            logger.info(f"Default branch: {default_branch}")

            # 2. Ensure workflow YAML exists on the DEFAULT branch
            #    (workflow_dispatch REQUIRES the file on the default branch)
            workflow_path = ".github/workflows/octus-tests.yml"
            workflow_is_new = False
            try:
                await self._get_file(client, repo, workflow_path, default_branch)
                logger.info("Workflow YAML already exists on default branch")
            except Exception:
                logger.info("Committing workflow YAML to default branch...")
                await self._commit_file(client, repo, default_branch, workflow_path,
                                         WORKFLOW_YAML, "[Octus] Add test workflow")
                workflow_is_new = True
                # GitHub needs a moment to register new workflows
                logger.info("Waiting for GitHub to register new workflow...")
                await asyncio.sleep(5)

            # 3. Get base SHA (re-fetch in case we just committed to default)
            base_sha = await self._get_branch_sha(client, repo, default_branch)

            # 4. Create test branch
            await self._create_branch(client, repo, branch_name, base_sha)
            logger.info(f"Created branch {branch_name}")

            # 5. Commit test file to the test branch
            test_path = f"octus_tests/test_{suite_id[:8]}.py"
            await self._commit_file(client, repo, branch_name, test_path, test_code,
                                     f"[Octus] Add generated tests for {suite_id}")
            logger.info(f"Committed test file: {test_path}")

            # 6. Trigger workflow dispatch on the test branch
            run_id = await self._trigger_workflow(client, repo, branch_name, suite_id)
            logger.info(f"Triggered workflow, run_id={run_id}")

            return {
                "run_id": run_id,
                "branch": branch_name,
                "repo": repo,
                "status": "queued",
                "test_file": test_path,
            }

    async def get_run_status(self, repo: str, run_id: int) -> dict:
        """Check the status of a workflow run."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/actions/runs/{run_id}",
                headers=self.headers,
            )
            if resp.status_code != 200:
                return {"status": "error", "message": resp.text}

            data = resp.json()
            result = {
                "run_id": run_id,
                "status": data.get("status"),        # queued, in_progress, completed
                "conclusion": data.get("conclusion"), # success, failure, null
                "html_url": data.get("html_url"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
            }

            # If completed, try to get job logs
            if data.get("status") == "completed":
                logs = await self._get_run_logs(client, repo, run_id)
                result["logs"] = logs

            return result

    # ── Internal helpers ──

    async def _get_default_branch(self, client, repo: str) -> str:
        resp = await client.get(f"{GITHUB_API}/repos/{repo}", headers=self.headers)
        resp.raise_for_status()
        return resp.json().get("default_branch", "main")

    async def _get_branch_sha(self, client, repo: str, branch: str) -> str:
        resp = await client.get(
            f"{GITHUB_API}/repos/{repo}/git/ref/heads/{branch}",
            headers=self.headers,
        )
        resp.raise_for_status()
        return resp.json()["object"]["sha"]

    async def _create_branch(self, client, repo: str, branch: str, sha: str):
        resp = await client.post(
            f"{GITHUB_API}/repos/{repo}/git/refs",
            headers=self.headers,
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        if resp.status_code not in (200, 201):
            raise ValueError(f"Failed to create branch: {resp.text}")

    async def _get_file(self, client, repo: str, path: str, branch: str) -> dict:
        resp = await client.get(
            f"{GITHUB_API}/repos/{repo}/contents/{path}",
            headers=self.headers,
            params={"ref": branch},
        )
        resp.raise_for_status()
        return resp.json()

    async def _commit_file(self, client, repo: str, branch: str,
                            path: str, content: str, message: str):
        encoded = base64.b64encode(content.encode()).decode()

        # Check if file already exists on this branch (need SHA for update)
        sha = None
        try:
            existing = await self._get_file(client, repo, path, branch)
            sha = existing.get("sha")
        except Exception:
            pass

        body = {
            "message": message,
            "content": encoded,
            "branch": branch,
        }
        if sha:
            body["sha"] = sha

        resp = await client.put(
            f"{GITHUB_API}/repos/{repo}/contents/{path}",
            headers=self.headers,
            json=body,
        )
        if resp.status_code not in (200, 201):
            raise ValueError(f"Failed to commit {path}: {resp.text}")

    async def _trigger_workflow(self, client, repo: str, branch: str, suite_id: str) -> Optional[int]:
        """Trigger workflow_dispatch and return the run ID."""
        # Trigger
        resp = await client.post(
            f"{GITHUB_API}/repos/{repo}/actions/workflows/octus-tests.yml/dispatches",
            headers=self.headers,
            json={"ref": branch, "inputs": {"suite_id": suite_id}},
        )
        if resp.status_code != 204:
            raise ValueError(f"Failed to trigger workflow: {resp.text}")

        # Wait briefly then find the run
        import asyncio
        await asyncio.sleep(3)

        resp = await client.get(
            f"{GITHUB_API}/repos/{repo}/actions/runs",
            headers=self.headers,
            params={"branch": branch, "per_page": 1},
        )
        if resp.status_code == 200:
            runs = resp.json().get("workflow_runs", [])
            if runs:
                return runs[0]["id"]

        return None

    async def _get_run_logs(self, client, repo: str, run_id: int) -> str:
        """Get the jobs for a run and extract step outputs."""
        resp = await client.get(
            f"{GITHUB_API}/repos/{repo}/actions/runs/{run_id}/jobs",
            headers=self.headers,
        )
        if resp.status_code != 200:
            return "Unable to fetch logs"

        jobs = resp.json().get("jobs", [])
        log_lines = []
        for job in jobs:
            log_lines.append(f"Job: {job['name']} — {job.get('conclusion', 'unknown')}")
            for step in job.get("steps", []):
                status_icon = "✅" if step.get("conclusion") == "success" else "❌"
                log_lines.append(f"  {status_icon} {step['name']} ({step.get('conclusion', '?')})")

        return "\n".join(log_lines)
