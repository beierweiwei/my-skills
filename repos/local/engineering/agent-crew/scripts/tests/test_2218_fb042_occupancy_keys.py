#!/usr/bin/env python3
"""2.2.18 / FB-042：crew-ticket 占用键统一为仓库根相对路径。

覆盖：
① 子目录 cwd / `./` 前缀 / `../` 回退均解析为同一 canonical 键；
② 子目录内同名文件按 cwd 解析为各自物理路径的键；
③ 旧键兼容读取：后缀匹配同 owner 幂等、异 owner 拒绝并列出持有者；
④ release：冲突仅按精确键判定（精确异 owner 拒绝；仅后缀命中他人 → no-op rc=0），
   同 owner 清理 canonical 与命中的旧键，互为后缀的两 owner 键可各自释放；
⑤ 新 claim 只写 canonical 键，不误伤不命中的旧键；
⑥ 拒绝语义不变：绝对路径、`~`、空路径、逃逸仍 rc=2；
⑦ 不同路径写法并发 claim 同一文件只有一个赢家。

运行：python3 scripts/tests/test_2218_fb042_occupancy_keys.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "crew-ticket.py"


class OccupancyKeysTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "tester@example.com")
        self.git("config", "user.name", "Tester")

    def tearDown(self):
        self._tmp.cleanup()

    # ---- helpers ---------------------------------------------------------

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            self.fail(f"git {' '.join(args)}: {proc.stderr or proc.stdout}")
        return proc.stdout

    def cli(self, *args: str, cwd: Path | None = None, check: bool = True):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args],
            cwd=str(cwd or self.root),
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            self.fail(f"exit {proc.returncode}: {proc.stderr or proc.stdout}")
        return proc

    def write(self, name: str, text: str = "content\n") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def occupancy(self) -> dict:
        path = self.root / ".git" / "crew" / "occupancy.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def seed_occupancy(self, data: dict) -> None:
        path = self.root / ".git" / "crew" / "occupancy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    # ---- ①② canonical 键 ------------------------------------------------

    def test_subdir_dot_and_parent_spellings_resolve_to_same_key(self):
        self.write("scripts/x.py")
        sub = self.root / "pkg"
        sub.mkdir()

        self.cli("claim", "--owner", "owner-a", "scripts/x.py")
        dot = self.cli("claim", "--owner", "owner-a", "./scripts/x.py")
        self.assertIn("no-op", dot.stdout)
        parent = self.cli("claim", "--owner", "owner-a", "../scripts/x.py", cwd=sub)
        self.assertIn("no-op", parent.stdout)
        self.assertEqual(self.occupancy(), {"scripts/x.py": "owner-a"})

    def test_subdir_relative_input_keys_by_cwd(self):
        self.write("pkg/scripts/y.py")

        self.cli("claim", "--owner", "owner-a", "scripts/y.py", cwd=self.root / "pkg")
        self.assertEqual(self.occupancy(), {"pkg/scripts/y.py": "owner-a"})

    def test_full_prefix_and_subdir_spellings_share_one_key(self):
        self.write("skills/agent-crew/scripts/crew-msg.py")
        sub = self.root / "skills" / "agent-crew"

        self.cli("claim", "--owner", "owner-a", "scripts/crew-msg.py", cwd=sub)
        rejected = self.cli(
            "claim", "--owner", "owner-b",
            "skills/agent-crew/scripts/crew-msg.py",
            check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("held by owner-a", rejected.stderr)
        self.assertEqual(
            self.occupancy(), {"skills/agent-crew/scripts/crew-msg.py": "owner-a"}
        )

    # ---- ③ 旧键兼容 ------------------------------------------------------

    def test_legacy_key_other_owner_conflict(self):
        self.seed_occupancy({"scripts/x.py": "owner-a"})
        rejected = self.cli(
            "claim", "--owner", "owner-b", "deep/scripts/x.py", check=False
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("held by owner-a", rejected.stderr)
        self.assertEqual(self.occupancy(), {"scripts/x.py": "owner-a"})

    def test_legacy_key_same_owner_idempotent(self):
        self.seed_occupancy({"scripts/x.py": "owner-a"})
        again = self.cli("claim", "--owner", "owner-a", "deep/scripts/x.py")
        self.assertIn("no-op", again.stdout)
        self.assertEqual(self.occupancy(), {"scripts/x.py": "owner-a"})

    def test_legacy_key_shorter_than_canonical_conflict(self):
        # 反向兼容：旧键以 `/<canonical>` 结尾
        self.seed_occupancy({"legacy/pkg/x.py": "owner-a"})
        rejected = self.cli("claim", "--owner", "owner-b", "pkg/x.py", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("held by owner-a", rejected.stderr)

    # ---- ④⑤ release / 新键写入 -------------------------------------------

    def test_release_cleans_canonical_and_matched_legacy(self):
        self.seed_occupancy(
            {
                "scripts/x.py": "owner-a",
                "deep/scripts/x.py": "owner-a",
                "other/keep.py": "owner-b",
            }
        )
        self.cli("release", "--owner", "owner-a", "deep/scripts/x.py")
        self.assertEqual(self.occupancy(), {"other/keep.py": "owner-b"})

    def test_release_exact_key_other_owner_rejected(self):
        self.seed_occupancy({"deep/scripts/x.py": "owner-b"})
        rejected = self.cli(
            "release", "--owner", "owner-a", "deep/scripts/x.py", check=False
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("held by owner-b", rejected.stderr)
        self.assertEqual(self.occupancy(), {"deep/scripts/x.py": "owner-b"})

    def test_release_suffix_only_other_owner_is_noop(self):
        self.seed_occupancy({"scripts/x.py": "owner-b"})
        noop = self.cli("release", "--owner", "owner-a", "deep/scripts/x.py")
        self.assertIn("no-op", noop.stdout)
        self.assertEqual(self.occupancy(), {"scripts/x.py": "owner-b"})

    def test_release_own_exact_with_other_owner_suffix(self):
        # S-1：自己精确键存在、后缀命中他人键，不得死锁
        self.seed_occupancy({"scripts/x.py": "owner-b", "deep/scripts/x.py": "owner-a"})
        self.cli("release", "--owner", "owner-a", "deep/scripts/x.py")
        self.assertEqual(self.occupancy(), {"scripts/x.py": "owner-b"})

    def test_release_reciprocal_suffix_keys_independent(self):
        self.seed_occupancy({"scripts/x.py": "owner-a", "deep/scripts/x.py": "owner-b"})
        self.cli("release", "--owner", "owner-a", "scripts/x.py")
        self.assertEqual(self.occupancy(), {"deep/scripts/x.py": "owner-b"})
        self.cli("release", "--owner", "owner-b", "deep/scripts/x.py")
        self.assertEqual(self.occupancy(), {})

    def test_release_missing_exact_cleans_own_legacy(self):
        self.seed_occupancy({"scripts/x.py": "owner-a"})
        self.cli("release", "--owner", "owner-a", "deep/scripts/x.py")
        self.assertEqual(self.occupancy(), {})

    def test_claim_writes_canonical_and_keeps_unmatched_legacy(self):
        self.seed_occupancy({"other/keep.py": "owner-b"})
        self.cli("claim", "--owner", "owner-a", "deep/scripts/x.py")
        self.assertEqual(
            self.occupancy(),
            {"other/keep.py": "owner-b", "deep/scripts/x.py": "owner-a"},
        )

    # ---- ⑥ 拒绝语义不变 ---------------------------------------------------

    def test_path_safety_still_rejects_absolute_tilde_empty_and_escape(self):
        for value in (str(self.root / "a.py"), "~/a.py", "", "../escape.py"):
            rejected = self.cli("claim", "--owner", "owner-a", value, check=False)
            self.assertEqual(rejected.returncode, 2, value)
        self.assertEqual(self.occupancy(), {})

    # ---- ⑦ 并发单赢家 ----------------------------------------------------

    def test_concurrent_claim_different_spellings_single_winner(self):
        self.write("sub/scripts/shared.py")
        sub = self.root / "sub"
        specs = [
            ("owner-a", self.root, "sub/scripts/shared.py"),
            ("owner-b", sub, "scripts/shared.py"),
        ]
        procs = [
            subprocess.Popen(
                [
                    sys.executable, str(SCRIPT), "--root", str(self.root),
                    "claim", "--owner", owner, arg,
                ],
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for owner, cwd, arg in specs
        ]
        results = [(proc,) + proc.communicate(timeout=60) for proc in procs]
        codes = [proc.returncode for proc, _, _ in results]
        self.assertEqual(codes.count(0), 1, [err for _, _, err in results])
        self.assertEqual(codes.count(2), 1, [err for _, _, err in results])

        winner = [owner for (owner, _, _), proc in zip(specs, procs)
                  if proc.returncode == 0][0]
        loser_err = next(err for proc, _, err in results if proc.returncode == 2)
        self.assertIn(f"held by {winner}", loser_err)
        self.assertEqual(self.occupancy(), {"sub/scripts/shared.py": winner})


if __name__ == "__main__":
    unittest.main()
