<!-- source: https://docs.langchain.com/oss/python/langchain/errors/MODEL_AUTHENTICATION.md | section: /oss/python/langchain | fetched: 2026-09-09 -->

# MODEL_AUTHENTICATION

  Currently only used in `langchainjs` (JavaScript/TypeScript).

Your model provider is denying you access to their service.

This error typically occurs when there's an issue with your authentication credentials or API keys.

## Troubleshooting

* Confirm that your API key or authentication credentials are accurate and valid.
* If using environment-based authentication, verify:
  * The variable name is spelled correctly
  * The variable contains an assigned value
  * Third-party packages like `dotenv` haven't interfered with loading
* If using a proxy or non-standard endpoint, make sure that your custom provider does not expect an alternative authentication scheme.
* Bypass environment variable issues by passing credentials explicitly:

```python
from langchain_openai import ChatOpenAI

model = ChatOpenAI(api_key="YOUR_KEY_HERE")
```
