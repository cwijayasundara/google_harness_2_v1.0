import {createModels,createProvider,envApiKeyAuth,type Model} from "@earendil-works/pi-ai";
import {openAICompletionsApi} from "@earendil-works/pi-ai/api/openai-completions.lazy";

export const MODEL="accounts/fireworks/models/deepseek-v4p1-flash";
export function workerModels(){
  const url=process.env.HARNESS_GATEWAY_URL;
  const token=process.env.HARNESS_GATEWAY_TOKEN;
  if(!url || !token) throw Error("Pi worker requires the credential gateway");
  const model:Model<"openai-completions">={id:MODEL,name:"DeepSeek V4.1 Flash",api:"openai-completions",provider:"fireworks",baseUrl:url,reasoning:true,input:["text"],cost:{input:0.30,output:1.20,cacheRead:0.006,cacheWrite:0.30},contextWindow:131072,maxTokens:4096,compat:{supportsDeveloperRole:false,supportsStore:false}};
  const models=createModels();
  models.setProvider(createProvider({id:"fireworks",name:"Harness Fireworks Gateway",baseUrl:url,auth:{apiKey:envApiKeyAuth("Scoped gateway token",["HARNESS_GATEWAY_TOKEN"])},models:[model],api:openAICompletionsApi()}));
  return models;
}
