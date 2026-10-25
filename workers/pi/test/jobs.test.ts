import { afterEach, expect, test } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dirs: string[]=[];
afterEach(async()=>{for(const dir of dirs.splice(0)) await rm(dir,{recursive:true,force:true});});

test("duplicate requests reacquire one job and changed payloads are rejected",async()=>{
  const {JobStore}=await import("../src/jobs.js");
  const dir=await mkdtemp(join(tmpdir(),"pi-jobs-"));dirs.push(dir);
  const jobs=new JobStore(join(dir,"jobs.sqlite"));
  const input={request_id:"one",parent_digest:"a".repeat(64),evidence_digest:"b".repeat(64),scope:"INV",allowed_paths:["scripts/check.py"],budget_usd:"0.2",routing_policy:"v1"};
  const first=jobs.admit(input);
  expect(jobs.admit(input).id).toBe(first.id);
  expect(()=>jobs.admit({...input,scope:"DOM"})).toThrow(/conflict/);
  jobs.close();
  const reopened=new JobStore(join(dir,"jobs.sqlite"));
  expect(reopened.admit(input).id).toBe(first.id);
  expect(reopened.get(first.id).status).toBe("pending");
  reopened.close();
});

test("edit failures keep the message instead of the generic error name", async()=>{
  const {editFailure}=await import("../src/jobs.js");
  expect(editFailure(new Error("ENOENT: proposal.json"))).toBe("ENOENT: proposal.json");
  expect(editFailure(new Error("x".repeat(500)))).toHaveLength(300);
});

test("committed result is immutable and cancellation is terminal",async()=>{
  const {JobStore}=await import("../src/jobs.js");
  const dir=await mkdtemp(join(tmpdir(),"pi-jobs-"));dirs.push(dir);
  const jobs=new JobStore(join(dir,"jobs.sqlite"));
  const job=jobs.admit({request_id:"two",parent_digest:"a".repeat(64),evidence_digest:"b".repeat(64),scope:"COMPOSE",allowed_paths:[],budget_usd:"0.2",routing_policy:"v1"});
  jobs.cancel(job.id);
  expect(jobs.get(job.id).status).toBe("cancelled");
  expect(()=>jobs.complete(job.id,{diff:{}})).toThrow(/terminal/);
  jobs.close();
});
