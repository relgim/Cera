from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts/run_cera_stage_with_terminal_capture.ps1"


class SequenceFirstTerminalCaptureTests(unittest.TestCase):
    def test_error_action_stop_cannot_preempt_local_http_terminal_result(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        self.assertIsNotNone(powershell)
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            child = root / "local_http_failure.py"
            stdout = root / "runner.stdout.log"
            stderr = root / "runner.stderr.log"
            result = root / "RESULT.json"
            child.write_text(
                textwrap.dedent(
                    f"""
                    import json
                    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
                    from pathlib import Path
                    import sys
                    from threading import Thread
                    from urllib.error import HTTPError
                    from urllib.request import urlopen

                    result = Path({str(result)!r})

                    class Handler(BaseHTTPRequestHandler):
                        def log_message(self, format, *args):
                            return

                        def do_GET(self):
                            body = b'{{"code":"fixture_failure"}}'
                            self.send_response(400)
                            self.send_header("Content-Type", "application/json")
                            self.send_header("Content-Length", str(len(body)))
                            self.end_headers()
                            self.wfile.write(body)

                    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                    worker = Thread(target=server.serve_forever, daemon=True)
                    worker.start()
                    payload = {{
                        "status": "running",
                        "provider_calls": 0,
                        "story_writes": 0,
                    }}
                    try:
                        try:
                            urlopen(
                                f"http://127.0.0.1:{{server.server_address[1]}}/fail"
                            )
                        except HTTPError as exc:
                            diagnostic = exc.read().decode("utf-8")
                            print(diagnostic, file=sys.stderr, flush=True)
                            payload.update(
                                status="failed",
                                error_type="HTTPError",
                                http_status=exc.code,
                                exact_error=diagnostic,
                            )
                    finally:
                        server.shutdown()
                        server.server_close()
                        worker.join(timeout=5)
                        temporary_result = result.with_suffix(".tmp")
                        temporary_result.write_text(
                            json.dumps(payload, sort_keys=True), encoding="utf-8"
                        )
                        temporary_result.replace(result)
                    raise SystemExit(1)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            command = (
                "$ErrorActionPreference='Stop'; "
                f"& '{LAUNCHER}' "
                f"-PythonExe '{Path(sys.executable).resolve()}' "
                f"-RunnerPath '{child}' "
                f"-WorkingDirectory '{root}' "
                f"-StdoutPath '{stdout}' "
                f"-StderrPath '{stderr}' "
                f"-ResultPath '{result}'; "
                "exit $LASTEXITCODE"
            )
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(completed.stderr, "")
            self.assertTrue(result.is_file())
            terminal = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(terminal["status"], "failed")
            self.assertEqual(terminal["http_status"], 400)
            self.assertEqual(terminal["provider_calls"], 0)
            self.assertEqual(terminal["story_writes"], 0)
            self.assertEqual(
                terminal["exact_error"],
                '{"code":"fixture_failure"}',
            )
            self.assertIn('{"code":"fixture_failure"}', stderr.read_text())


if __name__ == "__main__":
    unittest.main()
