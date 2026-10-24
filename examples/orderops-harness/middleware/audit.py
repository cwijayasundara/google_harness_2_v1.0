"""Reserved for evidence-supported audit improvements."""

from langchain.agents.middleware import AgentMiddleware


def create_middleware(config):
    return AgentMiddleware()
