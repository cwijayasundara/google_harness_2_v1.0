import os
import subprocess

import pytest


@pytest.mark.docker
def test_container_cannot_write_bundle_or_reach_public_network():
    if os.environ.get("HARNESS_TEST_DOCKER") != "1":
        pytest.skip("set HARNESS_TEST_DOCKER=1 after building the executor image")
    # This is a real container test, not a simulated boundary assertion.
    result=subprocess.run(["docker","run","--rm","--network=none","--read-only","--user=65532:65532","harness-clone-executor:0.1","python","-c","import socket; from pathlib import Path; denied=False\ntry: Path('/app/escape').write_text('bad')\nexcept OSError: denied=True\nassert denied\ntry: socket.create_connection(('1.1.1.1',443),timeout=1); raise AssertionError('egress allowed')\nexcept OSError: pass"],capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stderr
