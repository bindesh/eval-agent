"""Loading local configuration without putting credentials in a shell profile."""

from agent_eval.env import find_env_file, load_env, parse_env_file


def test_parses_the_usual_shapes():
    parsed = parse_env_file(
        "# a comment\n"
        "\n"
        "ANTHROPIC_API_KEY=sk-ant-plain\n"
        "export EXPORTED=yes\n"
        'QUOTED="has spaces"\n'
        "SINGLE='single'\n"
        "TRAILING=value # not part of it\n"
        "EMPTY=\n"
        "not a pair\n"
    )
    assert parsed == {
        "ANTHROPIC_API_KEY": "sk-ant-plain", "EXPORTED": "yes",
        "QUOTED": "has spaces", "SINGLE": "single",
        "TRAILING": "value", "EMPTY": "",
    }


def test_a_hash_inside_a_quoted_value_is_kept():
    assert parse_env_file('KEY="abc#def"') == {"KEY": "abc#def"}


def test_the_shell_wins_over_the_file(tmp_path, monkeypatch):
    """A stale .env must never silently override what you just exported."""
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-file\nOTHER=from-file\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    monkeypatch.delenv("OTHER", raising=False)

    applied = load_env(tmp_path / ".env")

    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"
    assert os.environ["OTHER"] == "from-file"
    assert applied == ["OTHER"], "only names actually set are reported"


def test_loading_reports_names_never_values(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SECRET_THING=sk-ant-do-not-print\n")
    monkeypatch.delenv("SECRET_THING", raising=False)
    applied = load_env(tmp_path / ".env")
    assert applied == ["SECRET_THING"]
    assert not any("sk-ant" in name for name in applied)


def test_missing_file_is_not_an_error(tmp_path):
    assert load_env(tmp_path / "nope.env") == []


def test_finds_the_file_by_walking_up(tmp_path):
    (tmp_path / ".env").write_text("A=1\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_env_file(nested) == tmp_path / ".env"


def test_the_repo_ignores_dotenv():
    from .conftest import REPO_ROOT
    assert ".env\n" in (REPO_ROOT / ".gitignore").read_text()
    assert (REPO_ROOT / ".env.example").exists()
