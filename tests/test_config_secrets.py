"""Tests for the single-file secrets source in gridbot.config.

The posture under test: one file, owned by the service user, no group/other
bits. Anything unsafe or malformed fails CLOSED (every secret absent), and
removing a key from the file disarms at the next read.
"""

from __future__ import annotations

import logging
import os

import pytest

from gridbot import config
from gridbot.redaction import redact_text

NAME = "X_GRIDBOT_TEST_SECRET"
VALUE = "value-under-test-123"


def _write(path, text: str, mode: int = 0o600):
    if path.exists():
        os.chmod(path, 0o600)
    path.write_text(text)
    os.chmod(path, mode)
    return path


@pytest.fixture
def secrets_file(tmp_path, monkeypatch):
    path = tmp_path / "secrets.env"
    monkeypatch.setenv(config.SECRETS_FILE_ENV, str(path))
    return path


def test_reads_a_safe_file(secrets_file) -> None:
    _write(secrets_file, f"# comment\n\n{NAME}={VALUE}\nOTHER_NAME = 'quoted value'\n")
    assert config._secret(NAME) == VALUE
    assert config._secret("OTHER_NAME") == "quoted value"
    assert config.require(NAME) == VALUE


def test_missing_file_is_absent_and_require_raises(secrets_file) -> None:
    assert config._secret(NAME) is None
    with pytest.raises(config.MissingSecret) as exc:
        config.require(NAME)
    assert VALUE not in str(exc.value)


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o660])
def test_group_or_world_bits_fail_closed(secrets_file, mode, caplog) -> None:
    _write(secrets_file, f"{NAME}={VALUE}\n", mode)
    with caplog.at_level(logging.ERROR, logger="gridbot.config"):
        assert config._secret(NAME) is None
    assert VALUE not in caplog.text
    with pytest.raises(config.SecretsFileError):
        config.load_secrets()


def test_read_only_0400_is_accepted(secrets_file) -> None:
    _write(secrets_file, f"{NAME}={VALUE}\n", 0o400)
    assert config._secret(NAME) == VALUE


def test_symlink_is_refused(tmp_path, monkeypatch) -> None:
    real = _write(tmp_path / "real.env", f"{NAME}={VALUE}\n")
    link = tmp_path / "link.env"
    link.symlink_to(real)
    monkeypatch.setenv(config.SECRETS_FILE_ENV, str(link))
    assert config._secret(NAME) is None


def test_malformed_line_is_refused_without_echoing_it(secrets_file) -> None:
    _write(secrets_file, f"{NAME}={VALUE}\nthis line has {VALUE} and no equals\n")
    with pytest.raises(config.SecretsFileError) as exc:
        config.load_secrets()
    assert "line 2" in str(exc.value)
    assert VALUE not in str(exc.value)
    assert config._secret(NAME) is None  # the whole file is refused, not just the line


def test_removing_a_key_disarms_on_the_next_read(secrets_file) -> None:
    _write(secrets_file, f"{NAME}={VALUE}\n")
    assert config._secret(NAME) == VALUE
    _write(secrets_file, "# key revoked\n")
    assert config._secret(NAME) is None
    secrets_file.unlink()
    assert config._secret(NAME) is None


def test_same_size_in_place_revoke_with_preserved_mtime_disarms(secrets_file) -> None:
    """A stat-keyed cache would serve the old key here (review finding)."""
    _write(secrets_file, f"{NAME}={VALUE}\n")
    st = os.stat(secrets_file)
    assert config._secret(NAME) == VALUE
    with open(secrets_file, "r+") as fh:  # same size, same inode
        fh.write("#")
    os.utime(secrets_file, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert os.stat(secrets_file).st_size == st.st_size
    assert config._secret(NAME) is None


def test_invalid_utf8_fails_closed_without_carrying_bytes(secrets_file) -> None:
    secrets_file.write_bytes(f"{NAME}={VALUE}\n".encode() + b"\xff\xfe\n")
    os.chmod(secrets_file, 0o600)
    with pytest.raises(config.SecretsFileError) as exc:
        config.load_secrets()
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    assert VALUE not in str(exc.value)
    assert config._secret(NAME) is None


def test_fifo_is_refused_without_hanging(tmp_path, monkeypatch) -> None:
    fifo = tmp_path / "fifo.env"
    os.mkfifo(fifo, 0o600)
    monkeypatch.setenv(config.SECRETS_FILE_ENV, str(fifo))
    assert config._secret(NAME) is None


def test_duplicate_name_is_refused(secrets_file) -> None:
    _write(secrets_file, f"{NAME}={VALUE}\n{NAME}=other-value-456\n")
    with pytest.raises(config.SecretsFileError, match="set twice"):
        config.load_secrets()


def test_callers_get_independent_mappings(secrets_file) -> None:
    _write(secrets_file, f"{NAME}={VALUE}\n")
    config.load_secrets().pop(NAME)
    assert config._secret(NAME) == VALUE


def test_environment_is_not_a_secret_source(secrets_file, monkeypatch) -> None:
    monkeypatch.setenv(NAME, VALUE)
    assert config._secret(NAME) is None


def test_values_are_registered_with_the_redactor(secrets_file) -> None:
    _write(secrets_file, f"{NAME}={VALUE}\n")
    config._secret(NAME)
    assert VALUE not in redact_text(f"leaked {VALUE} here")


def test_systemd_credentials_directory_is_used(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(config.SECRETS_FILE_ENV, raising=False)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    _write(tmp_path / config.CREDENTIAL_NAME, f"{NAME}={VALUE}\n", 0o400)
    assert config.secrets_path() == tmp_path / config.CREDENTIAL_NAME
    assert config._secret(NAME) == VALUE


def test_check_reports_presence_without_values(secrets_file, capsys) -> None:
    _write(secrets_file, f"GRID_API_KEY={VALUE}\n")
    assert config._check() == 0
    out = capsys.readouterr().out
    assert "set    GRID_API_KEY" in out
    assert "unset  POLYMARKET_US_KEY_ID" in out
    assert VALUE not in out


def test_check_flags_an_unsafe_file(secrets_file, capsys) -> None:
    _write(secrets_file, f"GRID_API_KEY={VALUE}\n", 0o644)
    assert config._check() == 2
    assert "UNSAFE" in capsys.readouterr().out
