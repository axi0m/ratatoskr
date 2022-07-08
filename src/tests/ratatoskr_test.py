import pytest
import sys
from .. import ratatoskr

print(ratatoskr.__path__)
print(ratatoskr.verify_environment("GITHUB_TOKEN"))
token = verify_environment("GITHUB_TOKEN")


def test_verify_environment(token):
    assert isinstance(token, str)
