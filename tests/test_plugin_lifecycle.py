from __future__ import annotations

import base64
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

try:
    from models import CoverStatus, NewTrack, VideoMetadata
    from plugin import create_plugin

    SDK_AVAILABLE = True
except ModuleNotFoundError as exc:
    if exc.name != "maibot_sdk":
        raise
    SDK_AVAILABLE = False


class _SendRecorder:
    def __init__(self) -> None:
        self.text_messages: list[tuple[str, str]] = []
        self.hybrid_messages: list[tuple[list[dict[str, str]], str]] = []
        self.hybrid_result = True

    async def text(self, text: str, stream_id: str) -> bool:
        self.text_messages.append((text, stream_id))
        return True

    async def hybrid(self, segments: list[dict[str, str]], stream_id: str) -> bool:
        self.hybrid_messages.append((segments, stream_id))
        return self.hybrid_result


@unittest.skipUnless(SDK_AVAILABLE, "MaiBot SDK is only available in the MaiBot virtual environment")
class PluginLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.data_dir = root / "data"
        self.send = _SendRecorder()
        self.plugin = create_plugin()
        self.plugin.set_plugin_config(self.plugin.build_default_config())
        self.plugin._set_context(
            SimpleNamespace(
                paths=SimpleNamespace(data_dir=self.data_dir, runtime_dir=root / "runtime"),
                send=self.send,
                logger=logging.getLogger("test.plugin"),
            )
        )
        await self.plugin.on_load()

    async def asyncTearDown(self) -> None:
        await self.plugin.on_unload()
        self.temp_dir.cleanup()

    async def _add_cached_track(self) -> bytes:
        assert self.plugin._repository is not None
        cover_dir = self.data_dir / "covers"
        cover_dir.mkdir(parents=True, exist_ok=True)
        cover_body = b"\xff\xd8\xfflifecycle-cover"
        (cover_dir / "BV0000000001.jpg").write_bytes(cover_body)
        await self.plugin._repository.add_track(
            NewTrack(
                metadata=VideoMetadata(
                    bvid="BV0000000001",
                    aid=10001,
                    title="Lifecycle Track",
                    cover_url="https://i0.hdslb.com/lifecycle.jpg",
                ),
                canonical_url="https://www.bilibili.com/video/BV0000000001",
                uploader_id="user-1",
                origin_group_id="group-1",
                origin_stream_id="group-1",
                cover_path="covers/BV0000000001.jpg",
                cover_status=CoverStatus.CACHED,
            )
        )
        return cover_body

    async def test_loads_recommend_service_and_sends_command_reply(self) -> None:
        self.assertIsNotNone(self.plugin._repository)
        self.assertIsNotNone(self.plugin._recommend_service)
        cover_body = await self._add_cached_track()

        handled, summary, stop = await self.plugin.recommend_chinese_vocaloid(
            stream_id="stream-a",
            user_id="user-1",
            group_id="group-1",
        )

        expected = "推荐\n《Lifecycle Track》\nhttps://www.bilibili.com/video/BV0000000001"
        self.assertEqual((handled, summary, stop), (True, expected, True))
        self.assertEqual(self.send.text_messages, [])
        self.assertEqual(
            self.send.hybrid_messages,
            [
                (
                    [
                        {"type": "text", "content": "推荐\n《Lifecycle Track》"},
                        {"type": "image", "content": base64.b64encode(cover_body).decode("ascii")},
                        {"type": "text", "content": "https://www.bilibili.com/video/BV0000000001"},
                    ],
                    "stream-a",
                )
            ],
        )

    async def test_hybrid_send_failure_falls_back_to_text(self) -> None:
        await self._add_cached_track()
        self.send.hybrid_result = False

        handled, summary, stop = await self.plugin.recommend_chinese_vocaloid(
            stream_id="stream-a",
            user_id="user-1",
            group_id="group-1",
        )

        expected = "推荐\n《Lifecycle Track》\nhttps://www.bilibili.com/video/BV0000000001"
        self.assertEqual((handled, summary, stop), (True, expected, True))
        self.assertEqual(self.send.text_messages, [(expected, "stream-a")])
        self.assertEqual(len(self.send.hybrid_messages), 1)


if __name__ == "__main__":
    unittest.main()
