from __future__ import annotations

import json
import unittest
from threading import Thread
from urllib.request import Request, urlopen

from cera.pi_scene.http import (
    PiSceneHttpAdapter,
    PiSceneServerConfigV1,
    build_pi_scene_server,
)
from cera.pi_scene.http_contracts import (
    PI_SCENE_ADULT_MODEL,
    PI_SCENE_AUTO_MODEL,
    PI_SCENE_ORDINARY_MODEL,
)


class PiSceneModelCatalogTests(unittest.TestCase):
    def test_models_endpoint_advertises_the_automatic_production_model(self) -> None:
        token = "local-test-token-that-is-long-enough"
        adapter = PiSceneHttpAdapter(
            coordinator=object(),  # type: ignore[arg-type]
            session_id="model-catalog-test",
            context_provider=lambda *_args: None,  # type: ignore[arg-type]
        )
        server = build_pi_scene_server(
            adapter,
            PiSceneServerConfigV1(
                host="127.0.0.1",
                port=0,
                authorization_token=token,
                approved_origins=("http://127.0.0.1:8000",),
            ),
        )
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/v1/models",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(
                [row["id"] for row in payload["data"]],
                [
                    PI_SCENE_AUTO_MODEL,
                    PI_SCENE_ORDINARY_MODEL,
                    PI_SCENE_ADULT_MODEL,
                ],
            )
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
