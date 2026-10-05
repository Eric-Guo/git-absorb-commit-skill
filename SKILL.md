---
name: git-absorb-commit
description: Absorb a cleanup or fixup commit (HEAD or an ancestor) into preceding owners, or move it beside a related earlier commit while keeping dependent fixes with later owners. Preserve the final tree. Supports modified text hunks, new text files, and explicitly retained residual changes.
---

# Absorb or move a related fix

Preserve the final file tree exactly. Respect the user's named destination commits. Otherwise attribute modified lines by history, not simply the most recent commit touching each file. Different hunks in one file can belong to different commits; newly added files need an explicit ownership decision because they have no blame history.

Use the bundled Python 3 script for planning and rewriting. It uses a temporary index, preserves commit messages and author/committer metadata, keeps a backup ref, and updates the current branch only after verifying the final tree. Neither absorption nor a supported move needs a checkout, reset, stash, push, or live worktree edit.

Choose the requested outcome:

- **Absorb:** eliminate the cleanup commit by folding its changes into earlier owners, optionally retaining an explicitly requested residual commit. Follow the workflow below.
- **Move:** keep the fix as a separate commit immediately after a named earlier commit. Read [Move a related fix](references/move-related-fix.md). A request to move does not authorize absorbing the main fix into the anchor. When the commit mixes that fix with later feature corrections, inspect dependencies before choosing between a whole-commit move and partial absorption followed by a move.

## Absorption workflow

1. Read applicable repository instructions. Inspect `git status --short`, branch, target diff, and the branch base. Use the repository's default branch, not an assumed `main`. Resolve the boundary with `git merge-base HEAD <base-ref>`.
2. Absorption accepts HEAD or an older ancestor after the base. The plan records the checkout `head` separately from the selected `commit`; blame comes from the selected commit’s parent, and destinations must precede that commit. Descendants replay in order automatically. This is covered by an explicit absorption request and needs no separate approval merely because the commit is older. Moving a separate fix remains HEAD-only.

   The script supports a nonempty **commit on the current branch**, added or modified UTF-8 regular text files, and linear history since the boundary. Text must end with a newline unless empty. It rejects dirty worktrees, merges, deletions/renames, mode changes to existing files, and signed commits that would need rewriting. If unsupported, explain the limitation; do not reset, drop changes, or broaden the rewrite automatically. Ask only for genuinely missing scope or a necessary alternative.
3. Generate a plan outside the repository:

   ```bash
   python3 /absolute/skill/path/scripts/absorb.py plan --repo /absolute/repo --commit <sha> --base <merge-base-sha> --out /tmp/absorb-plan.json
   ```

4. Read the JSON plan and review every assignment:

   - `change: "modify"` hunks show old/new lines and blame candidates, with counts and commit subjects. A sole eligible owner is filled automatically. For a hunk spanning several commits, inspect `git show <candidate> -- <file>` and choose the commit that introduced the construct being corrected. Formatting of a combined construct normally belongs to the commit that assembled it.
   - `change: "add"` contains an entire new file, including an empty file. It has no blame candidates and its `target` stays unset. Use a user-specified owner or inspect earlier commits for evidence such as the import or feature that requires the missing file. Do not guess from the latest directory edit; ask only if ownership remains unclear.
   - Keep assignments within the user's intended scope and after the base. Do not assign a whole modified file by its latest edit date, pick by majority alone, or rewrite upstream commits outside the boundary.

5. Edit **only each hunk's `target`** in the plan, using a full commit SHA. The script validates the remaining plan. Targets must precede the selected commit and follow the base; descendants cannot be destinations. The default remains all-or-nothing: every hunk needs an eligible target. Only when the user explicitly permits a residual commit or authorizes moving the main fix separately while folding dependent changes into their owners, leave intentionally unabsorbed hunks at `target: null` and use `--keep-unassigned` on apply. Do not invent owners merely to clear ambiguity. At least one hunk must be assigned; all-null plans are rejected without a rewrite. Added files move whole to one owner, and their paths must be absent at that destination. If a changed block or added file genuinely contains unrelated fixes for multiple owners, the script cannot split it further. Prepare a finer patch split within the authorized scope and regenerate the plan, or retain the block when permitted; ask only when that needs an unresolved ownership decision. Independent changed blocks within the same modified file are already separate hunks.
6. Give a short update with the number of destination commits and any residual hunks. An explicit user request to eliminate/absorb the commit authorizes the local rewrite; do not ask again. For a review-only request, stop after planning.
7. Apply:

   ```bash
   python3 /absolute/skill/path/scripts/absorb.py apply --repo /absolute/repo --plan /tmp/absorb-plan.json
   ```

   For an explicitly authorized partial absorption, append `--keep-unassigned`. The script applies null-target patches at the selected commit’s original position in a temporary index, before replaying descendants, checks the exact final tree, and creates one residual commit preserving the selected commit message and author/committer metadata. It does not simply force the final tree to match. If no hunks remain, no residual commit is created. Signed commits that would need rewriting, including a residual commit, are rejected. The result includes `absorbed` (selected SHA), `replayed_descendants`, `residual` (new SHA or null) and `residual_hunks`; the selected commit maps to the residual SHA when one exists.

   On a patch conflict, the script leaves the branch and working tree untouched. Inspect the reported commit and patch ownership. Correct a demonstrated attribution mistake and retry; do not use blanket ours/theirs resolutions or force the final tree into the last commit.
8. Verify `git status --short`, `git diff --exit-code <backup-ref> HEAD`, and that the removed SHA is no longer an ancestor of HEAD. Report destination count, residual hunk count and SHA if present, new HEAD, and backup ref. A retained residual commit has a new SHA and may precede HEAD; inspect its own parent-to-commit diff. The backup ref points to the original checkout HEAD, not the selected older commit.

   A retained residual commit has a new SHA; the selected commit must no longer be an ancestor. Verify protected branch refs remain unchanged and that the residual diff contains only the explicitly retained changes. The script writes an old-to-new mapping beside the plan. A history-only rewrite with an identical tree needs history verification; it introduces no new source behavior to test. Follow any explicitly required repository checks.

Keep the backup ref. Do not push unless explicitly requested. Existing commit messages remain unchanged, even if today's repository conventions differ.
