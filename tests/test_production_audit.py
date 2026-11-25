import io
import unittest
from urllib.error import HTTPError

from unittest.mock import patch

from scripts.inspect_production_r2 import read_cloudflare_json


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.payload


class ProductionAuditRetryTests(unittest.TestCase):
    def test_cloudflare_transient_500_is_retried(self):
        transient = HTTPError(
            "https://api.cloudflare.com/test",
            500,
            "Internal Server Error",
            {},
            io.BytesIO(b"temporary"),
        )
        response = Response(b'{"success":true,"result":{"rules":[]}}')

        with (
            patch("scripts.inspect_production_r2.urlopen", side_effect=[transient, response]) as urlopen,
            patch("scripts.inspect_production_r2.time.sleep") as sleep,
        ):
            result = read_cloudflare_json(
                "https://api.cloudflare.com/test",
                {"Authorization": "Bearer test"},
            )

        self.assertTrue(result["success"])
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()

    def test_non_retryable_cloudflare_error_is_not_retried(self):
        error = HTTPError(
            "https://api.cloudflare.com/test",
            403,
            "Forbidden",
            {},
            io.BytesIO(b"forbidden"),
        )

        with (
            patch("scripts.inspect_production_r2.urlopen", side_effect=error) as urlopen,
            patch("scripts.inspect_production_r2.time.sleep") as sleep,
        ):
            with self.assertRaises(HTTPError):
                read_cloudflare_json(
                    "https://api.cloudflare.com/test",
                    {"Authorization": "Bearer test"},
                )

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
