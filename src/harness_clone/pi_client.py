"""Idempotent Pi job admission; never mistake partial output for a candidate."""
import time

import httpx


class PiClient:
    def __init__(self,url: str,client: httpx.Client | None = None,token: str = ""):
        self.url=url.rstrip("/")
        self.client=client or httpx.Client(timeout=30)
        self.headers={"Authorization":f"Bearer {token}"}

    def submit(self,job: dict) -> dict:
        for attempt in range(2):
            try:
                reply=self.client.post(self.url+"/jobs",json=job,headers=self.headers)
                reply.raise_for_status()
                return reply.json()
            except (httpx.ReadTimeout,httpx.ConnectError):
                if attempt:
                    raise
        raise RuntimeError("unreachable")

    def status(self,job_id):
        response=self.client.get(self.url+f"/jobs/{job_id}",headers=self.headers)
        response.raise_for_status()
        return response.json()

    def wait(self,job_id: str,timeout: float = 180):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            job=self.status(job_id)
            if job["status"]=="completed":
                return job["result"]
            if job["status"] in {"failed","cancelled"}:
                raise RuntimeError(f"Pi job {job['status']}")
            time.sleep(0.2)
        self.cancel(job_id)
        raise TimeoutError("Pi editing job timed out")

    def cancel(self,job_id):
        response=self.client.post(self.url+f"/jobs/{job_id}/cancel",headers=self.headers)
        response.raise_for_status()
        return response.json()
