"""Regression tests for detector registry/dependency functions exposed by Web HTTP."""
from __future__ import annotations

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from tests.http_test_handler import AuthorizedTestHandler
from scripts import visualization_server


class DashboardCatalogApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), AuthorizedTestHandler
        )
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.worker.join(timeout=5)
        cls.server.server_close()

    def get_payload(self, path: str) -> dict:
        with urlopen(self.url + path, timeout=8) as response:
            self.assertEqual(response.status, 200)
            return json.load(response)

    def test_detector_registry_functions_exposed(self):
        for name in (
            "get_available_algorithms",
            "get_detector_config_schema",
            "get_all_detector_schemas",
            "check_all_detectors",
            "install_detector_dependencies",
            "install_all_missing_dependencies",
        ):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(visualization_server, name, None)))

    def test_available_algorithms(self):
        payload = self.get_payload("/api/available-algorithms")
        self.assertIn("zscore", {item["name"] for item in payload["algorithms"]})

    def test_detector_schema_single_and_all(self):
        payload = self.get_payload("/api/algorithm-config-schema?algorithm=zscore")
        self.assertEqual(payload["schema"]["name"], "zscore")
        payload = self.get_payload("/api/algorithm-config-schema")
        self.assertIn("zscore", payload["schemas"])

    def test_dependency_status_endpoints(self):
        for path in ("/api/dependencies", "/api/dependencies/status"):
            with self.subTest(path=path):
                payload = self.get_payload(path)
                self.assertIn("zscore", payload["detectors"])
                self.assertIn("installed", payload["detectors"]["zscore"])


if __name__ == "__main__":
    unittest.main()
