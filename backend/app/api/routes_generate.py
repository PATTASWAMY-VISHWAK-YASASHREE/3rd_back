from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.models.request_models import GenerateRequest
from app.models.test_case_models import TestSuiteResponse
from app.services.gemini_chain import GeminiChain
from app.services.github_models_chain import GitHubModelsChain
from app.services.test_parser import TestCaseParser
from app.store.database import get_session
from app.store.repository import TestSuiteRepository
from app.config import get_settings

router = APIRouter(prefix="/tests", tags=["Test Generation"])


from app.services.github_service import GitHubService, resolve_github_token
import logging

logger = logging.getLogger(__name__)


def _clean_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"string", "none", "null"}:
        return None
    return cleaned


def _split_repo_full_name(repo_name: str) -> tuple[Optional[str], Optional[str]]:
    if "/" not in repo_name:
        return None, None
    owner, repo = repo_name.split("/", 1)
    owner = owner.strip()
    repo = repo.strip()
    if not owner or not repo:
        return None, None
    return owner, repo


def _truncate_context(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n\n# [File content truncated for context budget]"


def _select_related_paths(tree: list[dict], selected_file: str, limit: int) -> list[str]:
    if limit <= 0:
        return []

    normalized_selected = selected_file.replace("\\", "/").strip("/")
    selected_dir = normalized_selected.rsplit("/", 1)[0] if "/" in normalized_selected else ""
    selected_ext = normalized_selected.rsplit(".", 1)[-1].lower() if "." in normalized_selected else ""

    same_ext_candidates = []
    other_candidates = []
    for item in tree:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path = str(item.get("path", "")).replace("\\", "/").strip("/")
        if not path or path == normalized_selected:
            continue
        path_dir = path.rsplit("/", 1)[0] if "/" in path else ""
        if path_dir != selected_dir:
            continue

        if selected_ext and path.lower().endswith(f".{selected_ext}"):
            same_ext_candidates.append(path)
        else:
            other_candidates.append(path)

    return (same_ext_candidates + other_candidates)[:limit]


def _build_generation_chain():
    settings = get_settings()
    provider = settings.llm_provider.strip().lower()
    has_models_token = bool((settings.github_models_token or "").strip())
    if provider == "github_models" or (provider == "auto" and has_models_token):
        logger.info("Using GitHub Models provider for test generation")
        return GitHubModelsChain()

    logger.info("Using Gemini provider for test generation")
    return GeminiChain()


@router.post("/generate", response_model=TestSuiteResponse)
async def generate_tests(
    http_request: Request,
    request: GenerateRequest,
    x_github_token: Optional[str] = Header(default=None, alias="X-GitHub-Token"),
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
        settings = get_settings()
        generation_chain = _build_generation_chain()
        parser = TestCaseParser()

        # Fetch GitHub context if provided
        context_code = None
        repo_name = _clean_optional_text(request.github_repo)
        file_path = _clean_optional_text(request.github_file_path)
        if repo_name and file_path:
            try:
                # Use token from request if provided, else from config/env
                cookie_token = _clean_optional_text(
                    http_request.cookies.get(settings.github_token_cookie_name)
                )
                token = resolve_github_token(
                    _clean_optional_text(request.github_token),
                    _clean_optional_text(x_github_token),
                    cookie_token,
                    _clean_optional_text(settings.github_token),
                )
                gh_service = GitHubService(token=token)
                max_file_chars = max(settings.github_context_max_file_chars, 1024)
                related_limit = max(settings.github_context_related_files, 0)

                primary_content = gh_service.fetch_file_content(repo_name, file_path)
                context_sections = [
                    f"# FILE: {file_path}\n{_truncate_context(primary_content, max_file_chars)}"
                ]

                if related_limit > 0:
                    owner, repo = _split_repo_full_name(repo_name)
                    if owner and repo:
                        try:
                            tree = await gh_service.get_file_tree(owner, repo)
                            related_paths = _select_related_paths(tree, file_path, related_limit)
                            for related_path in related_paths:
                                try:
                                    related_content = gh_service.fetch_file_content(repo_name, related_path)
                                    context_sections.append(
                                        f"# FILE: {related_path}\n{_truncate_context(related_content, max_file_chars)}"
                                    )
                                except Exception as rel_error:
                                    logger.warning("Skipping related context file %s: %s", related_path, rel_error)
                        except Exception as tree_error:
                            logger.warning("Skipping related context discovery for %s: %s", repo_name, tree_error)
                    else:
                        logger.warning("Skipping related context fetch due to invalid repo format: %s", repo_name)

                context_code = "\n\n".join(context_sections)
                logger.info(
                    "Fetched context bundle: files=%s chars=%s",
                    len(context_sections),
                    len(context_code),
                )
            except Exception as e:
                logger.error(f"Failed to fetch GitHub context: {e}")
                # Don't fail the whole request, just warn and proceed without context
                # or maybe we SHOULD fail? User explicitly asked for it. 
                # Let's append a warning to the story context? 
                # For now, let's log and proceed, effectively falling back to black-box.
        elif request.github_repo or request.github_file_path:
            logger.warning("Skipping GitHub context fetch due to invalid github_repo/github_file_path values")

        raw_data = await generation_chain.generate(request, context_code=context_code)
        strict_mode = bool(getattr(generation_chain, "strict_quality_mode", False))
        min_cases = max(int(getattr(generation_chain, "min_cases", 3)), 1)
        suite = parser.parse(raw_data, request, strict_mode=strict_mode, min_cases=min_cases)

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
