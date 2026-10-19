"""Phoenix tracing, switched on only when enable() is called.

LLM inputs/outputs are hidden from spans; the pipeline records decisions only.
"""


def enable(project: str = "care-bot", endpoint: str = "http://localhost:6006/v1/traces"):
    from openinference.instrumentation import TraceConfig
    from openinference.instrumentation.openai import OpenAIInstrumentor
    from phoenix.otel import register

    provider = register(project_name=project, endpoint=endpoint, batch=True, verbose=False)
    OpenAIInstrumentor().instrument(tracer_provider=provider,
                                    config=TraceConfig(hide_inputs=True, hide_outputs=True))
    return provider
