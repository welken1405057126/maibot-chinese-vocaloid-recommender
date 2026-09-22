from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from bilibili_client import BilibiliResponseError, parse_video_metadata  # noqa: E402


def make_payload() -> dict[str, object]:
    return {
        "code": 0,
        "message": "0",
        "data": {
            "bvid": "BV1vFxXzWELU",
            "aid": 123456,
            "title": "Test title",
            "pic": "http://i0.hdslb.com/test.jpg",
            "state": 0,
            "stat": {"view": 9876},
        },
    }


class ParseVideoMetadataTests(unittest.TestCase):
    def test_maps_required_fields_and_normalizes_cover_scheme(self) -> None:
        metadata = parse_video_metadata(make_payload())

        self.assertEqual(metadata.bvid, "BV1vFxXzWELU")
        self.assertEqual(metadata.aid, 123456)
        self.assertEqual(metadata.title, "Test title")
        self.assertEqual(metadata.cover_url, "https://i0.hdslb.com/test.jpg")
        self.assertEqual(metadata.state, 0)
        self.assertEqual(metadata.view_count, 9876)

    def test_rejects_api_business_error(self) -> None:
        with self.assertRaisesRegex(BilibiliResponseError, "code=-400"):
            parse_video_metadata({"code": -400, "message": "请求错误", "data": None})

    def test_rejects_missing_nested_view_count(self) -> None:
        payload = make_payload()
        data = payload["data"]
        assert isinstance(data, dict)
        data["stat"] = {}

        with self.assertRaisesRegex(BilibiliResponseError, "data.stat.view"):
            parse_video_metadata(payload)


if __name__ == "__main__":
    unittest.main()
