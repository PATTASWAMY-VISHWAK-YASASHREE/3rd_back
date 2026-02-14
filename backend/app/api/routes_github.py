"""
GitHub OAuth + data routes.
- /auth/github/login      → returns OAuth redirect URL
- /auth/github/callback   → exchanges code for token
- /github/repos           → lists accessible repos
- /github/repos/{owner}/{repo}/tree → file tree
"""

from fastapi import APIRouter, HTTPException, Query
from app.services.github_service import GitHubService

import logging
import secrets

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth/github", tags=["GitHub Auth"])
github_router = APIRouter(prefix="/github", tags=["GitHub Data"])


@auth_router.get("/login")
async def github_login():
    """Returns the GitHub OAuth authorization URL."""
    state = secrets.token_urlsafe(16)
    url = GitHubService.get_oauth_url(state=state)
    return {"url": url, "state": state}


@auth_router.get("/callback")
async def github_callback(code: str = Query(...), state: str = Query(default="")):
    """
    Called after user authorizes on GitHub.
    Exchanges the temporary code for an access token.
    """
    try:
        token_data = await GitHubService.exchange_code_for_token(code)
        return {
            "access_token": token_data.get("access_token"),
            "token_type": token_data.get("token_type", "bearer"),
            "scope": token_data.get("scope", ""),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"GitHub OAuth callback error: {e}")
        raise HTTPException(status_code=500, detail="Failed to exchange code for token")


@github_router.get("/repos")
async def list_repos(token: str = Query(..., description="GitHub access token")):
    """Lists repositories accessible to the authenticated user."""
    try:
        gh = GitHubService(token=token)
        repos = await gh.list_repos()
        return repos
    except Exception as e:
        logger.error(f"Error listing repos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@github_router.get("/repos/{owner}/{repo}/tree")
async def get_file_tree(
    owner: str,
    repo: str,
    token: str = Query(..., description="GitHub access token"),
    branch: str = Query(default="main", description="Branch name"),
):
    """Returns the recursive file tree for a repository."""
    try:
        gh = GitHubService(token=token)
        tree = await gh.get_file_tree(owner, repo, branch)
        return tree
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting file tree: {e}")
        raise HTTPException(status_code=500, detail=str(e))
