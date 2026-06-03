from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def load_dotenv_values(path: str | Path = ".env") -> dict[str, str]:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_optional_quotes(value.strip())
        if key:
            values[key] = value
    return values


def resolve_brave_api_key(
    cli_value: str | None,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path = ".env",
) -> str:
    if cli_value and cli_value.strip():
        return cli_value.strip()

    current_env = env if env is not None else os.environ
    env_value = current_env.get("BRAVE_SEARCH_API_KEY", "").strip()
    if env_value:
        return env_value

    return load_dotenv_values(dotenv_path).get("BRAVE_SEARCH_API_KEY", "").strip()


def resolve_database_url(
    cli_value: str | None,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path = ".env",
) -> str:
    if cli_value and cli_value.strip():
        return cli_value.strip()

    current_env = env if env is not None else os.environ
    env_value = current_env.get("LUDORA_DATABASE_URL", "").strip()
    if env_value:
        return env_value

    return load_dotenv_values(dotenv_path).get("LUDORA_DATABASE_URL", "").strip()


def resolve_bgg_api_token(
    cli_value: str | None,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path = ".env",
) -> str:
    if cli_value and cli_value.strip():
        return cli_value.strip()

    current_env = env if env is not None else os.environ
    env_value = current_env.get("BGG_API_TOKEN", "").strip()
    if env_value:
        return env_value

    return load_dotenv_values(dotenv_path).get("BGG_API_TOKEN", "").strip()


def resolve_bgg_api_base_url(
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path = ".env",
) -> str:
    current_env = env if env is not None else os.environ
    env_value = current_env.get("BGG_API_BASE_URL", "").strip()
    if env_value:
        return env_value

    dotenv_value = load_dotenv_values(dotenv_path).get("BGG_API_BASE_URL", "").strip()
    return dotenv_value or "https://boardgamegeek.com/xmlapi2"


def resolve_browser_fetch_enabled(
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path = ".env",
) -> bool:
    current_env = env if env is not None else os.environ
    env_value = current_env.get("LUDORA_BROWSER_FETCH_ENABLED", "").strip()
    if env_value:
        return _is_truthy(env_value)

    dotenv_value = load_dotenv_values(dotenv_path).get("LUDORA_BROWSER_FETCH_ENABLED", "").strip()
    return _is_truthy(dotenv_value)


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _is_truthy(value: str) -> bool:
    return value.casefold() in {"1", "true", "yes", "y", "on"}
