import asyncio
import unittest

from scripts.cloudstream import collect_cloudstream, normalize_plugin, parse_repository_manifest
from scripts.common import http_url


class CloudStreamTests(unittest.TestCase):
    def test_plugin_list_manifest_and_relative_urls(self):
        metadata, urls, rows = parse_repository_manifest(
            {"name":"Example","pluginLists":["lists/plugins.json"]},
            "https://example.test/repo.json")
        self.assertEqual(metadata["name"], "Example")
        self.assertEqual(urls, ["https://example.test/lists/plugins.json"])
        self.assertIsNone(rows)

    def test_github_blob_urls_are_raw(self):
        self.assertEqual(http_url("https://github.com/org/repo/blob/main/a.json"),
                         "https://raw.githubusercontent.com/org/repo/main/a.json")

    def test_normalizes_plugin_metadata(self):
        plugin = normalize_plugin(
            {"name":"Demo","url":"extensions/demo.cs3","version":"2","apiVersion":1},
            "https://example.test/plugins.json",
            {"key":"repo","name":"Repository"})
        self.assertEqual(plugin["url"], "https://example.test/extensions/demo.cs3")
        self.assertEqual(plugin["internalName"], "Demo")
        self.assertEqual(plugin["version"], 2)

    def test_collects_configured_repository_and_plugin(self):
        async def fake_fetch(_session, url):
            if url.endswith("repo.json"):
                return {"name":"Example","pluginLists":["plugins.json"]}
            return [{"name":"Demo","internalName":"Demo","url":"demo.cs3"}]
        async def run():
            result = await collect_cloudstream(
                [{"key":"example","url":"https://example.test/repo.json"}],
                object(), fetch_json=fake_fetch)
            self.assertEqual(len(result["repositories"]), 1)
            self.assertEqual(len(result["plugins"]), 1)
            self.assertEqual(result["plugins"][0]["url"], "https://example.test/demo.cs3")
            self.assertEqual(result["warnings"], [])
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
