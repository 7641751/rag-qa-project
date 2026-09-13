<!-- source: https://docs.langchain.com/oss/python/langchain/test/index.md | section: /oss/python/langchain | fetched: 2026-09-09 -->

# Test

> Strategies for testing LangChain agents, including unit tests, integration tests, and trajectory evaluations.

Agentic applications let an LLM decide its own next steps to solve a problem. That flexibility is powerful, but the model's black-box nature makes it hard to predict how a tweak in one part of your agent will affect the whole. To build production-ready agents, thorough testing is essential.

There are a few approaches to testing your agents:

* **Unit tests** exercise small, deterministic pieces of your agent in isolation using in-memory fakes so you can assert exact behavior quickly and deterministically.
* **Integration tests** test the agent using real network calls to confirm that components work together, credentials and schemas line up, and latency is acceptable.
* **Evals** use evaluators to assess your agent's execution trajectory, either via deterministic matching or an LLM judge.

Agentic applications tend to lean more on integration because they chain multiple components together and must deal with flakiness due to the nondeterministic nature of LLMs.

  Run evaluations at scale, track results over time, and compare experiments with [LangSmith](https://smith.langchain.com?utm_source=docs\&utm_medium=cta\&utm_campaign=langsmith-signup\&utm_content=oss-langchain-test-index). See [Evaluate an LLM application](/langsmith/evaluate-llm-application) to get started.

  - [Unit testing](/oss/python/langchain/test/unit-testing)
    Mock chat models and use in-memory persistence to test agent logic without API calls.

  - [Integration testing](/oss/python/langchain/test/integration-testing)
    Test your agent with real LLM APIs. Organize tests, manage keys, handle flakiness, and control costs.

  - [Evals](/oss/python/langchain/test/evals)
    Evaluate agent trajectories with deterministic matching or LLM-as-judge evaluators.
