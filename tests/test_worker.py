import time

from harness_clone.contracts import ExperimentConfig
from harness_clone.worker import make_backend


def test_shell_command_cannot_consume_the_stage_timeout(tmp_path):
    # One runaway command (e.g. `grep -r ... /`) used to inherit the whole
    # stage timeout, so the stage was killed with no trace and no output.
    config = ExperimentConfig(timeout_seconds=600)
    backend = make_backend(config, root_dir=tmp_path)
    assert backend._default_timeout < config.timeout_seconds / 2

    small = make_backend(ExperimentConfig(timeout_seconds=10), root_dir=tmp_path)
    started = time.monotonic()
    result = small.execute("sleep 30", timeout=3600)  # agent asks for more
    assert time.monotonic() - started < 9
    assert result.exit_code == 124


def test_grep_rooted_at_filesystem_root_is_refused(tmp_path):
    backend = make_backend(ExperimentConfig(), root_dir=tmp_path)
    assert "grep path must be a directory" in backend.grep("x", "/").error
