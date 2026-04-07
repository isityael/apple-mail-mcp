"""Tests for per-account IMAP configuration."""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from apple_mail_mcp import imap


def _write_config(data: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


def test_legacy_single_account_config():
    """Legacy format: root-level host/port/user/password."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = os.path.join(tmpdir, "imap.json")
        _write_config({"host": "127.0.0.1", "port": 1143, "user": "me@proton.me", "password": "secret"}, cfg_path)

        old_cfg = imap.CONFIG_FILE
        imap.CONFIG_FILE = cfg_path
        try:
            config = imap.get_imap_config()
            assert config["user"] == "me@proton.me"
            assert config["port"] == 1143
        finally:
            imap.CONFIG_FILE = old_cfg


def test_multi_account_config():
    """Multi-account format: accounts dict keyed by name."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = os.path.join(tmpdir, "imap.json")
        _write_config(
            {
                "accounts": {
                    "proton": {"host": "127.0.0.1", "port": 1143, "user": "me@proton.me", "password": "secret"},
                    "iCloud": {"host": "imap.mail.me.com", "port": 993, "user": "me@icloud.com", "password": "app-pw"},
                }
            },
            cfg_path,
        )

        old_cfg = imap.CONFIG_FILE
        imap.CONFIG_FILE = cfg_path
        try:
            # get_account_config by exact name
            cfg = imap.get_account_config("proton")
            assert cfg is not None
            assert cfg["user"] == "me@proton.me"

            cfg2 = imap.get_account_config("iCloud")
            assert cfg2 is not None
            assert cfg2["user"] == "me@icloud.com"

            # Case-insensitive match
            cfg3 = imap.get_account_config("ICLOUD")
            assert cfg3 is not None
            assert cfg3["user"] == "me@icloud.com"

            # Non-existent account
            assert imap.get_account_config("Gmail") is None
            assert imap.has_imap_config("proton") is True
            assert imap.has_imap_config("Gmail") is False
        finally:
            imap.CONFIG_FILE = old_cfg


def test_legacy_get_imap_config_uses_first_account():
    """Legacy get_imap_config() uses first account from multi-account format."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = os.path.join(tmpdir, "imap.json")
        _write_config(
            {
                "accounts": {
                    "first": {"host": "h1", "port": 100, "user": "u1", "password": "p1"},
                    "second": {"host": "h2", "port": 200, "user": "u2", "password": "p2"},
                }
            },
            cfg_path,
        )

        old_cfg = imap.CONFIG_FILE
        imap.CONFIG_FILE = cfg_path
        try:
            config = imap.get_imap_config()
            assert config["user"] == "u1"
            assert config["host"] == "h1"
        finally:
            imap.CONFIG_FILE = old_cfg


def test_missing_config_file():
    """No config file returns None for account lookup."""
    old_cfg = imap.CONFIG_FILE
    imap.CONFIG_FILE = "/nonexistent/path/imap.json"
    try:
        assert imap.get_account_config("anything") is None
        assert imap.has_imap_config("anything") is False
    finally:
        imap.CONFIG_FILE = old_cfg


def test_legacy_user_match():
    """Legacy single-account config matches by user field."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = os.path.join(tmpdir, "imap.json")
        _write_config({"host": "127.0.0.1", "port": 1143, "user": "me@proton.me", "password": "pw"}, cfg_path)

        old_cfg = imap.CONFIG_FILE
        imap.CONFIG_FILE = cfg_path
        try:
            cfg = imap.get_account_config("me@proton.me")
            assert cfg is not None
            assert cfg["user"] == "me@proton.me"

            # Non-matching user returns None
            assert imap.get_account_config("other@gmail.com") is None
        finally:
            imap.CONFIG_FILE = old_cfg
