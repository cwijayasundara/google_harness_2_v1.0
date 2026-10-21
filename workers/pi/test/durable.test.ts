import {expect,test} from "vitest";
import {mkdtemp,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {BACKGROUND_CONTEXT} from "@earendil-works/chord/context";
import {createModels} from "@earendil-works/pi-ai/models";
import {fauxProvider,fauxAssistantMessage} from "@earendil-works/pi-ai/providers/faux";
import {openEditor} from "../src/edit.js";
import {MODEL} from "../src/models.js";

test("real Durable SQLite retains admitted submission across reopen without duplicate generation",async()=>{
  const dir=await mkdtemp(join(tmpdir(),"pi-durable-"));
  const faux=fauxProvider({provider:"fireworks",models:[{id:MODEL}]});
  faux.setResponses([fauxAssistantMessage("completed")]);
  const models=createModels();models.setProvider(faux.provider);
  try {
    const first=await openEditor(join(dir,"session.sqlite"),models,dir);
    const submission=await first.root.submit({type:"input",requestId:"stable-edit",content:"Reply completed."},BACKGROUND_CONTEXT);
    const done=await submission.wait(BACKGROUND_CONTEXT);
    expect(done.status).toBe("done");
    await first.harness.close(BACKGROUND_CONTEXT);
    const second=await openEditor(join(dir,"session.sqlite"),models,dir);
    const duplicate=await second.root.submit({type:"input",requestId:"stable-edit",content:"Reply completed."},BACKGROUND_CONTEXT);
    expect(duplicate.id).toBe(submission.id);
    expect((await duplicate.wait(BACKGROUND_CONTEXT)).status).toBe("done");
    expect(faux.state.callCount).toBe(1);
    await second.harness.close(BACKGROUND_CONTEXT);
  } finally {await rm(dir,{recursive:true,force:true});}
});
