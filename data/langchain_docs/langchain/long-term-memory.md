<!-- source: https://docs.langchain.com/oss/python/langchain/long-term-memory.md | section: /oss/python/langchain | fetched: 2026-09-09 -->

# Long-term memory

> Add long-term memory to LangChain agents to store and recall data across conversations and sessions

Long-term memory lets your agent store and recall information across different conversations and sessions.
Unlike [short-term memory](/oss/python/langchain/short-term-memory), which is scoped to a single thread, long-term memory persists across threads and can be recalled at any time.

Long-term memory is built on [LangGraph stores](/oss/python/langgraph/stores), which save data as JSON documents organized by namespace and key.

## Usage

To add long-term memory to an agent, create a store and pass it to [`create_agent`](https://reference.langchain.com/python/langchain/agents/factory/create_agent):

**InMemoryStore**

    ```python
    from langchain.agents import create_agent
    from langchain_core.runnables import Runnable
    from langgraph.store.memory import InMemoryStore

    # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production use.
    store = InMemoryStore()

    agent: Runnable = create_agent(
        "claude-sonnet-4-6",
        tools=[],
        store=store,
    )
    ```

**PostgreSQL**

      ```bash
      pip install -U langgraph-checkpoint-postgres "psycopg[binary]"
      ```

      ```bash
      uv add langgraph-checkpoint-postgres "psycopg[binary]"
      ```

      By default, `langgraph-checkpoint-postgres` installs `psycopg` (Psycopg 3) without extras. The install above adds `psycopg[binary]`, which is recommended for most users. For other options, see the [Psycopg installation docs](https://www.psycopg.org/psycopg3/docs/basic/install.html).

    ```python
    from langchain.agents import create_agent
    from langchain_core.runnables import Runnable
    from langgraph.store.postgres import PostgresStore  # type: ignore[import-not-found]

    DB_URI = "postgresql://postgres:postgres@localhost:5432/postgres?sslmode=disable"

    with PostgresStore.from_conn_string(DB_URI) as store:
        store.setup()
        agent: Runnable = create_agent(
            "claude-sonnet-4-6",
            tools=[],
            store=store,
        )
    ```

  For other store backends, including Redis and MongoDB, see the [store integrations](/oss/python/integrations/long-term-memory) list. For a MongoDB walkthrough, see [long-term memory with MongoDB](/oss/python/integrations/memory/mongodb-long-term-memory).

Tools can then read from and write to the store using the `runtime.store` parameter. See [Read long-term memory in tools](#read-long-term-memory-in-tools) and [Write long-term memory from tools](#write-long-term-memory-from-tools) for examples.

  For a deeper dive into memory types (semantic, episodic, procedural) and strategies for writing memories, see the [Memory conceptual guide](/oss/python/concepts/memory#long-term-memory).

## Memory storage

LangGraph stores long-term memories as JSON documents in a [store](/oss/python/langgraph/stores).

Each memory is organized under a custom `namespace` (similar to a folder) and a distinct `key` (like a file name). Namespaces often include user or org IDs or other labels that makes it easier to organize information.

This structure enables hierarchical organization of memories. Cross-namespace searching is then supported through content filters.

**InMemoryStore**

    ```python
    from collections.abc import Sequence

    from langgraph.store.base import IndexConfig
    from langgraph.store.memory import InMemoryStore

    def embed(texts: Sequence[str]) -> list[list[float]]:
        # Replace with an actual embedding function or LangChain embeddings object
        return [[1.0, 2.0] for _ in texts]

    # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production use.
    store = InMemoryStore(index=IndexConfig(embed=embed, dims=2))
    user_id = "my-user"
    application_context = "chitchat"
    namespace = (user_id, application_context)
    store.put(
        namespace,
        "a-memory",
        {
            "rules": [
                "User likes short, direct language",
                "User only speaks English & python",
            ],
            "my-key": "my-value",
        },
    )
    # get the "memory" by ID
    item = store.get(namespace, "a-memory")
    # search for "memories" within this namespace, filtering on content equivalence, sorted by vector similarity
    items = store.search(
        namespace, filter={"my-key": "my-value"}, query="language preferences"
    )
    ```

**PostgreSQL**

    ```python
    from collections.abc import Sequence

    from langgraph.store.base import IndexConfig
    from langgraph.store.postgres import PostgresStore  # type: ignore[import-not-found]

    def embed(texts: Sequence[str]) -> list[list[float]]:
        # Replace with an actual embedding function or LangChain embeddings object
        return [[1.0, 2.0] for _ in texts]

    DB_URI = "postgresql://postgres:postgres@localhost:5432/postgres?sslmode=disable"

    with PostgresStore.from_conn_string(
        DB_URI,
        index=IndexConfig(embed=embed, dims=2),  # type: ignore[arg-type]
    ) as store:
        store.setup()
        user_id = "my-user"
        application_context = "chitchat"
        namespace = (user_id, application_context)
        store.put(
            namespace,
            "a-memory",
            {
                "rules": [
                    "User likes short, direct language",
                    "User only speaks English & python",
                ],
                "my-key": "my-value",
            },
        )
        item = store.get(namespace, "a-memory")
        items = store.search(
            namespace, filter={"my-key": "my-value"}, query="language preferences"
        )
    ```

For more information about the memory store, see the [Persistence](/oss/python/langgraph/stores) guide.

## Read long-term memory in tools

**InMemoryStore**

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore

      @dataclass
      class Context:
          user_id: str

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      # Write sample data to the store using the put method
      store.put(
          (
              "users",
          ),  # Namespace to group related data together (users namespace for user data)
          "user_123",  # Key within the namespace (user ID as key)
          {
              "name": "John Smith",
              "language": "English",
          },  # Data to store for the given user
      )

      @tool
      def get_user_info(runtime: ToolRuntime[Context]) -> str:
          """Look up user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          user_id = runtime.context.user_id
          # Retrieve data from store - returns StoreValue object with value and metadata
          user_info = runtime.store.get(("users",), user_id)
          return str(user_info.value) if user_info else "Unknown user"

      agent: Runnable = create_agent(
          model="google_genai:gemini-3.6-flash",
          tools=[get_user_info],
          # Pass store to agent - enables agent to access store when running tools
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "look up user information"}]},
          context=Context(user_id="user_123"),
      )
      ```

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore

      @dataclass
      class Context:
          user_id: str

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      # Write sample data to the store using the put method
      store.put(
          (
              "users",
          ),  # Namespace to group related data together (users namespace for user data)
          "user_123",  # Key within the namespace (user ID as key)
          {
              "name": "John Smith",
              "language": "English",
          },  # Data to store for the given user
      )

      @tool
      def get_user_info(runtime: ToolRuntime[Context]) -> str:
          """Look up user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          user_id = runtime.context.user_id
          # Retrieve data from store - returns StoreValue object with value and metadata
          user_info = runtime.store.get(("users",), user_id)
          return str(user_info.value) if user_info else "Unknown user"

      agent: Runnable = create_agent(
          model="openai:gpt-5.5",
          tools=[get_user_info],
          # Pass store to agent - enables agent to access store when running tools
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "look up user information"}]},
          context=Context(user_id="user_123"),
      )
      ```

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore

      @dataclass
      class Context:
          user_id: str

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      # Write sample data to the store using the put method
      store.put(
          (
              "users",
          ),  # Namespace to group related data together (users namespace for user data)
          "user_123",  # Key within the namespace (user ID as key)
          {
              "name": "John Smith",
              "language": "English",
          },  # Data to store for the given user
      )

      @tool
      def get_user_info(runtime: ToolRuntime[Context]) -> str:
          """Look up user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          user_id = runtime.context.user_id
          # Retrieve data from store - returns StoreValue object with value and metadata
          user_info = runtime.store.get(("users",), user_id)
          return str(user_info.value) if user_info else "Unknown user"

      agent: Runnable = create_agent(
          model="anthropic:claude-sonnet-4-6",
          tools=[get_user_info],
          # Pass store to agent - enables agent to access store when running tools
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "look up user information"}]},
          context=Context(user_id="user_123"),
      )
      ```

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore

      @dataclass
      class Context:
          user_id: str

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      # Write sample data to the store using the put method
      store.put(
          (
              "users",
          ),  # Namespace to group related data together (users namespace for user data)
          "user_123",  # Key within the namespace (user ID as key)
          {
              "name": "John Smith",
              "language": "English",
          },  # Data to store for the given user
      )

      @tool
      def get_user_info(runtime: ToolRuntime[Context]) -> str:
          """Look up user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          user_id = runtime.context.user_id
          # Retrieve data from store - returns StoreValue object with value and metadata
          user_info = runtime.store.get(("users",), user_id)
          return str(user_info.value) if user_info else "Unknown user"

      agent: Runnable = create_agent(
          model="openrouter:z-ai/glm-5.2",
          tools=[get_user_info],
          # Pass store to agent - enables agent to access store when running tools
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "look up user information"}]},
          context=Context(user_id="user_123"),
      )
      ```

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore

      @dataclass
      class Context:
          user_id: str

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      # Write sample data to the store using the put method
      store.put(
          (
              "users",
          ),  # Namespace to group related data together (users namespace for user data)
          "user_123",  # Key within the namespace (user ID as key)
          {
              "name": "John Smith",
              "language": "English",
          },  # Data to store for the given user
      )

      @tool
      def get_user_info(runtime: ToolRuntime[Context]) -> str:
          """Look up user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          user_id = runtime.context.user_id
          # Retrieve data from store - returns StoreValue object with value and metadata
          user_info = runtime.store.get(("users",), user_id)
          return str(user_info.value) if user_info else "Unknown user"

      agent: Runnable = create_agent(
          model="fireworks:accounts/fireworks/models/glm-5p2",
          tools=[get_user_info],
          # Pass store to agent - enables agent to access store when running tools
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "look up user information"}]},
          context=Context(user_id="user_123"),
      )
      ```

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore

      @dataclass
      class Context:
          user_id: str

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      # Write sample data to the store using the put method
      store.put(
          (
              "users",
          ),  # Namespace to group related data together (users namespace for user data)
          "user_123",  # Key within the namespace (user ID as key)
          {
              "name": "John Smith",
              "language": "English",
          },  # Data to store for the given user
      )

      @tool
      def get_user_info(runtime: ToolRuntime[Context]) -> str:
          """Look up user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          user_id = runtime.context.user_id
          # Retrieve data from store - returns StoreValue object with value and metadata
          user_info = runtime.store.get(("users",), user_id)
          return str(user_info.value) if user_info else "Unknown user"

      agent: Runnable = create_agent(
          model="baseten:zai-org/GLM-5.2",
          tools=[get_user_info],
          # Pass store to agent - enables agent to access store when running tools
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "look up user information"}]},
          context=Context(user_id="user_123"),
      )
      ```

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore

      @dataclass
      class Context:
          user_id: str

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      # Write sample data to the store using the put method
      store.put(
          (
              "users",
          ),  # Namespace to group related data together (users namespace for user data)
          "user_123",  # Key within the namespace (user ID as key)
          {
              "name": "John Smith",
              "language": "English",
          },  # Data to store for the given user
      )

      @tool
      def get_user_info(runtime: ToolRuntime[Context]) -> str:
          """Look up user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          user_id = runtime.context.user_id
          # Retrieve data from store - returns StoreValue object with value and metadata
          user_info = runtime.store.get(("users",), user_id)
          return str(user_info.value) if user_info else "Unknown user"

      agent: Runnable = create_agent(
          model="ollama:north-mini-code-1.0",
          tools=[get_user_info],
          # Pass store to agent - enables agent to access store when running tools
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "look up user information"}]},
          context=Context(user_id="user_123"),
      )
      ```

**PostgreSQL**

    ```python
    from dataclasses import dataclass

    from langchain.agents import create_agent
    from langchain.tools import ToolRuntime, tool
    from langchain_core.runnables import Runnable
    from langgraph.store.postgres import PostgresStore  # type: ignore[import-not-found]

    @dataclass
    class Context:
        user_id: str

    DB_URI = "postgresql://postgres:postgres@localhost:5432/postgres?sslmode=disable"

    with PostgresStore.from_conn_string(DB_URI) as store:
        store.setup()
        store.put(("users",), "user_123", {"name": "John Smith", "language": "English"})

        @tool
        def get_user_info(runtime: ToolRuntime[Context]) -> str:
            """Look up user info."""
            assert runtime.store is not None
            user_info = runtime.store.get(("users",), runtime.context.user_id)
            return str(user_info.value) if user_info else "Unknown user"

        agent: Runnable = create_agent(
            "claude-sonnet-4-6",
            tools=[get_user_info],
            store=store,
            context_schema=Context,
        )

        result = agent.invoke(
            {"messages": [{"role": "user", "content": "look up user information"}]},
            context=Context(user_id="user_123"),
        )
    ```

## Write long-term memory from tools

**InMemoryStore**

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore
      from typing_extensions import TypedDict

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      @dataclass
      class Context:
          user_id: str

      # TypedDict defines the structure of user information for the LLM
      class UserInfo(TypedDict):
          name: str

      # Tool that allows agent to update user information (useful for chat applications)
      @tool
      def save_user_info(user_info: UserInfo, runtime: ToolRuntime[Context]) -> str:
          """Save user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          store = runtime.store
          user_id = runtime.context.user_id
          # Store data in the store (namespace, key, data)
          store.put(("users",), user_id, dict(user_info))
          return "Successfully saved user info."

      agent: Runnable = create_agent(
          model="google_genai:gemini-3.6-flash",
          tools=[save_user_info],
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "My name is John Smith"}]},
          # user_id passed in context to identify whose information is being updated
          context=Context(user_id="user_123"),
      )

      # You can access the store directly to get the value
      item = store.get(("users",), "user_123")
      ```

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore
      from typing_extensions import TypedDict

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      @dataclass
      class Context:
          user_id: str

      # TypedDict defines the structure of user information for the LLM
      class UserInfo(TypedDict):
          name: str

      # Tool that allows agent to update user information (useful for chat applications)
      @tool
      def save_user_info(user_info: UserInfo, runtime: ToolRuntime[Context]) -> str:
          """Save user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          store = runtime.store
          user_id = runtime.context.user_id
          # Store data in the store (namespace, key, data)
          store.put(("users",), user_id, dict(user_info))
          return "Successfully saved user info."

      agent: Runnable = create_agent(
          model="openai:gpt-5.5",
          tools=[save_user_info],
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "My name is John Smith"}]},
          # user_id passed in context to identify whose information is being updated
          context=Context(user_id="user_123"),
      )

      # You can access the store directly to get the value
      item = store.get(("users",), "user_123")
      ```

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore
      from typing_extensions import TypedDict

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      @dataclass
      class Context:
          user_id: str

      # TypedDict defines the structure of user information for the LLM
      class UserInfo(TypedDict):
          name: str

      # Tool that allows agent to update user information (useful for chat applications)
      @tool
      def save_user_info(user_info: UserInfo, runtime: ToolRuntime[Context]) -> str:
          """Save user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          store = runtime.store
          user_id = runtime.context.user_id
          # Store data in the store (namespace, key, data)
          store.put(("users",), user_id, dict(user_info))
          return "Successfully saved user info."

      agent: Runnable = create_agent(
          model="anthropic:claude-sonnet-4-6",
          tools=[save_user_info],
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "My name is John Smith"}]},
          # user_id passed in context to identify whose information is being updated
          context=Context(user_id="user_123"),
      )

      # You can access the store directly to get the value
      item = store.get(("users",), "user_123")
      ```

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore
      from typing_extensions import TypedDict

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      @dataclass
      class Context:
          user_id: str

      # TypedDict defines the structure of user information for the LLM
      class UserInfo(TypedDict):
          name: str

      # Tool that allows agent to update user information (useful for chat applications)
      @tool
      def save_user_info(user_info: UserInfo, runtime: ToolRuntime[Context]) -> str:
          """Save user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          store = runtime.store
          user_id = runtime.context.user_id
          # Store data in the store (namespace, key, data)
          store.put(("users",), user_id, dict(user_info))
          return "Successfully saved user info."

      agent: Runnable = create_agent(
          model="openrouter:z-ai/glm-5.2",
          tools=[save_user_info],
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "My name is John Smith"}]},
          # user_id passed in context to identify whose information is being updated
          context=Context(user_id="user_123"),
      )

      # You can access the store directly to get the value
      item = store.get(("users",), "user_123")
      ```

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore
      from typing_extensions import TypedDict

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      @dataclass
      class Context:
          user_id: str

      # TypedDict defines the structure of user information for the LLM
      class UserInfo(TypedDict):
          name: str

      # Tool that allows agent to update user information (useful for chat applications)
      @tool
      def save_user_info(user_info: UserInfo, runtime: ToolRuntime[Context]) -> str:
          """Save user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          store = runtime.store
          user_id = runtime.context.user_id
          # Store data in the store (namespace, key, data)
          store.put(("users",), user_id, dict(user_info))
          return "Successfully saved user info."

      agent: Runnable = create_agent(
          model="fireworks:accounts/fireworks/models/glm-5p2",
          tools=[save_user_info],
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "My name is John Smith"}]},
          # user_id passed in context to identify whose information is being updated
          context=Context(user_id="user_123"),
      )

      # You can access the store directly to get the value
      item = store.get(("users",), "user_123")
      ```

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore
      from typing_extensions import TypedDict

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      @dataclass
      class Context:
          user_id: str

      # TypedDict defines the structure of user information for the LLM
      class UserInfo(TypedDict):
          name: str

      # Tool that allows agent to update user information (useful for chat applications)
      @tool
      def save_user_info(user_info: UserInfo, runtime: ToolRuntime[Context]) -> str:
          """Save user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          store = runtime.store
          user_id = runtime.context.user_id
          # Store data in the store (namespace, key, data)
          store.put(("users",), user_id, dict(user_info))
          return "Successfully saved user info."

      agent: Runnable = create_agent(
          model="baseten:zai-org/GLM-5.2",
          tools=[save_user_info],
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "My name is John Smith"}]},
          # user_id passed in context to identify whose information is being updated
          context=Context(user_id="user_123"),
      )

      # You can access the store directly to get the value
      item = store.get(("users",), "user_123")
      ```

      ```python
      from dataclasses import dataclass

      from langchain.agents import create_agent
      from langchain.tools import ToolRuntime, tool
      from langchain_core.runnables import Runnable
      from langgraph.store.memory import InMemoryStore
      from typing_extensions import TypedDict

      # InMemoryStore saves data to an in-memory dictionary. Use a DB-backed store in production.
      store = InMemoryStore()

      @dataclass
      class Context:
          user_id: str

      # TypedDict defines the structure of user information for the LLM
      class UserInfo(TypedDict):
          name: str

      # Tool that allows agent to update user information (useful for chat applications)
      @tool
      def save_user_info(user_info: UserInfo, runtime: ToolRuntime[Context]) -> str:
          """Save user info."""
          # Access the store - same as that provided to `create_agent`
          assert runtime.store is not None
          store = runtime.store
          user_id = runtime.context.user_id
          # Store data in the store (namespace, key, data)
          store.put(("users",), user_id, dict(user_info))
          return "Successfully saved user info."

      agent: Runnable = create_agent(
          model="ollama:north-mini-code-1.0",
          tools=[save_user_info],
          store=store,
          context_schema=Context,
      )

      # Run the agent
      agent.invoke(
          {"messages": [{"role": "user", "content": "My name is John Smith"}]},
          # user_id passed in context to identify whose information is being updated
          context=Context(user_id="user_123"),
      )

      # You can access the store directly to get the value
      item = store.get(("users",), "user_123")
      ```

**PostgreSQL**

    ```python
    from dataclasses import dataclass

    from langchain.agents import create_agent
    from langchain.tools import ToolRuntime, tool
    from langchain_core.runnables import Runnable
    from langgraph.store.postgres import PostgresStore  # type: ignore[import-not-found]
    from typing_extensions import TypedDict

    @dataclass
    class Context:
        user_id: str

    class UserInfo(TypedDict):
        name: str

    @tool
    def save_user_info(user_info: UserInfo, runtime: ToolRuntime[Context]) -> str:
        """Save user info."""
        assert runtime.store is not None
        runtime.store.put(("users",), runtime.context.user_id, dict(user_info))
        return "Successfully saved user info."

    DB_URI = "postgresql://postgres:postgres@localhost:5432/postgres?sslmode=disable"

    with PostgresStore.from_conn_string(DB_URI) as store:
        store.setup()
        agent: Runnable = create_agent(
            "claude-sonnet-4-6",
            tools=[save_user_info],
            store=store,
            context_schema=Context,
        )

        agent.invoke(
            {"messages": [{"role": "user", "content": "My name is John Smith"}]},
            context=Context(user_id="user_123"),
        )
    ```
