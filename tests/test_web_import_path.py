from pathlib import Path
import unittest


class WebImportPathTests(unittest.TestCase):
    def test_app_adds_project_root_before_loading_engine(self):
        app = Path(__file__).resolve().parents[1] / "app.py"
        source = app.read_text(encoding="utf-8")
        root_setup = 'if _APP_DIR not in sys.path:\n    sys.path.insert(0, _APP_DIR)'
        loader = '_ENGINE_SPEC.loader.exec_module(engine)'
        self.assertIn(root_setup, source)
        self.assertLess(source.index(root_setup), source.index(loader))


if __name__ == "__main__":
    unittest.main()
