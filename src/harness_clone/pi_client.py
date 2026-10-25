"""Idempotent Pi job admission; never mistake partial output for a candidate."""

import base64
import json
import subprocess
import time

import httpx


class DockerHTTPTransport(httpx.BaseTransport):
    """Reach the worker's loopback HTTP server through Docker's control socket.

    Internal Docker networks need no published port. Credentials and request
    bodies travel over stdin, rather than appearing in process arguments.
    """

    _REQUEST = """
const http = require('node:http');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', () => {
  const request = JSON.parse(input);
  const req = http.request({hostname: '127.0.0.1', port: 8081,
    method: request.method, path: request.path, headers: request.headers}, res => {
    const chunks = [];
    res.on('data', chunk => chunks.push(chunk));
    res.on('end', () => process.stdout.write(JSON.stringify({
      status: res.statusCode, body: Buffer.concat(chunks).toString('base64')
    })));
    res.on('error', error => {console.error(error.message); process.exit(1);});
  });
  req.setTimeout(request.timeout, () => req.destroy(new Error('worker request timed out')));
  req.on('error', error => {console.error(error.message); process.exit(1);});
  req.end(Buffer.from(request.body, 'base64'));
});
"""

    def __init__(self, container: str):
        self.container = container

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        timeout = request.extensions.get("timeout", {}).get("read") or 30
        payload = {
            "method": request.method,
            "path": request.url.raw_path.decode("ascii"),
            "headers": dict(request.headers),
            "body": base64.b64encode(request.read()).decode("ascii"),
            "timeout": max(1, int(timeout * 1000)),
        }
        try:
            result = subprocess.run(
                ["docker", "exec", "-i", self.container, "node", "-e", self._REQUEST],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=timeout + 2,
            )
        except subprocess.TimeoutExpired as exc:
            raise httpx.ReadTimeout("Pi control request timed out", request=request) from exc
        except OSError as exc:
            raise httpx.ConnectError("cannot execute Pi control request", request=request) from exc
        if result.returncode:
            raise httpx.ConnectError(
                f"Pi control request failed: {result.stderr.strip()[:300]}", request=request
            )
        try:
            response = json.loads(result.stdout)
            return httpx.Response(
                response["status"], content=base64.b64decode(response["body"], validate=True)
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise httpx.RemoteProtocolError("invalid Pi control response", request=request) from exc


class PiClient:
    def __init__(self, url: str, client: httpx.Client | None = None, token: str = ""):
        self.url = url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)
        self.headers = {"Authorization": f"Bearer {token}"}

    def submit(self, job: dict) -> dict:
        for attempt in range(2):
            try:
                reply = self.client.post(self.url + "/jobs", json=job, headers=self.headers)
                reply.raise_for_status()
                return reply.json()
            except (httpx.ReadTimeout, httpx.ConnectError):
                if attempt:
                    raise
        raise RuntimeError("unreachable")

    def status(self, job_id):
        response = self.client.get(self.url + f"/jobs/{job_id}", headers=self.headers)
        response.raise_for_status()
        return response.json()

    def wait(self, job_id: str, timeout: float = 180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.status(job_id)
            if job["status"] == "completed":
                return job["result"]
            if job["status"] in {"failed", "cancelled"}:
                detail = str(job.get("error") or "").strip()
                suffix = f": {detail[:300]}" if detail else ""
                raise RuntimeError(f"Pi job {job['status']}{suffix}")
            time.sleep(0.2)
        self.cancel(job_id)
        raise TimeoutError("Pi editing job timed out")

    def cancel(self, job_id):
        response = self.client.post(self.url + f"/jobs/{job_id}/cancel", headers=self.headers)
        response.raise_for_status()
        return response.json()
