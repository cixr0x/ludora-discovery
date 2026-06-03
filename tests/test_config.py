import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ludora.config import (
    load_dotenv_values,
    resolve_bgg_api_base_url,
    resolve_bgg_api_token,
    resolve_brave_api_key,
    resolve_browser_fetch_enabled,
    resolve_database_url,
)


class ConfigTests(unittest.TestCase):
    def test_load_dotenv_values_reads_key_value_pairs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dotenv_path = Path(temp_dir) / ".env"
            dotenv_path.write_text(
                "\n".join(
                    [
                        "# local secrets",
                        "BRAVE_SEARCH_API_KEY=from_dotenv",
                        "QUOTED_VALUE=\"quoted value\"",
                    ]
                ),
                encoding="utf-8",
            )

            values = load_dotenv_values(dotenv_path)

        self.assertEqual(values["BRAVE_SEARCH_API_KEY"], "from_dotenv")
        self.assertEqual(values["QUOTED_VALUE"], "quoted value")

    def test_resolve_brave_api_key_prefers_cli_then_environment_then_dotenv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dotenv_path = Path(temp_dir) / ".env"
            dotenv_path.write_text("BRAVE_SEARCH_API_KEY=from_dotenv\n", encoding="utf-8")

            self.assertEqual(
                resolve_brave_api_key("from_cli", env={"BRAVE_SEARCH_API_KEY": "from_env"}, dotenv_path=dotenv_path),
                "from_cli",
            )
            self.assertEqual(
                resolve_brave_api_key(None, env={"BRAVE_SEARCH_API_KEY": "from_env"}, dotenv_path=dotenv_path),
                "from_env",
            )
            self.assertEqual(
                resolve_brave_api_key(None, env={}, dotenv_path=dotenv_path),
                "from_dotenv",
            )

    def test_resolve_brave_api_key_returns_empty_string_when_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(resolve_brave_api_key(None, env={}, dotenv_path=Path(temp_dir) / ".env"), "")

    def test_resolve_database_url_prefers_cli_then_environment_then_dotenv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dotenv_path = Path(temp_dir) / ".env"
            dotenv_path.write_text("LUDORA_DATABASE_URL=postgresql://dotenv\n", encoding="utf-8")

            self.assertEqual(
                resolve_database_url(
                    "postgresql://cli",
                    env={"LUDORA_DATABASE_URL": "postgresql://env"},
                    dotenv_path=dotenv_path,
                ),
                "postgresql://cli",
            )
            self.assertEqual(
                resolve_database_url(None, env={"LUDORA_DATABASE_URL": "postgresql://env"}, dotenv_path=dotenv_path),
                "postgresql://env",
            )
            self.assertEqual(
                resolve_database_url(None, env={}, dotenv_path=dotenv_path),
                "postgresql://dotenv",
            )

    def test_resolve_browser_fetch_enabled_prefers_environment_then_dotenv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dotenv_path = Path(temp_dir) / ".env"
            dotenv_path.write_text("LUDORA_BROWSER_FETCH_ENABLED=true\n", encoding="utf-8")

            self.assertTrue(
                resolve_browser_fetch_enabled(
                    env={"LUDORA_BROWSER_FETCH_ENABLED": "yes"},
                    dotenv_path=dotenv_path,
                )
            )
            self.assertFalse(
                resolve_browser_fetch_enabled(
                    env={"LUDORA_BROWSER_FETCH_ENABLED": "false"},
                    dotenv_path=dotenv_path,
                )
            )
            self.assertTrue(resolve_browser_fetch_enabled(env={}, dotenv_path=dotenv_path))

    def test_resolve_browser_fetch_enabled_is_false_when_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertFalse(resolve_browser_fetch_enabled(env={}, dotenv_path=Path(temp_dir) / ".env"))

    def test_resolve_bgg_api_token_prefers_cli_then_environment_then_dotenv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dotenv_path = Path(temp_dir) / ".env"
            dotenv_path.write_text("BGG_API_TOKEN=from_dotenv\n", encoding="utf-8")

            self.assertEqual(
                resolve_bgg_api_token("from_cli", env={"BGG_API_TOKEN": "from_env"}, dotenv_path=dotenv_path),
                "from_cli",
            )
            self.assertEqual(resolve_bgg_api_token(None, env={"BGG_API_TOKEN": "from_env"}, dotenv_path=dotenv_path), "from_env")
            self.assertEqual(resolve_bgg_api_token(None, env={}, dotenv_path=dotenv_path), "from_dotenv")

    def test_resolve_bgg_api_base_url_defaults_to_xmlapi2(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(
                resolve_bgg_api_base_url(env={}, dotenv_path=Path(temp_dir) / ".env"),
                "https://boardgamegeek.com/xmlapi2",
            )


if __name__ == "__main__":
    unittest.main()
