"""The published test suite must not bootstrap an installed Hermes runtime."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PortableTests(unittest.TestCase):
    def run_with_poison_bootstrap(self, script):
        with tempfile.TemporaryDirectory(prefix='qr-portability-') as directory:
            path = Path(directory)
            (path / 'hermes_bootstrap.py').write_text('raise RuntimeError("live bootstrap imported by tests")\n')
            env = {**os.environ, 'HOME': str(path), 'HERMES_HOME': str(path),
                   'QR_HERMES_ROOT': str(path), 'PYTHONPATH': str(path),
                   'HERMES_DISABLE_LAZY_INSTALLS': '1'}
            result = subprocess.run([sys.executable, '-c', script], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_router_harness_never_imports_runtime_bootstrap(self):
        self.run_with_poison_bootstrap(
            f'import sys; sys.path.insert(0, {str(ROOT / "tests")!r}); '
            'from _harness import load_engine, cleanup; m, p = load_engine(); '
            'm.load_policy(); cleanup(p)')

    def test_spend_suite_never_imports_runtime_bootstrap(self):
        self.run_with_poison_bootstrap(
            f'import runpy; runpy.run_path({str(ROOT / "tests/test_spend.py")!r}, run_name="__main__")')


if __name__ == '__main__':
    unittest.main()
