"""Pi control traffic must work without a published port or worker egress."""

import os
import subprocess
import time
import uuid

import httpx
import pytest


@pytest.mark.docker
def test_pi_control_http_over_docker_exec_on_internal_network():
    if os.environ.get("HARNESS_TEST_DOCKER") != "1":
        pytest.skip("requires the built Pi image and Docker")
    from harness_clone.pi_client import DockerHTTPTransport

    name = f"hc-pi-transport-test-{uuid.uuid4().hex[:12]}"

    def docker(*args):
        return subprocess.run(["docker", *args], capture_output=True, text=True,
                              timeout=30, check=True)

    code = """
const http = require('node:http');
http.createServer(async (req, res) => {
  let body = ''; for await (const chunk of req) body += chunk;
  res.writeHead(req.headers.authorization === 'Bearer scoped-token' ? 200 : 403,
                {'content-type': 'application/json'});
  res.end(JSON.stringify({method: req.method, path: req.url,
                          body: body ? JSON.parse(body) : null}));
}).listen(8081, '0.0.0.0');
"""
    docker("network", "create", "--internal", name)
    try:
        docker("run", "-d", "--name", name, "--network", name,
               "--read-only", "--cap-drop=ALL", "--user=65532:65532",
               "harness-clone-pi:0.1", "node", "-e", code)
        with httpx.Client(transport=DockerHTTPTransport(name), timeout=5) as client:
            # `docker run -d` returns before node binds :8081; retry like PiEditor does.
            deadline = time.monotonic() + 10
            while True:
                try:
                    response = client.post("http://pi/jobs", json={"request_id": "stable"},
                                           headers={"Authorization": "Bearer scoped-token"})
                    break
                except httpx.TransportError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.2)
            assert response.status_code == 200
            assert response.json() == {
                "method": "POST", "path": "/jobs", "body": {"request_id": "stable"},
            }
            response = client.get("http://pi/jobs/job-1?detail=1",
                                  headers={"Authorization": "Bearer scoped-token"})
            assert response.json() == {
                "method": "GET", "path": "/jobs/job-1?detail=1", "body": None,
            }
            assert client.get("http://pi/jobs/job-1").status_code == 403
        assert docker("port", name).stdout.strip() == ""
        probe = """
const net=require('node:net');
const socket=net.connect({host:'1.1.1.1',port:443});
socket.on('connect',()=>process.exit(1));
socket.on('error',()=>process.exit(0));
socket.setTimeout(1000,()=>process.exit(0));
"""
        docker("exec", name, "node", "-e", probe)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        subprocess.run(["docker", "network", "rm", name], capture_output=True, timeout=30)
