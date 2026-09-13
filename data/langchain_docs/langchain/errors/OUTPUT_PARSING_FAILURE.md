<!-- source: https://docs.langchain.com/oss/python/langchain/errors/OUTPUT_PARSING_FAILURE.md | section: /oss/python/langchain | fetched: 2026-09-09 -->

# OUTPUT_PARSING_FAILURE

An [output parser](https://reference.langchain.com/python/langchain_core/output_parsers/) was unable to handle model output as expected.

  Some prebuilt constructs like legacy LangChain agents and chains may use output parsers internally, so you may see this error even if you're not visibly instantiating and using an output parser.

## Troubleshooting

* Consider using tool calling or other structured output techniques if possible without an output parser to reliably output parseable values.
* Add more precise formatting instructions to your prompt.
* If you are using a smaller or less capable model, try using a more capable one.
