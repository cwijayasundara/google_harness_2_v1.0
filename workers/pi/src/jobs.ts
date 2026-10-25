import { DatabaseSync } from "node:sqlite";
import { createHash, randomUUID } from "node:crypto";

export interface EditJob {
  request_id:string; parent_digest:string; evidence_digest:string;
  scope:string; allowed_paths:string[]; budget_usd:string; routing_policy:string;
}
export interface Job {id:string; input:EditJob; status:string; result?:unknown; error?:string; submission_id?:string}
function canonical(value:unknown):string {
  if(Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if(value!==null && typeof value==="object") return `{${Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>`${JSON.stringify(k)}:${canonical(v)}`).join(",")}}`;
  return JSON.stringify(value);
}
export function editFailure(error: unknown): string {
  const message = error instanceof Error && error.message ? error.message : "edit failure";
  return message.replace(/\s+/g, " ").slice(0, 300);
}

export class JobStore {
  private db:DatabaseSync;
  constructor(path:string){this.db=new DatabaseSync(path);this.db.exec("PRAGMA busy_timeout=5000; CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL, value TEXT NOT NULL)");}
  admit(input:EditJob):Job {
    if(!input.request_id || !/^[a-f0-9]{64}$/.test(input.parent_digest) || !/^[a-f0-9]{64}$/.test(input.evidence_digest) || !["INV","DOM","COMPOSE"].includes(input.scope) || !Array.isArray(input.allowed_paths) || !(Number(input.budget_usd)>0)) throw Error("invalid edit job");
    const fingerprint=createHash("sha256").update(canonical(input)).digest("hex");
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const existing=this.db.prepare("SELECT fingerprint,value FROM jobs WHERE request_id=?").get(input.request_id) as {fingerprint:string;value:string}|undefined;
      if(existing){if(existing.fingerprint!==fingerprint) throw Error("request identity conflict");this.db.exec("COMMIT");return JSON.parse(existing.value);}
      const job:Job={id:randomUUID(),input,status:"pending"};
      this.db.prepare("INSERT INTO jobs VALUES(?,?,?,?)").run(job.id,input.request_id,fingerprint,JSON.stringify(job));
      this.db.exec("COMMIT");return job;
    }catch(error){this.db.exec("ROLLBACK");throw error;}
  }
  get(id:string):Job {const row=this.db.prepare("SELECT value FROM jobs WHERE id=?").get(id) as {value:string}|undefined;if(!row) throw Error("unknown job");return JSON.parse(row.value);}
  pending():Job[]{return (this.db.prepare("SELECT value FROM jobs").all() as {value:string}[]).map(r=>JSON.parse(r.value) as Job).filter(j=>["pending","running"].includes(j.status));}
  private save(job:Job){this.db.prepare("UPDATE jobs SET value=? WHERE id=?").run(JSON.stringify(job),job.id);}
  running(id:string,submission_id?:string){const job=this.get(id);if(!["pending","running"].includes(job.status)) throw Error("terminal job");this.save({...job,status:"running",submission_id:submission_id??job.submission_id});}
  complete(id:string,result:unknown){const job=this.get(id);if(job.status==="completed" && canonical(job.result)===canonical(result)) return;if(!["pending","running"].includes(job.status)) throw Error("terminal job");this.save({...job,status:"completed",result});}
  fail(id:string,error:string){const job=this.get(id);if(["pending","running"].includes(job.status))this.save({...job,status:"failed",error});}
  cancel(id:string){const job=this.get(id);if(["pending","running"].includes(job.status))this.save({...job,status:"cancelled"});}
  close(){this.db.close();}
}
