"""Minimal env loader for the project_import sub-skill.

Project convention (blog-article-skill) is that every entry script loads the
**project-root** `.env` via python-dotenv. This loader mirrors that: it loads
`<project_root>/.env` (two levels above this `assets/` dir) and, if present, a
local `tools/project_import/.env` for isolation.

Safe no-op if python-dotenv is missing or the files are absent.
"""
import os

_PROJECT_IMPORT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# assets/ -> project_import/ -> tools/ -> <project_root>/
PROJECT_ROOT = os.path.dirname(os.path.dirname(_PROJECT_IMPORT_DIR))


def load_root_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root_env = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(root_env):
        load_dotenv(root_env)
    local_env = os.path.join(_PROJECT_IMPORT_DIR, ".env")
    if os.path.exists(local_env):
        load_dotenv(local_env)
