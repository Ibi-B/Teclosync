import asyncio
import unittest
from pathlib import Path

from scripts.telegram import collect_telegram, normalize_channel_username, parse_channel_page

FIXTURE = Path(__file__).parent / "fixtures" / "telegram" / "channel.html"
HTML = FIXTURE.read_text(encoding="utf-8")


class TelegramTests(unittest.TestCase):
    def test_channel_reference_normalization(self):
        self.assertEqual(normalize_channel_username("@example_channel"), "example_channel")
        self.assertEqual(normalize_channel_username("https://t.me/s/example_channel"), "example_channel")
        self.assertEqual(normalize_channel_username("t.me/example_channel"), "example_channel")

    def test_channel_header_posts_and_media(self):
        parsed = parse_channel_page(HTML, "example_channel")
        self.assertEqual(parsed["title"], "Example Channel")
        self.assertEqual(len(parsed["posts"]), 2)
        self.assertEqual(parsed["posts"][0]["media"][0]["type"], "photo")
        self.assertEqual(parsed["posts"][1]["media"][0]["type"], "video")
        self.assertEqual(parsed["posts"][1]["media"][0]["url"],
                         "https://cdn.example.test/video.mp4")

    def test_collection_and_older_page_pagination(self):
        older = HTML.replace("example_channel/102", "example_channel/100").replace(
            "example_channel/101", "example_channel/99")
        calls = []
        async def fake_fetch(_session, url, *, more=False):
            calls.append((url, more))
            return HTML if not more else older
        async def run():
            result = await collect_telegram(
                [{"username":"@example_channel", "maxPages":2}],
                object(), fetch_page=fake_fetch)
            self.assertEqual(len(result["channels"]), 1)
            self.assertEqual(result["channels"][0]["postCount"], 4)
            self.assertEqual(calls[0][1], False)
            self.assertEqual(calls[1][1], True)
            self.assertIn("before=101", calls[1][0])
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
