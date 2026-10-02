# git-absorb-commit

An [Agent Skill](https://agentskills.io/specification) that folds a cleanup or fixup **HEAD commit** into the earlier commits that own its changes, without changing the final file tree.

Unlike assigning each file to its last editor, this skill reviews ownership at the changed-block level. It supports newly added text files and an explicitly requested residual commit for changes that cannot safely be assigned.

## Requirements

- Python 3.8 or newer, using only the standard library
- Git 2.28 or newer on `PATH`
- A clean, named branch with linear history after an explicitly chosen base

Development verification uses Linux, Python 3.12, and Git 2.52. The test suite creates temporary repositories; it does not rewrite your project.

## Install

Clone into a directory named `git-absorb-commit`:

```sh
git clone https://github.com/Eric-Guo/git-absorb-commit-skill.git git-absorb-commit
```

Use your agent's skill installation mechanism to install that directory. It contains a standard `SKILL.md`, optional `agents/openai.yaml` metadata, and self-contained scripts. For manual installation, copy those files into a `git-absorb-commit` folder in your agent's supported skills location; consult your agent's documentation for that location.

For example, a local Codex user install can live at `~/.agents/skills/git-absorb-commit`. Copy the skill there only if that destination does not already contain a version you want to keep.

- [Agent Skills format](https://agentskills.io/specification)
- [OpenAI skill documentation](https://learn.chatgpt.com/docs/build-skills)

No package installation, API key, service, or network access is needed to run the skill after installation. This project does not depend on the separate `git-absorb` executable.

## Ask your agent

> Use git-absorb-commit to absorb my latest cleanup commit into its earlier owners. Preserve the final tree and do not rewrite the base branch.

For partial absorption:

> Absorb only the changes with a clear owner. Keep the remaining changes in a final commit.

Read [SKILL.md](SKILL.md) for the complete ownership-review and verification workflow. A request to review a plan alone does not authorize a rewrite or a push.

## Direct command-line use

Replace all paths and choose the correct base branch for your repository. The commit must be `HEAD`. Save the plan **outside** the target repository so it stays clean.

```sh
REPO=/path/to/your/repository
SKILL=/path/to/git-absorb-commit
BASE=$(git -C "$REPO" merge-base HEAD origin/main)

python3 "$SKILL/scripts/absorb.py" plan \
  --repo "$REPO" --commit HEAD --base "$BASE" \
  --out /tmp/absorb-plan.json
```

Review every hunk in the JSON plan. Modify only its `target`: use the full SHA of an intended owner **after the base and before HEAD**. A sole eligible blame candidate is prefilled; it still requires review. New files have no blame history. Do not choose a target by majority alone or move changes into an upstream commit.

Apply an entirely assigned plan:

```sh
python3 "$SKILL/scripts/absorb.py" apply \
  --repo "$REPO" --plan /tmp/absorb-plan.json
```

If partial absorption is intended, leave unassigned hunks at `"target": null` and opt in explicitly:

```sh
python3 "$SKILL/scripts/absorb.py" apply \
  --repo "$REPO" --plan /tmp/absorb-plan.json --keep-unassigned
```

At least one hunk must be assigned. An all-null plan is rejected without rewriting. No extra commit is created when no residual hunks remain. Otherwise, the residual HEAD retains the original HEAD's message and author/committer metadata.

The JSON report includes the new `head`, a `backup` ref, destination count, old-to-new `mapping`, `residual` SHA (or null), and `residual_hunks`. A matching `.result.json` file is written beside the plan. Treat a nonzero exit as failure; a report file alone is not proof that the branch was updated.

## Safety and limits

- Uses a temporary index and updates only the current branch, with an expected-old-SHA check
- Checks exact final-tree equality before the branch update; keeps a backup ref
- Preserves existing commit messages and author/committer identity and dates
- Does not checkout, reset, stash, push, or edit the live working tree
- A failed patch leaves the branch, working tree, and live index unchanged, but may leave a backup ref and unreachable Git objects
- Rewriting changes commit IDs; coordinate before rewriting history already shared with others
- The base boundary must be correct: the script does not infer which other branch refs you consider protected
- Supports nonempty HEAD commits containing added or modified UTF-8 regular text files; nonempty files must end in a newline
- Rejects merges in the rewrite range, deletions, renames, existing-file mode changes, and signatures that would be lost by rewriting
- Cannot split one changed block or one added file between multiple owners; retain it or prepare a finer split separately
- A partial plan can still conflict; inspect the reported commit, phase, file, and hunk IDs instead of forcing a resolution

Keep the backup ref until you have reviewed the rewritten history. Verify the final tree, clean status, mapping, residual diff, and protected branch refs. Pushing is a separate decision and is never done by these scripts.

## Tests and contributions

```sh
python3 scripts/test_absorb.py
```

The suite covers ownership splitting, added files, Unicode and quoted paths, metadata preservation, partial absorption, unassigned and ambiguous hunks, protected-base rejection, plan tampering, signed residual rejection, and conflict safety. Add an isolated regression test for behavior changes. Use synthetic repositories and redact private paths, commit data, logs, and credentials from reports.

## License

[MIT](LICENSE).
