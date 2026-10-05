#!/usr/bin/env python3
"""Absorb an ancestor commit or move a HEAD commit while preserving the final tree."""
import argparse
import difflib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid


def git(repo, *args, data=None, env=None):
    result = subprocess.run(["git", "-C", str(repo), *args], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if result.returncode:
        raise ValueError(f"git {' '.join(args)}\n{result.stderr.decode(errors='replace')}")
    return result.stdout


def sha(repo, ref):
    return git(repo, "rev-parse", "--verify", ref + "^{commit}").decode().strip()


def inspect(repo, commit, base, allow_single=False, allow_older=True):
    head = sha(repo, "HEAD")
    commit, base = sha(repo, commit), sha(repo, base)
    if not allow_older and commit != head:
        raise ValueError("Only a HEAD commit is supported.")
    if git(repo, "status", "--porcelain").strip():
        raise ValueError("Working tree must be clean, including untracked files.")
    branch = git(repo, "symbolic-ref", "HEAD").decode().strip()
    git(repo, "merge-base", "--is-ancestor", base, commit)
    git(repo, "merge-base", "--is-ancestor", commit, head)
    if git(repo, "rev-list", "--merges", base + ".." + head).strip():
        raise ValueError("Merge commits in the rewrite range are unsupported.")
    commits = git(repo, "rev-list", "--reverse", base + ".." + head).decode().splitlines()
    if commit not in commits or (not allow_single and commits.index(commit) < 1):
        raise ValueError("No preceding destination commits after the base.")
    eligible = set(commits[:commits.index(commit)])
    changes = git(repo, "diff", "--raw", "--no-abbrev", "--no-renames", "-z", commit + "^", commit).split(b"\0")
    hunks = []
    for i in range(0, len(changes) - 1, 2):
        info, path = changes[i].decode(), changes[i + 1].decode()
        oldmode, newmode, _, _, status = info.split()
        if status not in ("M", "A") or newmode not in ("100644", "100755"):
            raise ValueError(f"Only added or modified regular text files are supported: {path}")
        if status == "M" and oldmode[1:] != newmode:
            raise ValueError(f"Mode changes are unsupported: {path}")
        before = b"" if status == "A" else git(repo, "show", commit + "^:" + path)
        after = git(repo, "show", commit + ":" + path)
        if b"\0" in before + after:
            raise ValueError(f"Binary file unsupported: {path}")
        old, new = before.decode().splitlines(keepends=True), after.decode().splitlines(keepends=True)
        if (before and not before.endswith(b"\n")) or (after and not after.endswith(b"\n")):
            raise ValueError(f"Missing final newline unsupported: {path}")
        if status == "A":
            # A new file has no blame history, including when it is empty.
            hunks.append({"id": len(hunks) + 1, "file": path, "change": "add", "start": 0, "end": 0,
                          "old": [], "new": new, "candidates": [], "target": None})
            continue
        for kind, a, b, c, d in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if kind == "equal":
                continue
            start, end = (a + 1, b) if a < b else (max(1, a), min(len(old), a + 1))
            counts = {}
            if old:
                blame = git(repo, "blame", "--line-porcelain", "-L", f"{start},{end}", commit + "^", "--", path).decode()
                for owner in re.findall(r"^([0-9a-f]{40,64}) \d+ \d+(?: \d+)?$", blame, re.M):
                    counts[owner] = counts.get(owner, 0) + 1
            candidates = [{"commit": owner, "lines": count, "eligible": owner in eligible,
                           "subject": git(repo, "show", "-s", "--format=%s", owner).decode().strip()}
                          for owner, count in counts.items()]
            owners = [x["commit"] for x in candidates if x["eligible"]]
            hunks.append({"id": len(hunks) + 1, "file": path, "change": "modify", "start": a, "end": b,
                          "old": old[a:b], "new": new[c:d], "candidates": candidates,
                          "target": owners[0] if len(owners) == 1 else None})
    if not hunks:
        raise ValueError("No text changes to absorb.")
    return {"head": head, "commit": commit, "base": base, "branch": branch, "hunks": hunks}, commits


def patch(repo, head, path, hunks):
    if hunks[0]["change"] == "add":
        # Keep Git's creation headers, executable mode, and path quoting intact.
        return git(repo, "diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-renames",
                   head + "^", head, "--", path)
    before = git(repo, "show", head + "^:" + path)
    lines = before.decode().splitlines(keepends=True)
    for hunk in sorted(hunks, key=lambda h: h["start"], reverse=True):
        lines[hunk["start"]:hunk["end"]] = hunk["new"]
    after = "".join(lines).encode()
    oldblob = git(repo, "hash-object", "-w", "--stdin", data=before).decode().strip()
    newblob = git(repo, "hash-object", "-w", "--stdin", data=after).decode().strip()
    # Git uses C-style quoting with octal UTF-8 bytes, not JSON Unicode escapes.
    def quote_path(value):
        return '"' + "".join(
            chr(byte) if 32 <= byte < 127 and byte not in (34, 92)
            else "\\" + chr(byte) if byte in (34, 92)
            else f"\\{byte:03o}"
            for byte in value.encode("utf-8")
        ) + '"'

    left, right = quote_path("a/" + path), quote_path("b/" + path)
    diff = "".join(difflib.unified_diff(before.decode().splitlines(keepends=True), lines,
                                      fromfile=left, tofile=right))
    return (f"diff --git {left} {right}\nindex {oldblob}..{newblob}\n" + diff).encode()


def apply_patch_with_context(repo, delta, env, phase, commit, hunks=(), path=None):
    try:
        git(repo, "apply", "--cached", "--3way", "--whitespace=nowarn", data=delta, env=env)
    except ValueError as error:
        ids = [h["id"] for h in hunks]
        raise ValueError(f"Patch failed: phase={phase}, commit={commit}, "
                         f"path={path!r}, hunk_ids={ids}\n{error}") from error


def create_commit(repo, raw_commit, tree, parent, env):
    headers, message = raw_commit.split(b"\n\n", 1)
    metadata = {}
    for line in headers.splitlines():
        if line.startswith((b"author ", b"committer ")):
            role, identity = line.split(b" ", 1)
            name, rest = identity.rsplit(b" <", 1)
            email, date = rest.split(b"> ", 1)
            prefix = "GIT_" + role.decode().upper()
            metadata.update({prefix + "_NAME": name.decode(), prefix + "_EMAIL": email.decode(),
                             prefix + "_DATE": date.decode()})
    return git(repo, "-c", "commit.gpgSign=false", "commit-tree", tree, "-p", parent,
                 data=message, env=dict(env, **metadata)).decode().strip()


def apply(repo, planpath, keep_unassigned=False):
    plan = json.loads(planpath.read_text())
    fresh, commits = inspect(repo, plan["commit"], plan["base"])
    cleanup = plan["commit"]
    eligible = commits[:commits.index(cleanup)]
    if len(plan["hunks"]) != len(fresh["hunks"]):
        raise ValueError("Plan hunks changed; regenerate it.")
    for supplied, expected in zip(plan["hunks"], fresh["hunks"]):
        target = supplied.get("target")
        if target not in eligible and not (keep_unassigned and target is None):
            raise ValueError(f"Hunk {expected['id']} needs a full eligible target SHA.")
        expected["target"] = target
    if plan != fresh:
        raise ValueError("Only hunk target fields may be edited; regenerate the plan.")
    for hunk in plan["hunks"]:
        if hunk["target"] is not None and hunk["change"] == "add" and git(repo, "ls-tree", hunk["target"], "--", hunk["file"]).strip():
            raise ValueError(f"Added file already exists at destination {hunk['target']}: {hunk['file']}")
    targets = {h["target"] for h in plan["hunks"] if h["target"] is not None}
    residual = [h for h in plan["hunks"] if h["target"] is None]
    if not targets:
        raise ValueError("No assigned hunks to absorb; branch was not updated.")
    earliest = min(commits.index(c) for c in targets)
    rewrite = [c for c in commits[earliest:] if c != cleanup]
    raw = {c: git(repo, "cat-file", "commit", c)
           for c in rewrite + ([cleanup] if residual else [])}
    for c, value in raw.items():
        if b"\ngpgsig" in value.split(b"\n\n", 1)[0]:
            raise ValueError(f"Signed commit would lose its signature: {c}")
    backup = "refs/backup/absorb-" + plan["head"][:12] + "-" + uuid.uuid4().hex[:8]
    git(repo, "update-ref", backup, plan["head"], "0" * len(plan["head"]))
    mapping = {}
    with tempfile.TemporaryDirectory(prefix="git-absorb-") as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        parent = sha(repo, rewrite[0] + "^")
        git(repo, "read-tree", parent, env=env)
        for commit in commits[earliest:]:
            if commit == cleanup:
                if residual:
                    for path in sorted({h["file"] for h in residual}):
                        delta = patch(repo, cleanup, path, [h for h in residual if h["file"] == path])
                        apply_patch_with_context(repo, delta, env, "retain-residual", cleanup,
                                                 [h for h in residual if h["file"] == path], path)
                    tree = git(repo, "write-tree", env=env).decode().strip()
                    parent = create_commit(repo, raw[cleanup], tree, parent, env)
                    mapping[cleanup] = parent
                continue
            original = git(repo, "diff", "--binary", "--no-ext-diff", commit + "^", commit)
            if original:
                apply_patch_with_context(repo, original, env, "replay-original", commit)
            selected = [h for h in plan["hunks"] if h["target"] == commit]
            for path in sorted({h["file"] for h in selected}):
                delta = patch(repo, cleanup, path, [h for h in selected if h["file"] == path])
                apply_patch_with_context(repo, delta, env, "absorb-selected", commit,
                                         [h for h in selected if h["file"] == path], path)
            tree = git(repo, "write-tree", env=env).decode().strip()
            parent = create_commit(repo, raw[commit], tree, parent, env)
            mapping[commit] = parent
        if git(repo, "rev-parse", parent + "^{tree}") != git(repo, "rev-parse", plan["head"] + "^{tree}"):
            raise ValueError("Final tree differs; branch was not updated.")
        if git(repo, "status", "--porcelain").strip() or git(repo, "symbolic-ref", "HEAD").decode().strip() != plan["branch"]:
            raise ValueError("Checkout changed during rewrite; branch was not updated.")
        report = {"head": parent, "backup": backup, "destinations": len(targets), "mapping": mapping,
                  "residual": mapping.get(cleanup), "residual_hunks": len(residual),
                  "absorbed": cleanup, "replayed_descendants": len(commits) - commits.index(cleanup) - 1}
        planpath.with_suffix(".result.json").write_text(json.dumps(report, indent=2) + "\n")
        git(repo, "update-ref", "-m", "absorb commit into preceding owners", plan["branch"], parent, plan["head"])
    print(json.dumps(report, indent=2))


def move(repo, commit, after, base, out):
    state, commits = inspect(repo, commit, base, allow_single=True, allow_older=False)
    head, after = state["head"], sha(repo, after)
    if after != state["base"] and after not in commits[:-1]:
        raise ValueError("The anchor must be the base or a preceding commit after it.")
    if after == sha(repo, head + "^"):
        report = {"head": head, "moved": head, "after": after, "backup": None,
                  "mapping": {}, "changed": False}
        out.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        return
    later = commits[commits.index(after) + 1:-1] if after in commits else commits[:-1]
    order = [head] + later
    raw = {c: git(repo, "cat-file", "commit", c) for c in order}
    for c, value in raw.items():
        if b"\ngpgsig" in value.split(b"\n\n", 1)[0]:
            raise ValueError(f"Signed commit would lose its signature: {c}")
    backup = "refs/backup/move-" + head[:12] + "-" + uuid.uuid4().hex[:8]
    git(repo, "update-ref", backup, head, "0" * len(head))
    mapping = {}
    with tempfile.TemporaryDirectory(prefix="git-move-") as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        parent = after
        git(repo, "read-tree", parent, env=env)
        for current in order:
            delta = git(repo, "diff", "--binary", "--no-ext-diff", current + "^", current)
            if delta:
                apply_patch_with_context(repo, delta, env,
                                         "move-selected" if current == head else "replay-original", current)
            tree = git(repo, "write-tree", env=env).decode().strip()
            parent = create_commit(repo, raw[current], tree, parent, env)
            mapping[current] = parent
        if git(repo, "rev-parse", parent + "^{tree}") != git(repo, "rev-parse", head + "^{tree}"):
            raise ValueError("Final tree differs; branch was not updated.")
        if git(repo, "status", "--porcelain").strip() or git(repo, "symbolic-ref", "HEAD").decode().strip() != state["branch"]:
            raise ValueError("Checkout changed during rewrite; branch was not updated.")
        report = {"head": parent, "moved": mapping[head], "after": after, "backup": backup,
                  "mapping": mapping, "changed": True}
        out.write_text(json.dumps(report, indent=2) + "\n")
        git(repo, "update-ref", "-m", "move commit after requested anchor", state["branch"], parent, head)
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--repo", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("apply")
    p.add_argument("--repo", required=True)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--keep-unassigned", action="store_true",
                   help="Keep null-target hunks in a final residual commit; at least one assigned hunk is required")
    p = sub.add_parser("move", help="Move HEAD immediately after an earlier commit without absorbing it")
    p.add_argument("--repo", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--after", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--out", type=Path, required=True, help="Write the JSON report outside the repository")
    args = parser.parse_args()
    try:
        if args.command == "plan":
            plan, _ = inspect(args.repo, args.commit, args.base)
            args.out.write_text(json.dumps(plan, indent=2) + "\n")
            print(f"Saved {len(plan['hunks'])} hunks to {args.out}; review every target before apply.")
        elif args.command == "apply":
            apply(args.repo, args.plan, keep_unassigned=args.keep_unassigned)
        else:
            move(args.repo, args.commit, args.after, args.base, args.out)
    except (ValueError, UnicodeError, OSError, KeyError) as error:
        parser.exit(1, f"Stopped: {error}\n")


if __name__ == "__main__":
    main()
