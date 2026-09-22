from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

try:
    from models import NewTrack, VideoMetadata
    from plugin import create_plugin

    SDK_AVAILABLE = True
except ModuleNotFoundError as exc:
    if exc.name != "maibot_sdk":
        raise
    SDK_AVAILABLE = False


class _SendRecorder:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def text(self, text: str, stream_id: str) -> bool:
        self.messages.append((text, stream_id))
        return True


@unittest.skipUnless(SDK_AVAILABLE, "MaiBot SDK is only available in the MaiBot virtual environment")
class PluginLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.send = _SendRecorder()
        self.plugin = create_plugin()
        self.plugin.set_plugin_config(self.plugin.build_default_config())
        self.plugin._set_context(
            SimpleNamespace(
                paths=SimpleNamespace(data_dir=root / "data", runtime_dir=root / "runtime"),
                send=self.send,
            )
        )
        await self.plugin.on_load()

    async def asyncTearDown(self) -> None:
        await self.plugin.on_unload()
        self.temp_dir.cleanup()

    async def test_loads_recommend_service_and_sends_command_reply(self) -> None:
        self.assertIsNotNone(self.plugin._repository)
        self.assertIsNotNone(self.plugin._recommend_service)
        assert self.plugin._repository is not None
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
            )
        )

        handled, summary, stop = await self.plugin.recommend_chinese_vocaloid(
            stream_id="stream-a",
            user_id="user-1",
            group_id="group-1",
        )

        expected = "推荐\n《Lifecycle Track》\nhttps://www.bilibili.com/video/BV0000000001"
        self.assertEqual((handled, summary, stop), (True, expected, True))
        self.assertEqual(self.send.messages, [(expected, "stream-a")])


if __name__ == "__main__":
    unittest.main()
