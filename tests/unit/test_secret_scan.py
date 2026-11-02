from pathlib import Path

from decline_desk.secret_scan import find_secrets, scan_files

REPO_ROOT = Path(__file__).resolve().parents[2]
# Built at runtime so this test file never contains a real-looking key itself.
REAL_LOOKING_KEY = "sk-ant-api03-" + "A1b2C3d4E5f6G7h8I9j0" * 4


def test_flags_a_real_looking_anthropic_key():
    assert find_secrets(f"ANTHROPIC_API_KEY={REAL_LOOKING_KEY}\n") == [1]


def test_reports_the_line_number_of_each_hit():
    text = f"line one\nkey = '{REAL_LOOKING_KEY}'\nline three\n"
    assert find_secrets(text) == [2]


def test_ignores_short_obviously_fake_test_keys():
    assert find_secrets('secret = "sk-ant-not-a-real-key"') == []


def test_ignores_empty_placeholder_in_env_example():
    assert find_secrets("ANTHROPIC_API_KEY=\n") == []


def test_scan_files_returns_path_and_line(tmp_path):
    leaky = tmp_path / "leaky.py"
    leaky.write_text(f"x = 1\nKEY = '{REAL_LOOKING_KEY}'\n")
    clean = tmp_path / "clean.py"
    clean.write_text("x = 1\n")

    assert scan_files([leaky, clean]) == [(leaky, 2)]


def test_scan_files_skips_missing_and_binary_files(tmp_path):
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00\xff\x00\xfe")

    assert scan_files([tmp_path / "deleted.py", binary]) == []


def test_env_file_is_gitignored_but_example_is_not():
    lines = (REPO_ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in lines
    assert "!.env.example" in lines


def test_pre_commit_hook_runs_the_scanner():
    hook = (REPO_ROOT / "scripts" / "pre-commit").read_text()
    assert "decline_desk.secret_scan" in hook
