import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gpu  # noqa: E402
import settings_model  # noqa: E402
import widgets  # noqa: E402


class GpuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"IMACTV_DATA_DIR": self.tmp.name})
        self.env.start()
        os.environ.pop("IMACTV_GPU_VIZ", None)
        gpu._result = None
        gpu._thread = None

    def tearDown(self):
        self.env.stop()
        gpu._result = None
        gpu._thread = None
        self.tmp.cleanup()

    def test_classify(self):
        self.assertTrue(gpu.classify("Mesa Intel(R) HD Graphics 520 (SKL GT2)"))
        self.assertTrue(gpu.classify("AMD Radeon RX 580 (radeonsi, polaris10)"))
        for sw in ("llvmpipe (LLVM 20.1.2, 256 bits)", "softpipe", "Software Rasterizer", "swrast"):
            self.assertFalse(gpu.classify(sw), sw)
        self.assertFalse(gpu.classify(""))
        self.assertFalse(gpu.classify("Mesa Intel(R) HD Graphics 520", direct=False))

    def run_result(self, stdout="", stderr="", code=0):
        return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)

    def test_probe_parses_child(self):
        out = json.dumps({"renderer": "Mesa Intel(R) HD Graphics 520", "vendor": "Intel", "direct": True,
                          "error": None})
        with mock.patch.object(subprocess, "run", return_value=self.run_result(out)):
            res = gpu.probe()
        self.assertTrue(res["hw"])
        self.assertEqual(res["vendor"], "Intel")

    def test_probe_failures_mean_no_gpu(self):
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 5)):
            self.assertEqual(gpu.probe()["error"], "timeout")
        with mock.patch.object(subprocess, "run", return_value=self.run_result("", "Segmentation fault", -11)):
            res = gpu.probe()
            self.assertFalse(res["hw"])
            self.assertIn("-11", res["error"])
        out = json.dumps({"renderer": "Mesa Intel", "vendor": "Intel", "direct": True, "error": "boom"})
        with mock.patch.object(subprocess, "run", return_value=self.run_result(out)):
            self.assertFalse(gpu.probe()["hw"])

    def test_detected_fallbacks(self):
        self.assertTrue(gpu.detected()["pending"])
        (Path(self.tmp.name) / "gpu.json").write_text(json.dumps({"hw": True, "renderer": "X"}))
        self.assertTrue(gpu.detected()["hw"])
        gpu._store({"hw": False, "renderer": "llvmpipe", "vendor": "Mesa", "error": None})
        self.assertFalse(gpu.detected()["hw"])
        self.assertFalse(json.loads((Path(self.tmp.name) / "gpu.json").read_text())["hw"])

    def test_start_detection_runs_once(self):
        calls = []
        with mock.patch.object(gpu, "probe", side_effect=lambda d=None: calls.append(d) or
                               {"hw": True, "renderer": "R", "vendor": "V", "error": None}):
            t = gpu.start_detection()
            self.assertIs(gpu.start_detection(), t)
            t.join(5)
        self.assertEqual(len(calls), 1)
        self.assertTrue(gpu.detected()["hw"])

    def test_use_gpu_viz_precedence(self):
        gpu._result = {"hw": True}
        self.assertTrue(gpu.use_gpu_viz({"gpu_viz": "auto"}))
        self.assertTrue(gpu.use_gpu_viz({}))
        self.assertFalse(gpu.use_gpu_viz({"gpu_viz": "off"}))
        gpu._result = {"hw": False}
        self.assertFalse(gpu.use_gpu_viz({"gpu_viz": "auto"}))
        self.assertTrue(gpu.use_gpu_viz({"gpu_viz": "on"}))
        with mock.patch.dict(os.environ, {"IMACTV_GPU_VIZ": "0"}):
            self.assertFalse(gpu.use_gpu_viz({"gpu_viz": "on"}))
        with mock.patch.dict(os.environ, {"IMACTV_GPU_VIZ": "1"}):
            self.assertTrue(gpu.use_gpu_viz({"gpu_viz": "off"}))

    def test_describe(self):
        self.assertIn("checking", gpu.describe({"gpu_viz": "auto"}))
        gpu._result = {"hw": True, "renderer": "Mesa Intel(R) HD Graphics 520"}
        self.assertIn("HD Graphics 520", gpu.describe({"gpu_viz": "auto"}))
        gpu._result = {"hw": False}
        self.assertIn("no graphics chip", gpu.describe({}))
        self.assertIn("processor", gpu.describe({"gpu_viz": "off"}))

    def test_config(self):
        self.assertEqual(widgets.parse_config({})["gpu_viz"], "auto")
        self.assertEqual(widgets.parse_config({"gpu_viz": "off"})["gpu_viz"], "off")
        self.assertEqual(widgets.parse_config({"gpu_viz": "maybe"})["gpu_viz"], "auto")
        self.assertEqual(settings_model.validate("gpu_viz", "on"), "on")
        with self.assertRaises(ValueError):
            settings_model.validate("gpu_viz", True)
        self.assertEqual(settings_model.label("auto"), "Auto")


if __name__ == "__main__":
    unittest.main()
