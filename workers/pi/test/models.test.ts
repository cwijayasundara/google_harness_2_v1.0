import {expect,test,vi} from "vitest";
import {MODEL,workerModels} from "../src/models.js";

test("Pi emits the bounded max_tokens field accepted by the inference gateway", async()=>{
  vi.stubEnv("HARNESS_GATEWAY_URL","http://127.0.0.1:1/v1");
  vi.stubEnv("HARNESS_GATEWAY_TOKEN","test-scoped-token");
  let payload:Record<string,unknown>|undefined;
  try {
    const models=workerModels();
    const model=models.getModel("fireworks",MODEL)!;
    await models.completeSimple(model,{
      messages:[{role:"user",content:"Reply ok",timestamp:0}],
    },{maxTokens:512,maxRetries:0,onPayload(value){
      payload=value as Record<string,unknown>;
      throw Error("Captured before network access");
    }});
    expect(payload?.max_tokens).toBe(512);
    expect(model.maxTokens).toBe(16384);
    expect(payload).not.toHaveProperty("max_completion_tokens");
    expect(payload).not.toHaveProperty("store");
  } finally {vi.unstubAllEnvs();}
});
