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
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        schema = payload["schema"]
        self.assertEqual(schema["properties"]["qq"]["type"], "string")
        self.assertTrue(payload["signature_required"])
        self.assertNotIn("required", schema)


if __name__ == "__main__":
    unittest.main()
