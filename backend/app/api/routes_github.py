"""
GitHub OAuth + data routes.
- /auth/github/login      → returns OAuth redirect URL
- /auth/github/callback   → exchanges code for token
- /github/repos           → lists accessible repos
- /github/repos/{owner}/{repo}/tree → file tree
"""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

from app.config import get_settings
from app.services.github_service import GitHubService, resolve_github_token

import logging
import secrets

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth/github", tags=["GitHub Auth"])
github_router = APIRouter(prefix="/github", tags=["GitHub Data"])


@auth_router.get("/login")
async def github_login(
    redirect_uri: Optional[str] = Query(
        default=None,
        description="Deprecated: redirect_uri override is ignored; dashboard callback is always used.",
    ),
):
    """Returns the GitHub OAuth authorization URL."""
    if redirect_uri:
        logger.info("Ignoring redirect_uri override for GitHub OAuth login")
    state = secrets.token_urlsafe(16)
    effective_redirect_uri = GitHubService.get_dashboard_callback_url()
    url = GitHubService.get_oauth_url(state=state)
    return {"url": url, "state": state, "redirect_uri": effective_redirect_uri}


@auth_router.get("/callback")
async def github_callback(
    response: Response,
    code: str = Query(...),
    state: str = Query(default=""),
    redirect_uri: Optional[str] = Query(
        default=None,
        description="Deprecated: redirect_uri override is ignored; dashboard callback is always used.",
    ),
    set_cookie: bool = Query(
        default=True,
        description="Store access token in a secure HttpOnly cookie for global dashboard login.",
    ),
):
    """
    Called after user authorizes on GitHub.
    Exchanges the temporary code for an access token.
    """
    try:
        settings = get_settings()
        if redirect_uri:
            logger.info("Ignoring redirect_uri override for GitHub OAuth callback")

        effective_redirect_uri = GitHubService.get_dashboard_callback_url()
        token_data = await GitHubService.exchange_code_for_token(code)
        access_token = token_data.get("access_token")

        if set_cookie and access_token:
            secure_cookie = effective_redirect_uri.strip().lower().startswith("https://")
            response.set_cookie(
                key=settings.github_token_cookie_name,
                value=access_token,
                httponly=True,
                max_age=settings.github_token_cookie_max_age_seconds,
                secure=secure_cookie,
                samesite="none" if secure_cookie else "lax",
            )
        return {
            "access_token": access_token,
            "token_type": token_data.get("token_type", "bearer"),
            "scope": token_data.get("scope", ""),
            "state": state,
            "redirect_uri": effective_redirect_uri,
            "stored_in_cookie": bool(set_cookie and access_token),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"GitHub OAuth callback error: {e}")
        raise HTTPException(status_code=500, detail="Failed to exchange code for token")


@auth_router.post("/logout")
async def github_logout(response: Response):
    settings = get_settings()
    response.delete_cookie(
        key=settings.github_token_cookie_name,
        samesite="lax",
    )
    return {"status": "ok"}


@github_router.get("/repos")
async def list_repos(
    request: Request,
    token: Optional[str] = Query(default=None, description="GitHub access token"),
    x_github_token: Optional[str] = Header(default=None, alias="X-GitHub-Token"),
):
    """Lists repositories accessible to the authenticated user."""
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

        gh = GitHubService(token=github_token)
        repos = await gh.list_repos()
        return repos
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing repos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@github_router.get("/repos/{owner}/{repo}/tree")
async def get_file_tree(
    request: Request,
    owner: str,
    repo: str,
    token: Optional[str] = Query(default=None, description="GitHub access token"),
    x_github_token: Optional[str] = Header(default=None, alias="X-GitHub-Token"),
    branch: str = Query(default="main", description="Branch name"),
):
    """Returns the recursive file tree for a repository."""
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

        gh = GitHubService(token=github_token)
        tree = await gh.get_file_tree(owner, repo, branch)
        return tree
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting file tree: {e}")
        raise HTTPException(status_code=500, detail=str(e))
