import json
import tempfile
import unittest
from pathlib import Path

from rtl_agent.design_config import DesignConfigError, load_design_config


class DesignLoaderTests(unittest.TestCase):
    def _design(self, root: Path, **updates: object) -> Path:
        data = {
            "design_name": "demo", "top_module": "demo",
            "rtl_filename": "demo.sv", "tb_filename": "demo_tb.sv",
            "spec_file": "spec.md", "testplan_file": "testplan.md",
            "max_repair_attempts": 3,
        }
        data.update(updates)
        (root / "design.json").write_text(json.dumps(data), encoding="utf-8")
        (root / "spec.md").write_text("A useful specification.\n", encoding="utf-8")
        return root

    def test_loads_valid_design(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            config = load_design_config(self._design(Path(name)))
            self.assertEqual(config.top_module, "demo")

    def test_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = self._design(Path(name), rtl_filename="../demo.sv")
            with self.assertRaises(DesignConfigError):
                load_design_config(directory)

    def test_rejects_empty_spec(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = self._design(Path(name))
            (directory / "spec.md").write_text("  ", encoding="utf-8")
            with self.assertRaises(DesignConfigError):
                load_design_config(directory)


if __name__ == "__main__":
    unittest.main()
