<!-- source: https://docs.langchain.com/oss/python/langchain/overview.md | section: /oss/python/langchain | fetched: 2026-09-09 -->

# LangChain overview

> LangChain provides create_agent: a minimal, highly configurable agent harness. Compose exactly the agent your use case needs from model, tools, prompt, and middleware.

**Agent = Model + Harness.** LangChain provides `create_agent`: a minimal, highly configurable harness. The harness is everything around the model loop: the prompt, the tools, and any middleware that shapes behavior. Start with the primitives and compose exactly what your use case needs. Supports [OpenAI, Anthropic, Google, and more](/oss/python/integrations/providers/overview).

  **LangChain vs. LangGraph vs. Deep Agents**

  Start with [Deep Agents](/oss/python/deepagents/overview/) for a "batteries-included" agent with features like automatic context compression, a virtual filesystem, and subagent-spawning. Deep Agents are built on LangChain [agents](/oss/python/langchain/agents/) which you can also use directly.

  Use [LangChain](/oss/python/langchain/agents) (`create_agent`) for a highly customizable harness, easily tailored to your use case and data.

  Use [LangGraph](/oss/python/langgraph/overview), our low-level orchestration framework, for advanced needs combining deterministic and agentic workflows.

  Use [LangSmith](/langsmith/observability) to trace, debug, and evaluate agents built with any of these frameworks. Follow the [tracing quickstart](/langsmith/trace-with-langchain) to get set up. We recommend you also set up [LangSmith Engine](/langsmith/engine) which monitors your traces, detects issues, and proposes fixes.

##  Create an agent

This example demonstrates how to create a simple LangChain agent with a custom tool:

  ```python
  # pip install -qU langchain "langchain[openai]"
  from langchain.agents import create_agent

  def get_weather(city: str) -> str:
      """Get weather for a given city."""
      return f"It's always sunny in {city}!"

  agent = create_agent(
      model="openai:gpt-5.5",
      tools=[get_weather],
      system_prompt="You are a helpful assistant",
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]}
  )
  print(result["messages"][-1].content_blocks)
  ```

  ```python
  # pip install -qU langchain "langchain[google-genai]"
  from langchain.agents import create_agent

  def get_weather(city: str) -> str:
      """Get weather for a given city."""
      return f"It's always sunny in {city}!"

  agent = create_agent(
      model="google_genai:gemini-2.5-flash-lite",
      tools=[get_weather],
      system_prompt="You are a helpful assistant",
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]}
  )
  print(result["messages"][-1].content_blocks)
  ```

  ```python
  # pip install -qU langchain "langchain[anthropic]"
  from langchain.agents import create_agent

  def get_weather(city: str) -> str:
      """Get weather for a given city."""
      return f"It's always sunny in {city}!"

  agent = create_agent(
      model="claude-sonnet-4-6",
      tools=[get_weather],
      system_prompt="You are a helpful assistant",
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]}
  )
  print(result["messages"][-1].content_blocks)
  ```

  ```python
  # pip install -qU langchain langchain-openrouter
  from langchain.agents import create_agent

  def get_weather(city: str) -> str:
      """Get weather for a given city."""
      return f"It's always sunny in {city}!"

  agent = create_agent(
      model="openrouter:anthropic/claude-sonnet-4-6",
      tools=[get_weather],
      system_prompt="You are a helpful assistant",
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]}
  )
  print(result["messages"][-1].content_blocks)
  ```

  ```python
  # pip install -qU langchain langchain-fireworks
  from langchain.agents import create_agent

  def get_weather(city: str) -> str:
      """Get weather for a given city."""
      return f"It's always sunny in {city}!"

  agent = create_agent(
      model="fireworks:accounts/fireworks/models/qwen3p5-397b-a17b",
      tools=[get_weather],
      system_prompt="You are a helpful assistant",
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]}
  )
  print(result["messages"][-1].content_blocks)
  ```

  ```python
  # pip install -qU langchain langchain-baseten
  from langchain.agents import create_agent

  def get_weather(city: str) -> str:
      """Get weather for a given city."""
      return f"It's always sunny in {city}!"

  agent = create_agent(
      model="baseten:zai-org/GLM-5.2",
      tools=[get_weather],
      system_prompt="You are a helpful assistant",
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]}
  )
  print(result["messages"][-1].content_blocks)
  ```

  ```python
  # pip install -qU langchain langchain-ollama
  from langchain.agents import create_agent

  def get_weather(city: str) -> str:
      """Get weather for a given city."""
      return f"It's always sunny in {city}!"

  agent = create_agent(
      model="ollama:devstral-2",
      tools=[get_weather],
      system_prompt="You are a helpful assistant",
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]}
  )
  print(result["messages"][-1].content_blocks)
  ```

  ```python
  # pip install -qU langchain "langchain[openai]"
  import os
  from langchain.agents import create_agent
  from langchain.chat_models import init_chat_model

  def get_weather(city: str) -> str:
      """Get weather for a given city."""
      return f"It's always sunny in {city}!"

  model = init_chat_model(
      "azure_openai:gpt-5.5",
      azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
  )
  agent = create_agent(
      model=model,
      tools=[get_weather],
      system_prompt="You are a helpful assistant",
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]}
  )
  print(result["messages"][-1].content_blocks)
  ```

  ```python
  # pip install -qU langchain langchain-aws
  from langchain.agents import create_agent

  def get_weather(city: str) -> str:
      """Get weather for a given city."""
      return f"It's always sunny in {city}!"

  # US cross-region inference profile; use global.anthropic.claude-sonnet-4-6 for worldwide routing.
  agent = create_agent(
      model="bedrock_converse:us.anthropic.claude-sonnet-4-6",
      tools=[get_weather],
      system_prompt="You are a helpful assistant",
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]}
  )
  print(result["messages"][-1].content_blocks)
  ```

  ```python
  # pip install -qU langchain "langchain[huggingface]"
  from langchain.agents import create_agent

  def get_weather(city: str) -> str:
      """Get weather for a given city."""
      return f"It's always sunny in {city}!"

  agent = create_agent(
      model="huggingface:microsoft/Phi-3-mini-4k-instruct",
      tools=[get_weather],
      system_prompt="You are a helpful assistant",
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]}
  )
  print(result["messages"][-1].content_blocks)
  ```

See the [Installation instructions](/oss/python/langchain/install) and [Quickstart guide](/oss/python/langchain/quickstart) to get started building your own agents and applications with LangChain.

  Use [LangSmith](/langsmith/observability) to trace requests, debug agent behavior, and evaluate outputs. Set `LANGSMITH_TRACING=true` and your API key to get started.

##  Core benefits

  - [Standard model interface](/oss/python/langchain/models)
    Use one interface for chat models, embeddings, and more across providers. Switch models with minimal code changes and keep your application portable as requirements evolve.

  - [Highly configurable harness](/oss/python/langchain/agents)
    Start with `create_agent` as a minimal harness and add capabilities incrementally through middleware. Compose only what your use case needs, from guardrails and retries to routing and custom tool policies.

  - [Built on top of LangGraph](/oss/python/langgraph/overview)
    LangChain's agents are built on top of LangGraph. This allows us to take advantage of LangGraph's durable execution, human-in-the-loop support, persistence, and more.

  - [Debug with LangSmith](/langsmith/observability)
    Inspect traces, tool calls, state transitions, and latency in one place. Find failure modes, evaluate quality, and improve agent behavior with execution data.
