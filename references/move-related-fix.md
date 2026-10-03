# Move a related fix beside an earlier commit

Keep the requested fix as a separate commit. The goal is an immediate parent relationship with the named anchor and an identical final tree, while preserving corrections that depend on later features.

## Inspect the destination and dependencies

Use the common repository checks and base boundary from `SKILL.md`. Inspect the HEAD diff, the anchor's versions of affected files, and intervening commits that introduced changed APIs or helpers. Blame identifies candidates; file availability and the construct's introduction establish whether an earlier placement is valid.

A whole-commit move can be invalid when it modifies files or endpoints introduced after the anchor. Related tests can have the same dependency even if their fixture file already exists. Generated-file ordering changes can belong to a later API change rather than the main fix.

If the user's requested whole-commit move has such dependencies and the handling is unspecified, explain the concrete conflict and clarify whether to move the main fix separately and fold dependent corrections into their later owners. Once that alternative is authorized, the intermediate residual commit and necessary patch splitting are part of the requested rewrite. Do not request the same authorization again. Do not silently introduce future features at the anchor to make a patch apply.

## Fold dependent changes, then move the residual

1. Save the original tip in a backup ref and record the base and other protected refs before either rewrite. Keep this original backup through both stages.
2. Follow the absorption workflow. Assign later feature corrections to their owners, and retain the main fix with `target: null`. Use `--keep-unassigned` only within the authorized scope. Read the result's `mapping` to resolve rewritten owner or anchor SHAs; use its `residual` as the commit to move. If there are no dependent corrections, skip absorption and move the original HEAD directly. An all-null absorption plan is unnecessary.
3. Review the residual diff against the anchor's production code, not only whether its patch applies. Final-tree equality does not prove that the moved commit implements its fix at that historical point: an earlier inline restoration call can survive a patch that only removes a later callback. Confirm the fix and tests work with the implementation present there. A combined changed block may need a finer split: directory regressions can move earlier while audio or debug endpoint regressions stay with the commits that introduced those endpoints. Do not edit a plan's `old`, `new`, or file fields to manufacture that split. Prepare separate patches or commits and regenerate plans as needed.
4. Move the remaining HEAD with the bundled command:

   ```bash
   python3 /absolute/skill/path/scripts/absorb.py move \
     --repo /absolute/repo --commit <residual-or-original-head> \
     --after <current-anchor-sha> --base <merge-base-sha> \
     --out /tmp/move-result.json
   ```

   The anchor must be the base or a preceding commit after it. The command applies HEAD immediately after the anchor in a temporary index, then replays intervening commits in their original order. It verifies the final tree and preserves messages and author/committer metadata before updating the current branch. It leaves the live index and working tree untouched. The same text-file, linear-history, clean-checkout, and signature restrictions apply as for absorption.

   The result contains `head`, `moved`, `after`, `mapping`, `backup`, and `changed`. `moved` can differ from the new HEAD because later commits remain after it. If already adjacent, it returns `changed: false`, an empty mapping, and no new backup. Treat a nonzero exit as failure; a result file alone is not proof of a branch update.

## When a move needs adaptation

On a patch conflict or final-tree mismatch, the command leaves the branch and checkout untouched. Inspect the reported commit and phase. A mistaken owner assignment can be corrected in a fresh plan. A valid fix can also depend on an older implementation shape: removing a later restoration callback may need to remove an inline restoration call at the earlier destination instead.

When adaptation is needed and already authorized, use an interactive rebase from the current anchor. Move the residual pick to the first position and mark it `edit`; mark later feature commits `edit` where dependent patches must be restored. Save those patches outside the repository before relocating them. Keep generated files owned by their generator and follow repository generation instructions.

At the moved commit, express the same fix against the implementation present there. When later commits replay, preserve their unrelated changes without reintroducing the behavior being corrected. Place deferred tests and API corrections where their production dependencies exist. Record any changes in ownership; do not use blanket ours/theirs resolutions, drop fixes, or overwrite the last tree to conceal a mistaken replay. The script itself does not adapt code or split combined blocks.

## Verify the completed rewrite

Check the final branch against the original pre-rewrite backup, not only the absorption stage's intermediate tip:

```bash
git -C /absolute/repo diff --exit-code <original-backup> HEAD
git -C /absolute/repo rev-parse <moved-sha>^
git -C /absolute/repo status --short
```

The moved commit's parent must equal the current anchor SHA. Verify that the old fix SHA is no longer an ancestor of HEAD, protected refs are unchanged, and the moved diff contains only the intended fix. Review a range-diff to check the folded corrections and the order of unrelated commits. Compose stage mappings when reporting rewritten IDs; the absorption result's HEAD is no longer the final HEAD after relocation.

Run repository-required checks and generation verification. An identical final tree needs no additional behavior tests solely because history changed. If adapting the fix or splitting dependencies raises a concrete concern about the moved commit itself, verify its focused behavior at that revision in an isolated checkout. Report the moved SHA and anchor, folded destination count, final HEAD, original backup, and material deviations or unfinished work. Keep backups and do not push unless explicitly requested.
