Implement a per-benchmark ignore list for agent workspaces in this repo (agent-eval).

Background: read docs/trust-incident.md, section 1, "What remains". GIT_EXCLUDES in
src/agent_eval/workspace.py is a fixed list of Python/JS cache patterns. A benchmark in
another language whose toolchain writes build output into the tree (Rust `target/`,
Go binaries, Gradle `build/`) would get that output into every agent diff, the
changed-files count, and the patch the judge reads - the same bug we already fixed once
for __pycache__.

What I want:
- An optional `workspace_excludes:` list of gitignore-style patterns in benchmark.yaml.
- Applied on top of the built-in defaults (never instead of them) everywhere a workspace
  is created - agent runs and `doctor`.
- Benchmarks without the key behave exactly as today.
- Tests for the new behaviour, following the style of tests/test_workspace.py.
- Document the key where benchmark.yaml fields are documented.

Follow AGENTS.md. Start with a short plan (which files, and anything you think is risky),
then implement. Before you finish, run the full pytest suite, ruff and mypy with the
tools in .venv/bin, and tell me exactly what you ran and what passed or failed.
