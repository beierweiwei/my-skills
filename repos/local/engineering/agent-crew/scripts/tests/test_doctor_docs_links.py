#!/usr/bin/env python3
"""doctor docs.links 范围收窄：第三方目录与 docs/历史 不参与断链检查。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from doctor_fixtures import ProjectFixture


class DoctorDocsLinksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))

    def write_doc(self, rel: str, text: str) -> Path:
        path = self.fx.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_docs_links_ignores_venv_broken_link(self):
        self.write_doc("docs/业务/原型/.venv/lib/site-packages/pkg/README.md",
                       "[缺失](missing.png)\n")
        rc, report = self.fx.doctor_json("--only", "docs.links")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "docs.links")["status"])

    def test_docs_links_ignores_history_broken_link(self):
        self.write_doc("docs/历史/进度追踪-截至20250101.md", "[缺失](missing.md)\n")
        rc, report = self.fx.doctor_json("--only", "docs.links")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "docs.links")["status"])

    def test_docs_links_reports_real_broken_link(self):
        self.write_doc("docs/guide.md", "[缺失](missing.md)\n")
        rc, report = self.fx.doctor_json("--only", "docs.links")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "docs.links")
        self.assertEqual("warn", item["status"], item)
        self.assertIn("docs/guide.md", item["detail"])
        self.assertIn("missing.md", item["detail"])

    def test_docs_links_passes_when_broken_links_are_all_excluded(self):
        self.write_doc("docs/业务/原型/.venv/lib/site-packages/pkg/README.md",
                       "[缺失](missing.png)\n")
        self.write_doc("docs/业务/原型/node_modules/pkg/README.md", "[缺失](missing.js)\n")
        self.write_doc("docs/历史/进度追踪-截至20250101.md", "[缺失](missing.md)\n")
        self.write_doc("docs/guide.md", "[AGENTS](../AGENTS.md)\n")
        rc, report = self.fx.doctor_json("--only", "docs.links")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "docs.links")
        self.assertEqual("pass", item["status"], item)
        self.assertIn("docs.links", item["id"])


if __name__ == "__main__":
    unittest.main()
