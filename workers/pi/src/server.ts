import {createServer} from "node:http";
import {timingSafeEqual} from "node:crypto";
import {mkdir,readFile} from "node:fs/promises";
import {editFailure,JobStore,type EditJob,type Job} from "./jobs.js";
import {executeEdit} from "./edit.js";

await mkdir("/data",{recursive:true});
const jobs=new JobStore("/data/jobs.sqlite");
const admitted=JSON.parse(await readFile("/context/job.json","utf8")) as EditJob;
const token=process.env.HARNESS_WORKER_TOKEN;
if(!token) throw Error("worker token required");
let active:string|undefined;
let abort:(()=>Promise<void>)|undefined;
async function run(job:Job){
  if(active || !["pending","running"].includes(job.status)) return;
  active=job.id;
  try {const result=await executeEdit(job,jobs,handler=>{abort=handler;});if(jobs.get(job.id).status!=="cancelled")jobs.complete(job.id,result);}
  catch(error){jobs.fail(job.id,editFailure(error));}
  finally{active=undefined;abort=undefined;}
}
for(const job of jobs.pending()) void run(job);
const server=createServer(async(req,res)=>{
  try {
    const supplied=req.headers.authorization?.replace(/^Bearer /,"")??"";
    if(supplied.length!==token.length || !timingSafeEqual(Buffer.from(supplied),Buffer.from(token))) throw Error("unauthorized");
    const url=new URL(req.url??"/","http://worker");
    const parts=url.pathname.split("/").filter(Boolean);
    let result:unknown;
    if(req.method==="POST" && url.pathname==="/jobs"){
      let body="";for await(const chunk of req){body+=chunk;if(body.length>1000000)throw Error("body too large");}
      const input=JSON.parse(body) as EditJob;
      if(JSON.stringify(input)!==JSON.stringify(admitted))throw Error("job differs from immutable context");
      const job=jobs.admit(input);result=job;void run(job);
    }else if(parts[0]==="jobs" && parts[1]){
      const job=jobs.get(parts[1]);
      if(req.method==="POST" && parts[2]==="cancel"){jobs.cancel(job.id);await abort?.();result=jobs.get(job.id);}
      else if(req.method==="GET" && parts[2]==="result"){if(job.status!=="completed")throw Error("result unavailable");result=job.result;}
      else if(req.method==="GET" && parts.length===2)result=job;
      else throw Error("unsupported route");
    }else throw Error("unsupported route");
    res.writeHead(200,{"content-type":"application/json"});res.end(JSON.stringify(result));
  }catch(error){res.writeHead(400,{"content-type":"application/json"});res.end(JSON.stringify({error:error instanceof Error?error.message:"request failed"}));}
});
server.listen(8081,"0.0.0.0");
process.on("SIGTERM",async()=>{await abort?.();server.close();jobs.close();process.exit(0);});
