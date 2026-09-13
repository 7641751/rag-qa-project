<!-- source: https://docs.langchain.com/oss/python/langgraph/install.md | section: /oss/python/langgraph | fetched: 2026-09-09 -->

# Install LangGraph

To install the base LangGraph package:

  ```bash
  pip install -U langgraph
  ```

  ```bash
  uv add langgraph
  ```

To use LangGraph you will usually want to access LLMs and define tools.
You can do this however you see fit.

One way to do this (which we will use in the docs) is to use [LangChain](/oss/python/langchain/overview).

Install LangChain with:

  ```bash
  pip install -U langchain
  # Requires Python 3.10+
  ```

  ```bash
  uv add langchain
  # Requires Python 3.10+
  ```

To work with specific LLM provider packages, you will need install them separately.

Refer to the [integrations](/oss/python/integrations/providers/overview) page for provider-specific installation instructions.
