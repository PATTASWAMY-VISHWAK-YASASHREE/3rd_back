"""
GitHub Service — handles OAuth token exchange, repo listing, file tree, and content fetching.
Uses httpx for async HTTP calls to the GitHub API.
"""

import httpx
import logging
from typing import Optional
from github import Github, Auth
from github.GithubException import GithubException

from app.config import get_settings

logger = logging.getLogger(__name__)

GITHUB_OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE = "https://api.github.com"


class GitHubService:
    def __init__(self, token: Optional[str] = None):
        self.token = token
        self.headers = {
            "Accept": "application/json",
            "User-Agent": "Octus-TestGen",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    @staticmethod
    def get_oauth_url(state: str = "") -> str:
        """Returns the GitHub OAuth authorization URL for the user to visit."""
        settings = get_settings()
        params = {
            "client_id": settings.github_client_id,
            "redirect_uri": settings.github_callback_url,
            "scope": "repo read:org",
            "state": state,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{GITHUB_OAUTH_AUTHORIZE_URL}?{query}"

    @staticmethod
    async def exchange_code_for_token(code: str) -> dict:
        """
        Exchanges the OAuth authorization code for an access token.
        Returns: { "access_token": "...", "token_type": "bearer", "scope": "..." }
        """
        settings = get_settings()
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GITHUB_OAUTH_TOKEN_URL,
                data={
                    "client_id": settings.github_client_id,
                    "client_secret": settings.github_client_secret,
                    "code": code,
                    "redirect_uri": settings.github_callback_url,
                },
                headers={"Accept": "application/json"},
            )
            data = response.json()
            logger.info(f"GitHub token exchange response: {list(data.keys())}")

            if "error" in data:
                logger.error(f"GitHub OAuth error: {data}")
                raise ValueError(
                    f"GitHub OAuth error: {data.get('error_description', data['error'])}"
                )

            return data

    async def list_repos(self) -> list[dict]:
        """Lists repositories accessible to the authenticated user."""
        repos = []
        page = 1
        async with httpx.AsyncClient() as client:
            while True:
                response = await client.get(
                    f"{GITHUB_API_BASE}/user/repos",
                    headers=self.headers,
                    params={
                        "per_page": 100,
                        "page": page,
                        "sort": "updated",
                        "direction": "desc",
                    },
                )
                if response.status_code != 200:
                    logger.error(f"GitHub API error listing repos: {response.text}")
                    break

                batch = response.json()
                if not batch:
                    break

                for repo in batch:
                    repos.append({
                        "full_name": repo["full_name"],
                        "name": repo["name"],
                        "owner": repo["owner"]["login"],
                        "private": repo["private"],
                        "default_branch": repo.get("default_branch", "main"),
                        "language": repo.get("language"),
                        "updated_at": repo.get("updated_at"),
                    })
                page += 1
                if len(batch) < 100:
                    break

        return repos

    async def get_file_tree(self, owner: str, repo: str, branch: str = "main") -> list[dict]:
        """
        Returns the recursive file tree for a repo.
        Each item: { "path": "src/App.jsx", "type": "blob"|"tree", "size": 1234 }
        Falls back to Contents API if Git Trees API returns empty.
        """
        async with httpx.AsyncClient() as client:
            # Try Git Trees API first (fast, recursive)
            response = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{branch}",
                headers=self.headers,
                params={"recursive": "1"},
            )
            logger.info(f"Git Trees API status={response.status_code} for {owner}/{repo}")

            if response.status_code == 200:
                data = response.json()
                tree = data.get("tree", [])
                logger.info(f"Git Trees API returned {len(tree)} items (truncated={data.get('truncated', False)})")

                files = []
                for item in tree:
                    if item["type"] == "blob":
                        files.append({
                            "path": item["path"],
                            "type": item["type"],
                            "size": item.get("size", 0),
                        })

                if files:
                    return files

            # Fallback: use Contents API (works better with some token types)
            logger.info(f"Falling back to Contents API for {owner}/{repo}")
            return await self._get_tree_via_contents(client, owner, repo, branch)

    async def _get_tree_via_contents(self, client, owner: str, repo: str, branch: str, path: str = "") -> list[dict]:
        """Recursively walk the repo via the Contents API as a fallback."""
        response = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}",
            headers=self.headers,
            params={"ref": branch},
        )
        if response.status_code != 200:
            logger.error(f"Contents API error: {response.status_code} — {response.text[:200]}")
            return []

        items = response.json()
        if not isinstance(items, list):
            items = [items]

        files = []
        for item in items:
            if item["type"] == "file":
                files.append({
                    "path": item["path"],
                    "type": "blob",
                    "size": item.get("size", 0),
                })
            elif item["type"] == "dir":
                # Recurse into subdirectories
                sub_files = await self._get_tree_via_contents(client, owner, repo, branch, item["path"])
                files.extend(sub_files)

        return files

    def fetch_file_content(self, repo_full_name: str, file_path: str) -> str:
        """
        Fetches raw content of a file from a GitHub repository.
        Uses PyGithub for authenticated access.
        """
        try:
            auth = Auth.Token(self.token) if self.token else None
            github = Github(auth=auth)
            repo = github.get_repo(repo_full_name)
            content_file = repo.get_contents(file_path)
            return content_file.decoded_content.decode("utf-8")

        except GithubException as e:
            logger.error(f"GitHub API error fetching {file_path} from {repo_full_name}: {e}")
            if e.status == 404:
                raise ValueError(f"File '{file_path}' not found in repo '{repo_full_name}'")
            elif e.status == 401:
                raise ValueError("Invalid GitHub token or unauthorized access")
            else:
                raise ValueError(f"GitHub Error: {e.data.get('message', str(e))}")
