from __future__ import annotations

from unittest.mock import patch

import pytest

import server.config as config_module


def _with_data(data: dict):
    return patch.object(config_module, "_data", data)


def test_default_kind_is_remote(tmp_path):
    with _with_data(
        {
            "repos": {
                "r": {"path": str(tmp_path), "upstream": "org/r"},
            }
        }
    ):
        repo = config_module.get_repo("r")
        assert repo.kind == "remote"
        assert repo.upstream == "org/r"


def test_local_kind_without_upstream(tmp_path):
    with _with_data(
        {
            "repos": {
                "r": {"path": str(tmp_path), "kind": "local"},
            }
        }
    ):
        repo = config_module.get_repo("r")
        assert repo.kind == "local"
        assert repo.upstream is None
        assert config_module.is_local("r")


def test_local_with_upstream_rejected(tmp_path):
    with _with_data(
        {
            "repos": {
                "r": {"path": str(tmp_path), "kind": "local", "upstream": "org/r"},
            }
        }
    ):
        with pytest.raises(ValueError, match="must not have upstream"):
            config_module.get_repos()


def test_remote_without_upstream_rejected(tmp_path):
    with _with_data(
        {
            "repos": {
                "r": {"path": str(tmp_path)},
            }
        }
    ):
        with pytest.raises(ValueError, match="requires upstream"):
            config_module.get_repos()


def test_invalid_kind_rejected(tmp_path):
    with _with_data(
        {
            "repos": {
                "r": {"path": str(tmp_path), "kind": "weird", "upstream": "org/r"},
            }
        }
    ):
        with pytest.raises(ValueError, match="must be 'remote' or 'local'"):
            config_module.get_repos()


def test_require_upstream_for_local_raises(tmp_path):
    with _with_data(
        {
            "repos": {
                "r": {"path": str(tmp_path), "kind": "local"},
            }
        }
    ):
        with pytest.raises(ValueError, match="local-only"):
            config_module.require_upstream("r")
