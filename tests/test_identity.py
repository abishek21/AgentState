import pytest

from agentstate.identity import normalize_remote_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/acme/repo.git", "github.com/acme/repo"),
        ("https://github.com/acme/repo", "github.com/acme/repo"),
        ("git@github.com:acme/repo.git", "github.com/acme/repo"),
        ("ssh://git@github.com/acme/repo.git", "github.com/acme/repo"),
        ("https://oauth2:token123@github.com/acme/repo.git", "github.com/acme/repo"),
        ("https://GitHub.com/Acme/Repo.git", "github.com/Acme/Repo"),
    ],
)
def test_normalize_remote_url(url, expected):
    assert normalize_remote_url(url) == expected


def test_different_protocols_same_repo_same_fingerprint():
    https_fp = normalize_remote_url("https://github.com/acme/repo.git")
    ssh_fp = normalize_remote_url("git@github.com:acme/repo.git")
    assert https_fp == ssh_fp
