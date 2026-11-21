"""Published environment artifacts must not contain machine-local paths."""
import pytest
from measurement_environment import normalize_freeze


def test_local_file_requirement_is_pinned_without_machine_path():
    raw = 'numpy==2.1.0\npytest-testpilot @ file:///Users/someone/project/python/pytest-testpilot\n'
    assert normalize_freeze(raw, {'pytest-testpilot': '0.1.0'}) == 'numpy==2.1.0\npytest-testpilot==0.1.0\n'


def test_machine_path_is_rejected_instead_of_published():
    with pytest.raises(ValueError, match='machine-local path'):
        normalize_freeze('-e /home/someone/project\n', {})


def test_remote_pinned_dependency_is_preserved():
    raw = '-e git+https://github.com/bocchisad/TestPilot.git@abc#egg=testpilot_train\n'
    assert normalize_freeze(raw, {}) == raw
