<!-- source: https://docs.langchain.com/oss/python/langchain/agents.md | section: /oss/python/langchain | fetched: 2026-09-09 -->

# Agents

An agent is a model calling tools in a loop until a given task is complete.

A harness is everything around that loop: the prompt, the tools, and any middleware that shapes the model's behavior.

  **Agent = Model + Harness**

  The job of a harness: get the model the right context at the right time for the given task.

[`create_agent`](https://reference.langchain.com/python/langchain/agents/factory/create_agent) is a highly configurable harness. At its simplest, you can create one with:

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="google_genai:gemini-3.6-flash", tools=tools)
  ```

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="openai:gpt-5.5", tools=tools)
  ```

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="anthropic:claude-sonnet-4-6", tools=tools)
  ```

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="openrouter:z-ai/glm-5.2", tools=tools)
  ```

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="fireworks:accounts/fireworks/models/glm-5p2", tools=tools)
  ```

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="baseten:zai-org/GLM-5.2", tools=tools)
  ```

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="ollama:north-mini-code-1.0", tools=tools)
  ```

Building on that, you can configure the basics directly with the `model=`, `tools=`, and `system_prompt=` parameters. For more advanced capabilities, extend the harness with [middleware](#configure-the-harness).

  [Deep Agents](/oss/python/deepagents/overview) builds on `create_agent` and comes with commonly useful capabilities already assembled, such as planning, file system tools, subagents, and memory. Use `create_agent` when you need to configure the harness yourself.

## Core components

### Model

Pass a model identifier string (`"provider:model"`) or an initialized model instance to select the model for your agent. See [Models](/oss/python/langchain/models) for parameters, provider setup, and dynamic model selection.

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="google_genai:gemini-3.6-flash", tools=tools)
  ```

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="openai:gpt-5.5", tools=tools)
  ```

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="anthropic:claude-sonnet-4-6", tools=tools)
  ```

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="openrouter:z-ai/glm-5.2", tools=tools)
  ```

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="fireworks:accounts/fireworks/models/glm-5p2", tools=tools)
  ```

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="baseten:zai-org/GLM-5.2", tools=tools)
  ```

  ```python
  from langchain.agents import create_agent

  agent = create_agent(model="ollama:north-mini-code-1.0", tools=tools)
  ```

### Tools

To provide the agent with tools, pass any Python callable, LangChain tool, or tool dict. See [Tools](/oss/python/langchain/tools) for tool definition, context access, and dynamic tool selection.

  ```python
  from langchain.agents import create_agent
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for information."""
      return f"Results for: {query}"

  agent = create_agent(model="google_genai:gemini-3.6-flash", tools=[search])
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for information."""
      return f"Results for: {query}"

  agent = create_agent(model="openai:gpt-5.5", tools=[search])
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for information."""
      return f"Results for: {query}"

  agent = create_agent(model="anthropic:claude-sonnet-4-6", tools=[search])
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for information."""
      return f"Results for: {query}"

  agent = create_agent(model="openrouter:z-ai/glm-5.2", tools=[search])
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for information."""
      return f"Results for: {query}"

  agent = create_agent(model="fireworks:accounts/fireworks/models/glm-5p2", tools=[search])
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for information."""
      return f"Results for: {query}"

  agent = create_agent(model="baseten:zai-org/GLM-5.2", tools=[search])
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for information."""
      return f"Results for: {query}"

  agent = create_agent(model="ollama:north-mini-code-1.0", tools=[search])
  ```

### System prompt

Shape how the agent approaches tasks. The system prompt parameter accepts a string or `SystemMessage`. For dynamic prompts at runtime, use [middleware](/oss/python/langchain/middleware).

  ```python
  agent = create_agent(
      model="google_genai:gemini-3.6-flash",
      tools=tools,
      system_prompt="You are a helpful assistant. Be concise and accurate.",
  )
  ```

  ```python
  agent = create_agent(
      model="openai:gpt-5.5",
      tools=tools,
      system_prompt="You are a helpful assistant. Be concise and accurate.",
  )
  ```

  ```python
  agent = create_agent(
      model="anthropic:claude-sonnet-4-6",
      tools=tools,
      system_prompt="You are a helpful assistant. Be concise and accurate.",
  )
  ```

  ```python
  agent = create_agent(
      model="openrouter:z-ai/glm-5.2",
      tools=tools,
      system_prompt="You are a helpful assistant. Be concise and accurate.",
  )
  ```

  ```python
  agent = create_agent(
      model="fireworks:accounts/fireworks/models/glm-5p2",
      tools=tools,
      system_prompt="You are a helpful assistant. Be concise and accurate.",
  )
  ```

  ```python
  agent = create_agent(
      model="baseten:zai-org/GLM-5.2",
      tools=tools,
      system_prompt="You are a helpful assistant. Be concise and accurate.",
  )
  ```

  ```python
  agent = create_agent(
      model="ollama:north-mini-code-1.0",
      tools=tools,
      system_prompt="You are a helpful assistant. Be concise and accurate.",
  )
  ```

### Structured output

Return a validated schema from the agent using `response_format=`. See [Structured output](/oss/python/langchain/structured-output) for strategies and examples.

  ```python
  from pydantic import BaseModel
  from langchain.agents import create_agent

  class Answer(BaseModel):
      summary: str
      confidence: float

  agent = create_agent(model="google_genai:gemini-3.6-flash", tools=tools, response_format=Answer)
  result = agent.invoke({"messages": [{"role": "user", "content": "Summarize AI trends"}]})
  result["structured_response"]  # Answer(summary=..., confidence=...)
  ```

  ```python
  from pydantic import BaseModel
  from langchain.agents import create_agent

  class Answer(BaseModel):
      summary: str
      confidence: float

  agent = create_agent(model="openai:gpt-5.5", tools=tools, response_format=Answer)
  result = agent.invoke({"messages": [{"role": "user", "content": "Summarize AI trends"}]})
  result["structured_response"]  # Answer(summary=..., confidence=...)
  ```

  ```python
  from pydantic import BaseModel
  from langchain.agents import create_agent

  class Answer(BaseModel):
      summary: str
      confidence: float

  agent = create_agent(model="anthropic:claude-sonnet-4-6", tools=tools, response_format=Answer)
  result = agent.invoke({"messages": [{"role": "user", "content": "Summarize AI trends"}]})
  result["structured_response"]  # Answer(summary=..., confidence=...)
  ```

  ```python
  from pydantic import BaseModel
  from langchain.agents import create_agent

  class Answer(BaseModel):
      summary: str
      confidence: float

  agent = create_agent(model="openrouter:z-ai/glm-5.2", tools=tools, response_format=Answer)
  result = agent.invoke({"messages": [{"role": "user", "content": "Summarize AI trends"}]})
  result["structured_response"]  # Answer(summary=..., confidence=...)
  ```

  ```python
  from pydantic import BaseModel
  from langchain.agents import create_agent

  class Answer(BaseModel):
      summary: str
      confidence: float

  agent = create_agent(model="fireworks:accounts/fireworks/models/glm-5p2", tools=tools, response_format=Answer)
  result = agent.invoke({"messages": [{"role": "user", "content": "Summarize AI trends"}]})
  result["structured_response"]  # Answer(summary=..., confidence=...)
  ```

  ```python
  from pydantic import BaseModel
  from langchain.agents import create_agent

  class Answer(BaseModel):
      summary: str
      confidence: float

  agent = create_agent(model="baseten:zai-org/GLM-5.2", tools=tools, response_format=Answer)
  result = agent.invoke({"messages": [{"role": "user", "content": "Summarize AI trends"}]})
  result["structured_response"]  # Answer(summary=..., confidence=...)
  ```

  ```python
  from pydantic import BaseModel
  from langchain.agents import create_agent

  class Answer(BaseModel):
      summary: str
      confidence: float

  agent = create_agent(model="ollama:north-mini-code-1.0", tools=tools, response_format=Answer)
  result = agent.invoke({"messages": [{"role": "user", "content": "Summarize AI trends"}]})
  result["structured_response"]  # Answer(summary=..., confidence=...)
  ```

### Agent state

Every agent manages its execution context through [`AgentState`](https://reference.langchain.com/python/langchain/agents/middleware/types/AgentState), a typed dictionary that holds the current conversation history and any custom fields your tools and middleware need.

The built-in field is:

| Field      | Type                | Description                                                                                                |
| ---------- | ------------------- | ---------------------------------------------------------------------------------------------------------- |
| `messages` | `list[BaseMessage]` | The full conversation history for the current thread. Append-only: new messages are added, never replaced. |

`AgentState` is also the type signature for every node-style middleware hook (`before_model`, `after_model`, and similar). Hooks receive the current state and can return a dict of updates to merge back into it.

To add custom fields (for example, a `user_id` or a counter), subclass `AgentState` and pass the subclass to `create_agent` via `state_schema=`:

  ```python
  from langchain.agents import AgentState, create_agent

  class MyState(AgentState):
      user_id: str
      call_count: int

  agent = create_agent(
      model="google_genai:gemini-3.6-flash",
      tools=[],
      state_schema=MyState,  # [!code highlight]
  )
  ```

  ```python
  from langchain.agents import AgentState, create_agent

  class MyState(AgentState):
      user_id: str
      call_count: int

  agent = create_agent(
      model="openai:gpt-5.5",
      tools=[],
      state_schema=MyState,  # [!code highlight]
  )
  ```

  ```python
  from langchain.agents import AgentState, create_agent

  class MyState(AgentState):
      user_id: str
      call_count: int

  agent = create_agent(
      model="anthropic:claude-sonnet-4-6",
      tools=[],
      state_schema=MyState,  # [!code highlight]
  )
  ```

  ```python
  from langchain.agents import AgentState, create_agent

  class MyState(AgentState):
      user_id: str
      call_count: int

  agent = create_agent(
      model="openrouter:z-ai/glm-5.2",
      tools=[],
      state_schema=MyState,  # [!code highlight]
  )
  ```

  ```python
  from langchain.agents import AgentState, create_agent

  class MyState(AgentState):
      user_id: str
      call_count: int

  agent = create_agent(
      model="fireworks:accounts/fireworks/models/glm-5p2",
      tools=[],
      state_schema=MyState,  # [!code highlight]
  )
  ```

  ```python
  from langchain.agents import AgentState, create_agent

  class MyState(AgentState):
      user_id: str
      call_count: int

  agent = create_agent(
      model="baseten:zai-org/GLM-5.2",
      tools=[],
      state_schema=MyState,  # [!code highlight]
  )
  ```

  ```python
  from langchain.agents import AgentState, create_agent

  class MyState(AgentState):
      user_id: str
      call_count: int

  agent = create_agent(
      model="ollama:north-mini-code-1.0",
      tools=[],
      state_schema=MyState,  # [!code highlight]
  )
  ```

For full details, examples, and middleware-level state schemas, see [Short-term memory](/oss/python/langchain/short-term-memory#customizing-agent-memory) and [Custom middleware](/oss/python/langchain/middleware/custom#state-updates).

## Invocation

  Trace each step of this loop, debug tool calls, and evaluate agent outputs with [LangSmith](https://smith.langchain.com?utm_source=docs\&utm_medium=cta\&utm_campaign=langsmith-signup\&utm_content=oss-langchain-agents). Follow the [tracing quickstart](/langsmith/trace-with-langchain) to get set up. We recommend you also set up [LangSmith Engine](/langsmith/engine) which monitors your traces, detects issues, and proposes fixes.

You can invoke an agent with a message. Behind the scenes that passes an update to the agent's [`State`](/oss/python/langgraph/graph-api#state). All agents include a [sequence of messages](/oss/python/langgraph/use-graph-api#messagesstate) in their state; to invoke the agent, pass a new message along with a `thread_id` so the agent can persist and resume conversation history:

  ```python
  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  agent = create_agent(
      model="google_genai:gemini-3.6-flash",
      tools=[],
      checkpointer=InMemorySaver(),
  )

  config = {"configurable": {"thread_id": str(uuid7())}}

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config=config,
  )

  # A follow-up turn on the same conversation: reuse the same thread_id to keep history
  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What about tomorrow?"}]},
      config=config,
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  agent = create_agent(
      model="openai:gpt-5.5",
      tools=[],
      checkpointer=InMemorySaver(),
  )

  config = {"configurable": {"thread_id": str(uuid7())}}

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config=config,
  )

  # A follow-up turn on the same conversation: reuse the same thread_id to keep history
  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What about tomorrow?"}]},
      config=config,
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  agent = create_agent(
      model="anthropic:claude-sonnet-4-6",
      tools=[],
      checkpointer=InMemorySaver(),
  )

  config = {"configurable": {"thread_id": str(uuid7())}}

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config=config,
  )

  # A follow-up turn on the same conversation: reuse the same thread_id to keep history
  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What about tomorrow?"}]},
      config=config,
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  agent = create_agent(
      model="openrouter:z-ai/glm-5.2",
      tools=[],
      checkpointer=InMemorySaver(),
  )

  config = {"configurable": {"thread_id": str(uuid7())}}

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config=config,
  )

  # A follow-up turn on the same conversation: reuse the same thread_id to keep history
  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What about tomorrow?"}]},
      config=config,
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  agent = create_agent(
      model="fireworks:accounts/fireworks/models/glm-5p2",
      tools=[],
      checkpointer=InMemorySaver(),
  )

  config = {"configurable": {"thread_id": str(uuid7())}}

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config=config,
  )

  # A follow-up turn on the same conversation: reuse the same thread_id to keep history
  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What about tomorrow?"}]},
      config=config,
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  agent = create_agent(
      model="baseten:zai-org/GLM-5.2",
      tools=[],
      checkpointer=InMemorySaver(),
  )

  config = {"configurable": {"thread_id": str(uuid7())}}

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config=config,
  )

  # A follow-up turn on the same conversation: reuse the same thread_id to keep history
  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What about tomorrow?"}]},
      config=config,
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  agent = create_agent(
      model="ollama:north-mini-code-1.0",
      tools=[],
      checkpointer=InMemorySaver(),
  )

  config = {"configurable": {"thread_id": str(uuid7())}}

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config=config,
  )

  # A follow-up turn on the same conversation: reuse the same thread_id to keep history
  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What about tomorrow?"}]},
      config=config,
  )
  ```

  Persisting conversation history with `thread_id` requires the agent to be configured with a [checkpointer](/oss/python/langchain/long-term-memory). When deployed on [LangSmith](/langsmith/deployment), a checkpointer is provisioned automatically. Locally, pass one explicitly, for example `create_agent(..., checkpointer=InMemorySaver())`.

If you also need to pass per-run configuration (such as a user ID, API keys, or feature flags) to tools and middleware, pass it as `context` alongside `config`. Define the shape of that data with `context_schema` and access it through `runtime.context`:

  ```python
  from dataclasses import dataclass

  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  @dataclass
  class Context:
      user_id: str

  agent = create_agent(
      model="google_genai:gemini-3.6-flash",
      tools=[],
      context_schema=Context,
      checkpointer=InMemorySaver(),
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config={"configurable": {"thread_id": str(uuid7())}},
      context=Context(user_id="user-123"),
  )
  ```

  ```python
  from dataclasses import dataclass

  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  @dataclass
  class Context:
      user_id: str

  agent = create_agent(
      model="openai:gpt-5.5",
      tools=[],
      context_schema=Context,
      checkpointer=InMemorySaver(),
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config={"configurable": {"thread_id": str(uuid7())}},
      context=Context(user_id="user-123"),
  )
  ```

  ```python
  from dataclasses import dataclass

  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  @dataclass
  class Context:
      user_id: str

  agent = create_agent(
      model="anthropic:claude-sonnet-4-6",
      tools=[],
      context_schema=Context,
      checkpointer=InMemorySaver(),
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config={"configurable": {"thread_id": str(uuid7())}},
      context=Context(user_id="user-123"),
  )
  ```

  ```python
  from dataclasses import dataclass

  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  @dataclass
  class Context:
      user_id: str

  agent = create_agent(
      model="openrouter:z-ai/glm-5.2",
      tools=[],
      context_schema=Context,
      checkpointer=InMemorySaver(),
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config={"configurable": {"thread_id": str(uuid7())}},
      context=Context(user_id="user-123"),
  )
  ```

  ```python
  from dataclasses import dataclass

  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  @dataclass
  class Context:
      user_id: str

  agent = create_agent(
      model="fireworks:accounts/fireworks/models/glm-5p2",
      tools=[],
      context_schema=Context,
      checkpointer=InMemorySaver(),
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config={"configurable": {"thread_id": str(uuid7())}},
      context=Context(user_id="user-123"),
  )
  ```

  ```python
  from dataclasses import dataclass

  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  @dataclass
  class Context:
      user_id: str

  agent = create_agent(
      model="baseten:zai-org/GLM-5.2",
      tools=[],
      context_schema=Context,
      checkpointer=InMemorySaver(),
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config={"configurable": {"thread_id": str(uuid7())}},
      context=Context(user_id="user-123"),
  )
  ```

  ```python
  from dataclasses import dataclass

  from langchain.agents import create_agent
  from langchain_core.utils.uuid import uuid7
  from langgraph.checkpoint.memory import InMemorySaver

  @dataclass
  class Context:
      user_id: str

  agent = create_agent(
      model="ollama:north-mini-code-1.0",
      tools=[],
      context_schema=Context,
      checkpointer=InMemorySaver(),
  )

  result = agent.invoke(
      {"messages": [{"role": "user", "content": "What's the weather in San Francisco?"}]},
      config={"configurable": {"thread_id": str(uuid7())}},
      context=Context(user_id="user-123"),
  )
  ```

`thread_id` scopes the *conversation* (message history, checkpoints), while `context` carries *per-run* data your tools and middleware read at invocation time. Both are commonly passed together. See [tool context](/oss/python/langchain/tools#context) and [Runtime](/oss/python/langchain/runtime) for more.

## Streaming

`invoke` returns the final response at the end of a run. If an agent executes multiple tool calls, users often need progress updates before completion. Use streaming to surface intermediate messages and tool activity as they happen.

```python
from langchain.messages import AIMessage, HumanMessage

stream = agent.stream_events(
    {"messages": [{"role": "user", "content": "Search for AI news and summarize the findings"}]},
    version="v3",
)
for snapshot in stream.values:
    # Each snapshot contains the full state at that point
    latest_message = snapshot["messages"][-1]
    if latest_message.content:
        if isinstance(latest_message, HumanMessage):
            print(f"User: {latest_message.content}")
        elif isinstance(latest_message, AIMessage):
            print(f"Agent: {latest_message.content}")
    elif latest_message.tool_calls:
        print(f"Calling tools: {[tc['name'] for tc in latest_message.tool_calls]}")
```

  For streaming modes, event types, and UI patterns, see [Streaming](/oss/python/langchain/streaming).

## Configure the harness

`create_agent` is highly extensible. Middleware is the primitive for customization: each piece handles one concern, hooks into the agent loop at the right moment, and composes freely with any other. Take exactly what your use case needs and skip the rest.

Common patterns are prebuilt as first-class middleware. You can build anything else as [custom middleware](/oss/python/langchain/middleware/custom).

As agents take on complex work, they need support across a few key areas. The middleware ecosystem provides:

  - [Execution environment](#execution-environment)
    Tools, filesystem, sandboxes, and code execution

  - [Context management](#context-management)
    Summarization, memory, skills, and prompt caching

  - [Planning and delegation](#planning-and-delegation)
    Todo lists and subagents for parallel, isolated work

  - [Fault tolerance](#fault-tolerance)
    Retries, fallbacks, and call limits

  - [Guardrails](#guardrails)
    PII detection and content controls

  - [Steering](#steering)
    Human-in-the-loop approval before high-impact actions

  `create_deep_agent` pre-assembles this stack for long-running coding and research tasks (filesystem, summarization, subagents, and prompt caching included by default). See [Deep Agents](/oss/python/deepagents/harness) for the full prebuilt harness.

### Execution environment

Agents are especially useful when they can take action rather than just generate text. The execution environment gives the agent a workspace: tools it can call, a filesystem for reading and writing files across turns, and code execution for running scripts or shell commands.

  ```python
  from langchain.agents import create_agent
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware

  agent = create_agent(
      model="google_genai:gemini-3.6-flash",
      tools=[search],
      middleware=[FilesystemMiddleware(backend=StateBackend())],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware

  agent = create_agent(
      model="openai:gpt-5.5",
      tools=[search],
      middleware=[FilesystemMiddleware(backend=StateBackend())],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware

  agent = create_agent(
      model="anthropic:claude-sonnet-4-6",
      tools=[search],
      middleware=[FilesystemMiddleware(backend=StateBackend())],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware

  agent = create_agent(
      model="openrouter:z-ai/glm-5.2",
      tools=[search],
      middleware=[FilesystemMiddleware(backend=StateBackend())],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware

  agent = create_agent(
      model="fireworks:accounts/fireworks/models/glm-5p2",
      tools=[search],
      middleware=[FilesystemMiddleware(backend=StateBackend())],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware

  agent = create_agent(
      model="baseten:zai-org/GLM-5.2",
      tools=[search],
      middleware=[FilesystemMiddleware(backend=StateBackend())],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware

  agent = create_agent(
      model="ollama:north-mini-code-1.0",
      tools=[search],
      middleware=[FilesystemMiddleware(backend=StateBackend())],
  )
  ```

See [`FilesystemMiddleware`](https://reference.langchain.com/python/deepagents/middleware/filesystem/FilesystemMiddleware), [Sandboxes](/oss/python/deepagents/sandboxes), [Interpreters](/oss/python/deepagents/interpreters).

  This example imports from the `deepagents` package. Install it with:

    ```bash
    pip install deepagents
    ```

    ```bash
    uv add deepagents
    ```

### Context management

Every model call has a fixed context window. As an agent runs, that window fills with accumulating history, tool results, and intermediate steps. Summarization compresses history before overflow hits; memory loads persistent instructions at startup so knowledge carries across sessions; skills surface domain knowledge on demand rather than loading everything upfront.

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware, MemoryMiddleware, SkillsMiddleware, SummarizationMiddleware

  backend = StateBackend()
  model = "google_genai:gemini-3.6-flash"

  agent = create_agent(
      model=model,
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          SummarizationMiddleware(model=model, backend=backend),
          MemoryMiddleware(backend=backend, sources=["./AGENTS.md"]),
          SkillsMiddleware(backend=backend, sources=["./skills/"]),
      ],
  )
  ```

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware, MemoryMiddleware, SkillsMiddleware, SummarizationMiddleware

  backend = StateBackend()
  model = "openai:gpt-5.5"

  agent = create_agent(
      model=model,
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          SummarizationMiddleware(model=model, backend=backend),
          MemoryMiddleware(backend=backend, sources=["./AGENTS.md"]),
          SkillsMiddleware(backend=backend, sources=["./skills/"]),
      ],
  )
  ```

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware, MemoryMiddleware, SkillsMiddleware, SummarizationMiddleware

  backend = StateBackend()
  model = "anthropic:claude-sonnet-4-6"

  agent = create_agent(
      model=model,
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          SummarizationMiddleware(model=model, backend=backend),
          MemoryMiddleware(backend=backend, sources=["./AGENTS.md"]),
          SkillsMiddleware(backend=backend, sources=["./skills/"]),
      ],
  )
  ```

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware, MemoryMiddleware, SkillsMiddleware, SummarizationMiddleware

  backend = StateBackend()
  model = "openrouter:z-ai/glm-5.2"

  agent = create_agent(
      model=model,
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          SummarizationMiddleware(model=model, backend=backend),
          MemoryMiddleware(backend=backend, sources=["./AGENTS.md"]),
          SkillsMiddleware(backend=backend, sources=["./skills/"]),
      ],
  )
  ```

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware, MemoryMiddleware, SkillsMiddleware, SummarizationMiddleware

  backend = StateBackend()
  model = "fireworks:accounts/fireworks/models/glm-5p2"

  agent = create_agent(
      model=model,
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          SummarizationMiddleware(model=model, backend=backend),
          MemoryMiddleware(backend=backend, sources=["./AGENTS.md"]),
          SkillsMiddleware(backend=backend, sources=["./skills/"]),
      ],
  )
  ```

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware, MemoryMiddleware, SkillsMiddleware, SummarizationMiddleware

  backend = StateBackend()
  model = "baseten:zai-org/GLM-5.2"

  agent = create_agent(
      model=model,
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          SummarizationMiddleware(model=model, backend=backend),
          MemoryMiddleware(backend=backend, sources=["./AGENTS.md"]),
          SkillsMiddleware(backend=backend, sources=["./skills/"]),
      ],
  )
  ```

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware, MemoryMiddleware, SkillsMiddleware, SummarizationMiddleware

  backend = StateBackend()
  model = "ollama:north-mini-code-1.0"

  agent = create_agent(
      model=model,
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          SummarizationMiddleware(model=model, backend=backend),
          MemoryMiddleware(backend=backend, sources=["./AGENTS.md"]),
          SkillsMiddleware(backend=backend, sources=["./skills/"]),
      ],
  )
  ```

See [`SummarizationMiddleware`](https://reference.langchain.com/python/langchain/agents/middleware/summarization/SummarizationMiddleware), [`MemoryMiddleware`](https://reference.langchain.com/python/deepagents/middleware/memory/MemoryMiddleware), [Skills](/oss/python/langchain/multi-agent/skills), [Context engineering](/oss/python/deepagents/context-engineering).

  This example imports from the `deepagents` package. Install it with:

    ```bash
    pip install deepagents
    ```

    ```bash
    uv add deepagents
    ```

### Planning and delegation

Complex tasks often exceed what one context window can handle. Delegation lets the main agent break work into pieces, hand them to subagents that each run in their own isolated context, and stay focused on coordination rather than execution. Work can run in parallel; the main agent's context stays clean.

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware
  from deepagents.middleware.subagents import SubAgentMiddleware
  from langchain.agents import create_agent
  from langchain.agents.middleware import TodoListMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  backend = StateBackend()

  agent = create_agent(
      model="google_genai:gemini-3.6-flash",
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          TodoListMiddleware(),
          SubAgentMiddleware(
              backend=backend,
              subagents=[
                  {
                      "name": "researcher",
                      "description": "Searches and returns a structured summary.",
                      "system_prompt": "Use the search tool to research the question and summarize key points.",
                      "tools": [search],
                      "model": "anthropic:claude-sonnet-4-6",
                      "middleware": [],
                  }
              ],
          ),
      ],
  )
  ```

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware
  from deepagents.middleware.subagents import SubAgentMiddleware
  from langchain.agents import create_agent
  from langchain.agents.middleware import TodoListMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  backend = StateBackend()

  agent = create_agent(
      model="openai:gpt-5.5",
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          TodoListMiddleware(),
          SubAgentMiddleware(
              backend=backend,
              subagents=[
                  {
                      "name": "researcher",
                      "description": "Searches and returns a structured summary.",
                      "system_prompt": "Use the search tool to research the question and summarize key points.",
                      "tools": [search],
                      "model": "anthropic:claude-sonnet-4-6",
                      "middleware": [],
                  }
              ],
          ),
      ],
  )
  ```

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware
  from deepagents.middleware.subagents import SubAgentMiddleware
  from langchain.agents import create_agent
  from langchain.agents.middleware import TodoListMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  backend = StateBackend()

  agent = create_agent(
      model="anthropic:claude-sonnet-4-6",
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          TodoListMiddleware(),
          SubAgentMiddleware(
              backend=backend,
              subagents=[
                  {
                      "name": "researcher",
                      "description": "Searches and returns a structured summary.",
                      "system_prompt": "Use the search tool to research the question and summarize key points.",
                      "tools": [search],
                      "model": "anthropic:claude-sonnet-4-6",
                      "middleware": [],
                  }
              ],
          ),
      ],
  )
  ```

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware
  from deepagents.middleware.subagents import SubAgentMiddleware
  from langchain.agents import create_agent
  from langchain.agents.middleware import TodoListMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  backend = StateBackend()

  agent = create_agent(
      model="openrouter:z-ai/glm-5.2",
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          TodoListMiddleware(),
          SubAgentMiddleware(
              backend=backend,
              subagents=[
                  {
                      "name": "researcher",
                      "description": "Searches and returns a structured summary.",
                      "system_prompt": "Use the search tool to research the question and summarize key points.",
                      "tools": [search],
                      "model": "anthropic:claude-sonnet-4-6",
                      "middleware": [],
                  }
              ],
          ),
      ],
  )
  ```

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware
  from deepagents.middleware.subagents import SubAgentMiddleware
  from langchain.agents import create_agent
  from langchain.agents.middleware import TodoListMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  backend = StateBackend()

  agent = create_agent(
      model="fireworks:accounts/fireworks/models/glm-5p2",
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          TodoListMiddleware(),
          SubAgentMiddleware(
              backend=backend,
              subagents=[
                  {
                      "name": "researcher",
                      "description": "Searches and returns a structured summary.",
                      "system_prompt": "Use the search tool to research the question and summarize key points.",
                      "tools": [search],
                      "model": "anthropic:claude-sonnet-4-6",
                      "middleware": [],
                  }
              ],
          ),
      ],
  )
  ```

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware
  from deepagents.middleware.subagents import SubAgentMiddleware
  from langchain.agents import create_agent
  from langchain.agents.middleware import TodoListMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  backend = StateBackend()

  agent = create_agent(
      model="baseten:zai-org/GLM-5.2",
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          TodoListMiddleware(),
          SubAgentMiddleware(
              backend=backend,
              subagents=[
                  {
                      "name": "researcher",
                      "description": "Searches and returns a structured summary.",
                      "system_prompt": "Use the search tool to research the question and summarize key points.",
                      "tools": [search],
                      "model": "anthropic:claude-sonnet-4-6",
                      "middleware": [],
                  }
              ],
          ),
      ],
  )
  ```

  ```python
  from deepagents.backends import StateBackend
  from deepagents.middleware import FilesystemMiddleware
  from deepagents.middleware.subagents import SubAgentMiddleware
  from langchain.agents import create_agent
  from langchain.agents.middleware import TodoListMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  backend = StateBackend()

  agent = create_agent(
      model="ollama:north-mini-code-1.0",
      tools=[search],
      middleware=[
          FilesystemMiddleware(backend=backend),
          TodoListMiddleware(),
          SubAgentMiddleware(
              backend=backend,
              subagents=[
                  {
                      "name": "researcher",
                      "description": "Searches and returns a structured summary.",
                      "system_prompt": "Use the search tool to research the question and summarize key points.",
                      "tools": [search],
                      "model": "anthropic:claude-sonnet-4-6",
                      "middleware": [],
                  }
              ],
          ),
      ],
  )
  ```

See [Subagents](/oss/python/langchain/multi-agent/subagents).

  This example imports from the `deepagents` package. Install it with:

    ```bash
    pip install deepagents
    ```

    ```bash
    uv add deepagents
    ```

### Name your agent

Optionally use an identifier for the agent. This is especially useful when embedding the agent as a subgraph in [multi-agent](/oss/python/langchain/multi-agent) systems.

  ```python
  agent = create_agent(model="google_genai:gemini-3.6-flash", tools=tools, name="research_assistant")
  ```

  ```python
  agent = create_agent(model="openai:gpt-5.5", tools=tools, name="research_assistant")
  ```

  ```python
  agent = create_agent(model="anthropic:claude-sonnet-4-6", tools=tools, name="research_assistant")
  ```

  ```python
  agent = create_agent(model="openrouter:z-ai/glm-5.2", tools=tools, name="research_assistant")
  ```

  ```python
  agent = create_agent(model="fireworks:accounts/fireworks/models/glm-5p2", tools=tools, name="research_assistant")
  ```

  ```python
  agent = create_agent(model="baseten:zai-org/GLM-5.2", tools=tools, name="research_assistant")
  ```

  ```python
  agent = create_agent(model="ollama:north-mini-code-1.0", tools=tools, name="research_assistant")
  ```

### Fault tolerance

Agents in production encounter failures that rarely appear in development: rate limits, model timeouts, transient API errors. Fault tolerance middleware handles these at the infrastructure level so your tools and business logic don't need try/catch around every call.

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import ModelRetryMiddleware, ToolRetryMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="google_genai:gemini-3.6-flash",
      tools=[search],
      middleware=[
          ModelRetryMiddleware(max_retries=3),
          ToolRetryMiddleware(max_retries=2),
      ],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import ModelRetryMiddleware, ToolRetryMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="openai:gpt-5.5",
      tools=[search],
      middleware=[
          ModelRetryMiddleware(max_retries=3),
          ToolRetryMiddleware(max_retries=2),
      ],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import ModelRetryMiddleware, ToolRetryMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="anthropic:claude-sonnet-4-6",
      tools=[search],
      middleware=[
          ModelRetryMiddleware(max_retries=3),
          ToolRetryMiddleware(max_retries=2),
      ],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import ModelRetryMiddleware, ToolRetryMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="openrouter:z-ai/glm-5.2",
      tools=[search],
      middleware=[
          ModelRetryMiddleware(max_retries=3),
          ToolRetryMiddleware(max_retries=2),
      ],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import ModelRetryMiddleware, ToolRetryMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="fireworks:accounts/fireworks/models/glm-5p2",
      tools=[search],
      middleware=[
          ModelRetryMiddleware(max_retries=3),
          ToolRetryMiddleware(max_retries=2),
      ],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import ModelRetryMiddleware, ToolRetryMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="baseten:zai-org/GLM-5.2",
      tools=[search],
      middleware=[
          ModelRetryMiddleware(max_retries=3),
          ToolRetryMiddleware(max_retries=2),
      ],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import ModelRetryMiddleware, ToolRetryMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="ollama:north-mini-code-1.0",
      tools=[search],
      middleware=[
          ModelRetryMiddleware(max_retries=3),
          ToolRetryMiddleware(max_retries=2),
      ],
  )
  ```

See [`ModelRetryMiddleware`](https://reference.langchain.com/python/langchain/agents/middleware/model_retry/ModelRetryMiddleware), [`ToolRetryMiddleware`](https://reference.langchain.com/python/langchain/agents/middleware/tool_retry/ToolRetryMiddleware), [Prebuilt middleware](/oss/python/langchain/middleware/built-in).

### Guardrails

Some policies can't live in a prompt—they need to be enforced deterministically regardless of what the model does. Guardrails intercept data as it flows through the agent loop, applying compliance rules or content policies before tool results reach the model's context.

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import PIIMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="google_genai:gemini-3.6-flash",
      tools=[search],
      middleware=[PIIMiddleware("email")],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import PIIMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="openai:gpt-5.5",
      tools=[search],
      middleware=[PIIMiddleware("email")],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import PIIMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="anthropic:claude-sonnet-4-6",
      tools=[search],
      middleware=[PIIMiddleware("email")],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import PIIMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="openrouter:z-ai/glm-5.2",
      tools=[search],
      middleware=[PIIMiddleware("email")],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import PIIMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="fireworks:accounts/fireworks/models/glm-5p2",
      tools=[search],
      middleware=[PIIMiddleware("email")],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import PIIMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="baseten:zai-org/GLM-5.2",
      tools=[search],
      middleware=[PIIMiddleware("email")],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import PIIMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="ollama:north-mini-code-1.0",
      tools=[search],
      middleware=[PIIMiddleware("email")],
  )
  ```

See [`PIIMiddleware`](https://reference.langchain.com/python/langchain/agents/middleware/pii/PIIMiddleware), [Prebuilt middleware](/oss/python/langchain/middleware/built-in).

### Steering

Full autonomy isn't always appropriate. Steering lets you place humans at specific decision points—before destructive writes, expensive API calls, or anything requiring judgment—without restructuring your agent. The agent pauses and waits; a human approves, edits, or rejects; execution continues.

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import HumanInTheLoopMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="google_genai:gemini-3.6-flash",
      tools=[search],
      middleware=[HumanInTheLoopMiddleware(interrupt_on={"write_file": True})],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import HumanInTheLoopMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="openai:gpt-5.5",
      tools=[search],
      middleware=[HumanInTheLoopMiddleware(interrupt_on={"write_file": True})],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import HumanInTheLoopMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="anthropic:claude-sonnet-4-6",
      tools=[search],
      middleware=[HumanInTheLoopMiddleware(interrupt_on={"write_file": True})],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import HumanInTheLoopMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="openrouter:z-ai/glm-5.2",
      tools=[search],
      middleware=[HumanInTheLoopMiddleware(interrupt_on={"write_file": True})],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import HumanInTheLoopMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="fireworks:accounts/fireworks/models/glm-5p2",
      tools=[search],
      middleware=[HumanInTheLoopMiddleware(interrupt_on={"write_file": True})],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import HumanInTheLoopMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="baseten:zai-org/GLM-5.2",
      tools=[search],
      middleware=[HumanInTheLoopMiddleware(interrupt_on={"write_file": True})],
  )
  ```

  ```python
  from langchain.agents import create_agent
  from langchain.agents.middleware import HumanInTheLoopMiddleware
  from langchain.tools import tool

  @tool
  def search(query: str) -> str:
      """Search for a query and return a short summary."""
      return f"Search results for: {query}"

  agent = create_agent(
      model="ollama:north-mini-code-1.0",
      tools=[search],
      middleware=[HumanInTheLoopMiddleware(interrupt_on={"write_file": True})],
  )
  ```

See [`HumanInTheLoopMiddleware`](https://reference.langchain.com/python/langchain/agents/middleware/human_in_the_loop/HumanInTheLoopMiddleware), [Human-in-the-loop](/oss/python/langchain/human-in-the-loop).

### Middleware resources

  - [Middleware overview](/oss/python/langchain/middleware/overview)
    How the middleware stack works and when hooks fire

  - [Prebuilt middleware](/oss/python/langchain/middleware/built-in)
    Full reference with configuration examples

  - [Custom middleware](/oss/python/langchain/middleware/custom)
    Write your own hooks for business logic, PII scrubbing, and more
