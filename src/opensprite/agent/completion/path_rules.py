"""Path classification helpers used by completion evidence checks."""

WEB_APP_ROOT_PATH = "apps/web"
TEST_PATH_PREFIX = "tests/"
PYTHON_FILE_SUFFIX = ".py"
DELEGATED_REVIEW_PATH_SUFFIXES = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".cs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".php",
    ".rb",
    ".swift",
    ".sql",
    ".sh",
    ".ps1",
    ".bat",
    ".cmd",
)
DELEGATED_REVIEW_EXACT_PATHS = frozenset(
    {
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        "vite.config.js",
        "vite.config.ts",
    }
)


def normalized_change_path(path: str | None) -> str:
    return str(path or "").replace("\\", "/").strip("/")


def normalized_touched_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    normalized = [normalized_change_path(path) for path in paths]
    return tuple(path for path in normalized if path)


def is_web_app_path(path: str | None) -> bool:
    normalized = normalized_change_path(path)
    return normalized == WEB_APP_ROOT_PATH or normalized.startswith(f"{WEB_APP_ROOT_PATH}/")


def is_python_file_path(path: str | None) -> bool:
    return normalized_change_path(path).endswith(PYTHON_FILE_SUFFIX)


def is_python_test_path(path: str | None) -> bool:
    normalized = normalized_change_path(path)
    return normalized.startswith(TEST_PATH_PREFIX) and is_python_file_path(normalized)


def strip_repo_snapshot_prefix(path: str) -> str:
    normalized = normalized_change_path(path)
    if normalized.startswith("repo/"):
        return normalized[5:]
    return normalized


def path_requires_delegated_review(path: str) -> bool:
    normalized = strip_repo_snapshot_prefix(path).lower()
    if normalized.endswith(DELEGATED_REVIEW_PATH_SUFFIXES):
        return True
    return normalized in DELEGATED_REVIEW_EXACT_PATHS


def common_verification_path(paths: tuple[str, ...]) -> str | None:
    if not paths:
        return None
    parts_list = [path.split("/") for path in paths if path]
    if not parts_list:
        return None
    common: list[str] = []
    for segments in zip(*parts_list):
        if len(set(segments)) != 1:
            break
        common.append(segments[0])
    if not common:
        return "."
    if len(common) == len(parts_list[0]) and not paths[0].endswith("/"):
        return "/".join(common[:-1]) or "."
    return "/".join(common) or "."
