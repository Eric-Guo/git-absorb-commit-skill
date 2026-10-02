#!/usr/bin/env python3
"""Exercise absorb.py against real, isolated Git repositories."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("absorb.py")


class AbsorbTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="test-absorb-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.planpath = self.root / "plan.json"
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="Test Author", GIT_AUTHOR_EMAIL="author@example.com",
                        GIT_COMMITTER_NAME="Test Committer", GIT_COMMITTER_EMAIL="committer@example.com",
                        GIT_AUTHOR_DATE="1700000000 +0800", GIT_COMMITTER_DATE="1700000060 +0800")
        self.git("init", "--quiet", "--initial-branch=topic", "--template=")
        self.git("config", "core.filemode", "true")
        self.write("base.txt", "base\n")
        self.base = self.commit("base")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], env=self.env)

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)

    def commit(self, message):
        self.git("add", "--all")
        self.git("-c", "commit.gpgSign=false", "commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD").decode().strip()

    def command(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=self.env,
                              capture_output=True, text=True)

    def plan(self):
        result = self.command("plan", "--repo", str(self.repo), "--commit", "HEAD",
                              "--base", self.base, "--out", str(self.planpath))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(self.planpath.read_text())

    def apply(self, plan, *options):
        self.planpath.write_text(json.dumps(plan))
        return self.command("apply", "--repo", str(self.repo), "--plan", str(self.planpath), *options)

    def assert_rewrite(self, head, report):
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), self.git("rev-parse", head + "^{tree}"))
        self.assertEqual(self.git("rev-parse", report["backup"]).decode().strip(), head)
        self.assertEqual(self.git("status", "--porcelain"), b"")
        ancestry = subprocess.run(["git", "-C", str(self.repo), "merge-base", "--is-ancestor", head, "HEAD"],
                                  env=self.env)
        self.assertEqual(ancestry.returncode, 1)
        for old, new in report["mapping"].items():
            self.assertEqual(self.git("show", "-s", "--format=%an%n%ae%n%at%n%ai%n%cn%n%ce%n%ct%n%ci%n%B", old),
                             self.git("show", "-s", "--format=%an%n%ae%n%at%n%ai%n%cn%n%ce%n%ct%n%ci%n%B", new))
        self.assertEqual(json.loads(self.planpath.with_suffix(".result.json").read_text()), report)

    def assert_rejected(self, plan, message, *options):
        head = self.git("rev-parse", "HEAD")
        index = self.git("ls-files", "--stage")
        result = self.apply(plan, *options)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("ls-files", "--stage"), index)
        self.assertEqual(self.git("status", "--porcelain"), b"")
        self.assertFalse(self.planpath.with_suffix(".result.json").exists())

    def test_new_files_join_owner_and_preserve_later_patch(self):
        self.write("watcher.py", "from ignore import patterns\n")
        owner = self.commit("feat: watch directories")
        self.write("notes.txt", "later change\n")
        later = self.commit("docs: add notes")
        additions = {"ignore.py": "patterns = ['vendor']\n", "test_ignore.py": "assert patterns\n",
                     "empty.txt": "", "scripts/检查 script.sh": "#!/bin/sh\nexit 0\n"}
        for path, content in additions.items():
            self.write(path, content)
        (self.repo / "scripts/检查 script.sh").chmod(0o755)
        head = self.commit("fix: restore omitted files")
        plan = self.plan()
        self.assertEqual(len(plan["hunks"]), len(additions))
        for hunk in plan["hunks"]:
            self.assertEqual(hunk["change"], "add")
            self.assertEqual(hunk["candidates"], [])
            self.assertIsNone(hunk["target"])
            hunk["target"] = owner
        result = self.apply(plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assert_rewrite(head, report)
        self.assertEqual(report["destinations"], 1)
        for path in additions:
            self.assertEqual(self.git("ls-tree", report["mapping"][owner], "--", path),
                             self.git("ls-tree", head, "--", path))
        rewritten = report["mapping"][later]
        self.assertEqual(self.git("diff", "--binary", later + "^", later),
                         self.git("diff", "--binary", rewritten + "^", rewritten))

    def test_mixed_hunks_keep_independent_owners(self):
        middle = "unchanged\n" * 10
        self.write("settings.txt", "first draft\n" + middle + "last draft\n")
        first = self.commit("feat: initial settings")
        self.write("settings.txt", "first draft\n" + middle + "last added\n")
        last = self.commit("feat: final setting")
        self.write("settings.txt", "first fixed\n" + middle + "last fixed\n")
        self.write("helper.txt", "required by initial settings\n")
        head = self.commit("fix: settings and helper")
        plan = self.plan()
        modified = [hunk for hunk in plan["hunks"] if hunk["change"] == "modify"]
        self.assertEqual([hunk["target"] for hunk in modified], [first, last])
        for hunk in plan["hunks"]:
            if hunk["change"] == "add":
                hunk["target"] = first
        result = self.apply(plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assert_rewrite(head, report)
        self.assertEqual(report["destinations"], 2)
        self.assertEqual(self.git("show", report["mapping"][first] + ":settings.txt"),
                         ("first fixed\n" + middle + "last draft\n").encode())

    def test_modified_unicode_and_quoted_paths_preserve_tree(self):
        paths = ["设置.txt", "目录/检查 script.txt", "café-😀.txt",
                 'quote"back\\slash.txt', "tab\tnewline\n.txt"]
        for path in paths:
            self.write(path, "original\n")
        owner = self.commit("feat: text files")
        self.write("later.txt", "unrelated\n")
        self.commit("docs: later change")
        for path in paths:
            self.write(path, "corrected\n")
        head = self.commit("fix: text files")
        plan = self.plan()
        self.assertEqual(len(plan["hunks"]), len(paths))
        self.assertTrue(all(hunk["target"] == owner for hunk in plan["hunks"]))
        result = self.apply(plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assert_rewrite(head, report)
        self.assertEqual(report["destinations"], 1)
        for path in paths:
            self.assertEqual(self.git("show", report["mapping"][owner] + ":" + path),
                             b"corrected\n")

    def test_addition_requires_eligible_owner(self):
        self.write("owner.txt", "owner\n")
        self.commit("owner")
        self.write("new.txt", "new\n")
        head = self.commit("add file")
        plan = self.plan()
        for target in (None, self.base, head):
            with self.subTest(target=target):
                plan["hunks"][0]["target"] = target
                self.assert_rejected(plan, "needs a full eligible target SHA")

    def test_plan_content_cannot_be_changed(self):
        self.write("owner.txt", "owner\n")
        owner = self.commit("owner")
        self.write("new.txt", "new\n")
        self.commit("add file")
        plan = self.plan()
        plan["hunks"][0]["target"] = owner
        plan["hunks"][0]["new"] = ["different\n"]
        self.assert_rejected(plan, "Only hunk target fields may be edited")

    def test_existing_path_at_destination_is_rejected(self):
        self.write("shared.txt", "earlier\n")
        owner = self.commit("owner")
        (self.repo / "shared.txt").unlink()
        self.commit("remove shared file")
        self.write("shared.txt", "restored\n")
        self.commit("restore shared file")
        plan = self.plan()
        plan["hunks"][0]["target"] = owner
        self.assert_rejected(plan, "Added file already exists at destination")

    def test_later_addition_conflict_leaves_checkout_untouched(self):
        self.write("owner.txt", "owner\n")
        owner = self.commit("owner")
        self.write("shared.txt", "intervening content\n")
        self.commit("create shared file")
        (self.repo / "shared.txt").unlink()
        self.commit("remove shared file")
        self.write("shared.txt", "restored content\n")
        self.commit("restore shared file")
        plan = self.plan()
        plan["hunks"][0]["target"] = owner
        self.assert_rejected(plan, "git apply")

    def partial_fixture(self):
        self.write("owned.txt", "owner\n")
        owner = self.commit("owner")
        self.write("owned.txt", "corrected\n")
        self.write("残留 file.txt", "new\n")
        head = self.commit("fix: cleanup")
        return owner, head, self.plan()

    def test_partial_keeps_added_file_and_metadata(self):
        owner, head, plan = self.partial_fixture()
        self.git("branch", "v2", self.base)
        result = self.apply(plan, "--keep-unassigned")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assert_rewrite(head, report)
        self.assertEqual(report["residual"], report["head"])
        self.assertEqual(report["residual_hunks"], 1)
        self.assertEqual(self.git("rev-parse", "v2").decode().strip(), self.base)
        self.assertEqual(self.git("show", report["mapping"][owner] + ":owned.txt"), b"corrected\n")
        self.assertEqual(self.git("diff", "--name-only", "-z", "HEAD^", "HEAD"),
                         "残留 file.txt\0".encode())

    def test_default_still_rejects_unassigned(self):
        _, _, plan = self.partial_fixture()
        self.assert_rejected(plan, "needs a full eligible target SHA")

    def test_all_residual_rejected_without_rewrite(self):
        _, _, plan = self.partial_fixture()
        for hunk in plan["hunks"]:
            hunk["target"] = None
        self.assert_rejected(plan, "No assigned hunks", "--keep-unassigned")
        self.assertEqual(self.git("for-each-ref", "refs/backup"), b"")

    def test_empty_residual_has_no_extra_commit(self):
        owner, head, plan = self.partial_fixture()
        for hunk in plan["hunks"]:
            hunk["target"] = owner
        result = self.apply(plan, "--keep-unassigned")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assert_rewrite(head, report)
        self.assertIsNone(report["residual"])
        self.assertEqual(report["residual_hunks"], 0)
        self.assertNotIn(head, report["mapping"])

    def test_partial_rejects_protected_base_target(self):
        _, _, plan = self.partial_fixture()
        plan["hunks"][0]["target"] = self.base
        self.assert_rejected(plan, "needs a full eligible target SHA", "--keep-unassigned")

    def test_ambiguous_and_unowned_blocks_remain_residual(self):
        self.write("ambiguous.txt", "first\nsecond\n")
        self.write("owned.txt", "owner\n")
        first = self.commit("first")
        self.write("ambiguous.txt", "first\nsecond changed\n")
        self.commit("second")
        self.write("ambiguous.txt", "combined\n")
        self.write("owned.txt", "corrected\n")
        self.write("base.txt", "base corrected\n")
        head = self.commit("fix combined and owned")
        plan = self.plan()
        uncertain = [h for h in plan["hunks"] if h["file"] != "owned.txt"]
        self.assertTrue(all(h["target"] is None for h in uncertain))
        result = self.apply(plan, "--keep-unassigned")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assert_rewrite(head, report)
        self.assertEqual(report["residual_hunks"], 2)
        self.assertEqual(self.git("show", report["mapping"][first] + ":base.txt"), b"base\n")

    def test_partial_same_unicode_file_independent_blocks(self):
        path = '目录/设置"\\😀.txt'
        middle = "unchanged\n" * 10
        self.write(path, "first\n" + middle + "last\n")
        owner = self.commit("owner")
        self.write(path, "first fixed\n" + middle + "last fixed\n")
        head = self.commit("fix both")
        plan = self.plan()
        self.assertEqual(len(plan["hunks"]), 2)
        plan["hunks"][1]["target"] = None
        result = self.apply(plan, "--keep-unassigned")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assert_rewrite(head, report)
        self.assertEqual(self.git("show", report["mapping"][owner] + ":" + path),
                         ("first fixed\n" + middle + "last\n").encode())
        self.assertEqual(report["residual_hunks"], 1)

    def test_partial_conflict_leaves_branch_index_and_tree_untouched(self):
        self.write("shared.txt", "earlier\n")
        owner = self.commit("owner")
        self.write("shared.txt", "intervening\n")
        self.commit("later")
        self.write("shared.txt", "fixed\n")
        self.write("residual.txt", "remaining\n")
        self.commit("fix")
        plan = self.plan()
        for hunk in plan["hunks"]:
            if hunk["file"] == "shared.txt":
                hunk["target"] = owner
        self.assert_rejected(plan, "phase=absorb-selected, commit=" + owner, "--keep-unassigned")

    def test_partial_rejects_tampered_residual_content(self):
        _, _, plan = self.partial_fixture()
        for hunk in plan["hunks"]:
            if hunk["target"] is None:
                hunk["new"] = ["tampered\n"]
        self.assert_rejected(plan, "Only hunk target fields may be edited", "--keep-unassigned")

    def test_partial_rejects_signed_residual_head(self):
        self.partial_fixture()
        raw = self.git("cat-file", "commit", "HEAD")
        headers, message = raw.split(b"\n\n", 1)
        signed = headers + b"\ngpgsig placeholder\n\n" + message
        head = subprocess.check_output(
            ["git", "-C", str(self.repo), "hash-object", "-w", "-t", "commit", "--stdin"],
            input=signed, env=self.env).decode().strip()
        self.git("update-ref", "refs/heads/topic", head)
        plan = self.plan()
        self.assert_rejected(plan, "Signed commit would lose its signature", "--keep-unassigned")

    def test_inherited_git_environment_cannot_target_another_repository(self):
        sentinel = self.root / "sentinel"
        sentinel.mkdir()
        command = ["git", "-C", str(sentinel)]
        subprocess.check_call(command + ["init", "--quiet", "--initial-branch=sentinel", "--template="],
                              env=self.env)
        (sentinel / "base.txt").write_text("sentinel\n")
        subprocess.check_call(command + ["add", "--all"], env=self.env)
        subprocess.check_call(command + ["-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "sentinel"],
                              env=self.env)
        (sentinel / "pending.txt").write_text("must stay untracked\n")
        before_head = subprocess.check_output(command + ["rev-parse", "HEAD"], env=self.env)
        before_status = subprocess.check_output(command + ["status", "--porcelain"], env=self.env)
        before_index = (sentinel / ".git" / "index").read_bytes()
        contaminated = dict(self.env, GIT_DIR=str(sentinel / ".git"), GIT_WORK_TREE=str(sentinel),
                            GIT_INDEX_FILE=str(sentinel / ".git" / "index"),
                            GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="user.name", GIT_CONFIG_VALUE_0="Injected")
        result = subprocess.run([sys.executable, __file__, "AbsorbTest.test_binary_addition_is_rejected"],
                                env=contaminated, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(subprocess.check_output(command + ["rev-parse", "HEAD"], env=self.env), before_head)
        self.assertEqual(subprocess.check_output(command + ["status", "--porcelain"], env=self.env), before_status)
        self.assertEqual((sentinel / ".git" / "index").read_bytes(), before_index)

    def test_binary_addition_is_rejected(self):
        self.write("owner.txt", "owner\n")
        self.commit("owner")
        self.write("binary.dat", b"binary\x00content")
        self.commit("binary addition")
        result = self.command("plan", "--repo", str(self.repo), "--commit", "HEAD",
                              "--base", self.base, "--out", str(self.planpath))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Binary file unsupported", result.stderr)
        self.assertFalse(self.planpath.exists())


if __name__ == "__main__":
    unittest.main()
