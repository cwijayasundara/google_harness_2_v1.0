from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ScriptedModel(BaseChatModel):
    replies: list[AIMessage] = Field(default_factory=lambda:[AIMessage(content="done")])
    seen: list = Field(default_factory=list)
    bound: list = Field(default_factory=list)
    model_name: str = "accounts/fireworks/models/deepseek-v4p1-flash"

    @property
    def _llm_type(self):
        return "fireworks-chat"

    def bind_tools(self, tools, **kwargs):
        self.bound.append(tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        reply=self.replies[min(len(self.seen)-1,len(self.replies)-1)]
        return ChatResult(generations=[ChatGeneration(message=reply)])
