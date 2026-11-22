import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_catalogs import build_release
from scripts.validate_catalog import validate_current, validate_version


class ReleaseTests(unittest.TestCase):
    def _config(self, root, cloud, telegram):
        root.mkdir(parents=True, exist_ok=True)
        (root / "cloudstream_repositories.json").write_text(
            json.dumps({"schemaVersion":1,"repositories":cloud}), encoding="utf-8")
        (root / "telegram_channels.json").write_text(
            json.dumps({"schemaVersion":1,"channels":telegram}), encoding="utf-8")
        return root

    def test_empty_config_publishes_valid_empty_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._config(root / "config", [], [])
            output = root / "output"
            counts = asyncio.run(build_release(config, output, "empty-test"))
            self.assertEqual(counts["cloudstreamPlugins"], 0)
            self.assertEqual(counts["telegramPosts"], 0)
            self.assertEqual(validate_current(output)["telegramChannels"], 0)

    def test_fixture_sources_build_populated_release(self):
        fixture_root = Path(__file__).parent / "fixtures"
        repo_doc = json.loads((fixture_root / "cloudstream" / "repository.json").read_text())
        plugin_doc = json.loads((fixture_root / "cloudstream" / "plugins.json").read_text())
        html = (fixture_root / "telegram" / "channel.html").read_text()
        async def fake_json(_session, url):
            return repo_doc if url.endswith("repository.json") else plugin_doc
        async def fake_page(_session, _url, *, more=False):
            return html
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._config(root / "config", [
                {"key":"fixture-repo","url":"https://fixture.test/repository.json"}], [
                {"username":"example_channel","maxPages":1}])
            output = root / "output"
            counts = asyncio.run(build_release(
                config, output, "fixture-test",
                cloudstream_fetch_json=fake_json,
                telegram_fetch_page=fake_page))
            self.assertEqual(counts["cloudstreamRepositories"], 1)
            self.assertEqual(counts["cloudstreamPlugins"], 1)
            self.assertEqual(counts["telegramChannels"], 1)
            self.assertEqual(counts["telegramPosts"], 2)
            self.assertEqual(validate_version(output, "fixture-test")["telegramPosts"], 2)


if __name__ == "__main__":
    unittest.main()
