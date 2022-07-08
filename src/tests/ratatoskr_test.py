import pytest
from ratatoskr import verify_environment


def test_verify_environment(token):
    token = verify_environment("GITHUB_TOKEN")
    assert isinstance(token, str)
