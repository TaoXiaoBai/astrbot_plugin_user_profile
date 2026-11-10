import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


class RealAstrBotSchemaTests(unittest.TestCase):
    def _astrbot_app_path(self, workdir, env):
        probe = "from astrbot.api.event import filter; from astrbot.core.provider.register import llm_tools"
        direct = subprocess.run(
            [sys.executable, "-c", probe], cwd=workdir, env=env,
            capture_output=True, text=True,
        )
        if direct.returncode == 0:
            return None

        candidates = []
        configured = os.environ.get("ASTRBOT_APP_PATH")
        if configured:
            candidates.append(pathlib.Path(configured))
        executable = pathlib.Path(sys.executable).resolve()
        candidates.append(executable.parent.parent / "app")
        for candidate in candidates:
            if not (candidate / "astrbot").is_dir():
                continue
            script = "import sys; sys.path.insert(0, %r); %s" % (str(candidate), probe)
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=workdir, env=env,
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return candidate
        self.skipTest(
            "AstrBot 不在当前解释器或可推导布局中；可设置 ASTRBOT_APP_PATH"
        )

    def test_user_profile_tool_exposes_required_qq_string(self):
        plugin_parent = pathlib.Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        with tempfile.TemporaryDirectory() as workdir:
            app_path = self._astrbot_app_path(workdir, env)
            paths = [str(plugin_parent)]
            if app_path is not None:
                paths.insert(0, str(app_path))
            script = (
                "import inspect,json,sys; sys.path[:0]=%r; "
                "import astrbot_plugin_user_profile.main as module; "
                "from astrbot.core.provider.register import llm_tools; "
                "tool=llm_tools.get_func('user_profile_query'); "
                "parameter=inspect.signature(module.UserProfilePlugin.user_profile_query).parameters['qq']; "
                "print(json.dumps({'schema':tool.parameters,'signature_required':parameter.default is inspect.Parameter.empty},ensure_ascii=False))"
            ) % paths
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=workdir, env=env,
                capture_output=True, text=True, check=True,
            )
        # AstrBot 的日志与 print 共用 stdout 时顺序不保证，直接定位 JSON 负载行。
        payload_line = next(
            line for line in result.stdout.splitlines()
            if line.strip().startswith('{"schema"')
        )
        payload = json.loads(payload_line)
        schema = payload["schema"]
        self.assertEqual(schema["properties"]["qq"]["type"], "string")
        self.assertTrue(payload["signature_required"])
        self.assertNotIn("required", schema)

    def test_profile_context_is_temporary_and_excluded_from_saved_history(self):
        plugin_parent = pathlib.Path(__file__).resolve().parents[2]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        with tempfile.TemporaryDirectory() as workdir:
            app_path = self._astrbot_app_path(workdir, env)
            paths = [str(plugin_parent)]
            if app_path is not None:
                paths.insert(0, str(app_path))
            script = "import sys; sys.path[:0]=%r\n" % paths + '''
import asyncio, json, types
from unittest.mock import AsyncMock
from astrbot.core.agent.message import TextPart, Message, dump_messages_with_checkpoints
from astrbot.core.provider.entities import ProviderRequest
from astrbot_plugin_user_profile.main import UserProfilePlugin

async def run():
    plugin = UserProfilePlugin.__new__(UserProfilePlugin)
    plugin.config = {"llm_context_inject": True}
    plugin._enabled = lambda: True
    plugin._schedule_friend_reconcile = lambda event: None
    plugin._raw_event = lambda event: {"self_id": "99999"}
    plugin._build_conversation_profile_context = AsyncMock(return_value="<!-- astrbot-user-profile-context -->profile")
    event = types.SimpleNamespace(get_sender_id=lambda: "11111")
    req = ProviderRequest(prompt="hello", system_prompt="stable", contexts=[{"role": "user", "content": "previous"}])
    other = TextPart(text="other context")
    req.extra_user_content_parts.append(other)
    await plugin.on_llm_request(event, req)
    await plugin.on_llm_request(event, req)
    assert req.system_prompt == "stable"
    assert req.prompt == "hello"
    assert req.contexts == [{"role": "user", "content": "previous"}]
    assert len(req.extra_user_content_parts) == 2
    assert req.extra_user_content_parts[0] is other
    part = req.extra_user_content_parts[1]
    assert part._no_save is True
    assembled = await req.assemble_context()
    assert any(item.get("_no_save") for item in assembled["content"])
    runtime = Message(role="user", content=[TextPart(text="hello"), other, part])
    saved = dump_messages_with_checkpoints([runtime])
    assert [item["text"] for item in saved[0]["content"]] == ["hello", "other context"]
    print(json.dumps({"temporary_profile_ok": True}))

asyncio.run(run())
'''
            result = subprocess.run([sys.executable, "-c", script], cwd=workdir, env=env,
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"temporary_profile_ok": true', result.stdout)


if __name__ == "__main__":
    unittest.main()
