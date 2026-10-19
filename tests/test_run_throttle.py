"""Run throttle uses an explicit never-run sentinel, not the machine's uptime."""
import importlib.util
import types
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from _harness import HERE, cleanup, load_engine


class RunThrottleClock(unittest.TestCase):
    def test_first_request_near_monotonic_zero_and_exact_retry_boundary(self):
        engine, temp = load_engine()
        self.addCleanup(cleanup, temp)
        engine.collect = lambda policy=None: {}
        spec = importlib.util.spec_from_file_location('qr_api_clock', HERE / 'dashboard/plugin_api.py')
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        api.engine = lambda: engine
        clock = [0.0]
        api.time = types.SimpleNamespace(monotonic=lambda: clock[0])
        app = FastAPI()
        app.include_router(api.router)
        with TestClient(app) as client:
            self.assertEqual(client.post('/run', json={}).status_code, 200)
            self.assertEqual(client.post('/run', json={}).status_code, 429)
            clock[0] = 29.999
            self.assertEqual(client.post('/run', json={}).status_code, 429)
            clock[0] = 30.0
            self.assertEqual(client.post('/run', json={}).status_code, 200)


if __name__ == '__main__':
    unittest.main()
