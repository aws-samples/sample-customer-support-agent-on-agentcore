"""AgentCore Observability - Manual OTEL SDK for AgentCore Evaluations

Replaces ADOT auto-instrumentation with manual OTEL SDK that directly creates
spans and log records in the format AgentCore Evaluations expects.

Three OTEL pipelines:
1. Traces → X-Ray (SigV4-signed OTLP)
2. Logs → CloudWatch Logs (SigV4-signed OTLP, two scopes)
3. Metrics → CloudWatch EMF (optional, for GenAI Observability dashboard)

Two logger scopes:
- strands.telemetry.tracer: I/O summary per query
- opentelemetry.instrumentation.botocore.bedrock-runtime: per-message events
"""

import json
import logging
import os
import re
import time
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-initialized globals (set by init_otel)
# ---------------------------------------------------------------------------
OTEL_AVAILABLE = False
_tracer = None
_struct_logger = None     # strands.telemetry.tracer scope
_bedrock_logger = None    # opentelemetry.instrumentation.botocore.bedrock-runtime scope
_resource = None
_provider = None
_log_provider = None

# Scope names (must match Strands exactly)
STRANDS_SCOPE = "strands.telemetry.tracer"
BEDROCK_SCOPE = "opentelemetry.instrumentation.botocore.bedrock-runtime"
BEDROCK_SCHEMA_URL = "https://opentelemetry.io/schemas/1.30.0"


def init_otel(
    service_name: str = "xxxx-agent",
    runtime_id: str = "",
    region: str = "",
) -> bool:
    """Initialize manual OTEL SDK for traces and logs.

    Must be called once at startup, BEFORE creating any ClaudeSDKClient.

    Args:
        service_name: Agent service name (used in resource attributes)
        runtime_id: AgentCore Runtime ID (for log group path)
        region: AWS region (auto-detected from OTEL endpoint if not set)

    Returns:
        True if initialization succeeded, False otherwise
    """
    global OTEL_AVAILABLE, _tracer, _struct_logger, _bedrock_logger
    global _resource, _provider, _log_provider

    # Guard against multiple initializations (AgentCore forks workers)
    if OTEL_AVAILABLE:
        return True

    try:
        import botocore.session
        import requests as _requests
        from aws_requests_auth.boto_utils import BotoAWSRequestsAuth
        from opentelemetry import baggage, context, trace
        from opentelemetry._logs import set_logger_provider, SeverityNumber
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs._internal import LogRecord
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from amazon.opentelemetry.distro.exporter.otlp.aws.traces.otlp_aws_span_exporter import (
            OTLPAwsSpanExporter,
        )
    except ImportError as e:
        logger.info(f"OTEL dependencies not available: {e}")
        return False

    # Resolve region
    if not region:
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        m = re.search(r"\.([a-z]{2}-[a-z]+-\d)\.amazonaws\.com", endpoint)
        region = m.group(1) if m else os.environ.get("AWS_REGION", "us-west-2")

    if not runtime_id:
        runtime_id = os.environ.get("AGENTCORE_RUNTIME_ID", f"{service_name}-0000000000")

    log_group = f"/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT"

    # --- Resource ---
    _resource = Resource.create({
        "service.name": f"{runtime_id.split('-')[0]}.DEFAULT" if "-" in runtime_id else f"{service_name}.DEFAULT",
        "aws.local.service": f"{runtime_id.split('-')[0]}.DEFAULT" if "-" in runtime_id else f"{service_name}.DEFAULT",
        "aws.service.type": "gen_ai_agent",
        "aws.log.group.names": log_group,
        "telemetry.auto.version": "0.15.0-aws",
    })

    # --- BaggageSpanProcessor: propagate session.id to all spans ---
    class _BaggageSpanProcessor(SpanProcessor):
        def on_start(self, span, parent_context=None):
            ctx = parent_context or context.get_current()
            for key, value in baggage.get_all(ctx).items():
                span.set_attribute(key, value)

    # --- Traces → X-Ray ---
    _provider = TracerProvider(resource=_resource)
    _provider.add_span_processor(_BaggageSpanProcessor())
    traces_endpoint = f"https://xray.{region}.amazonaws.com/v1/traces"
    _provider.add_span_processor(
        BatchSpanProcessor(
            OTLPAwsSpanExporter(
                aws_region=region,
                session=botocore.session.Session(),
                endpoint=traces_endpoint,
            )
        )
    )
    trace.set_tracer_provider(_provider)
    _tracer = trace.get_tracer(STRANDS_SCOPE)

    # --- Logs → CloudWatch Logs (via boto3 PutLogEvents, not OTLP) ---
    # OTLP /v1/logs path may not work through CloudWatch Logs VPC endpoint.
    # Use a custom exporter that calls PutLogEvents directly via boto3.
    import boto3 as _boto3
    from opentelemetry.sdk._logs.export import LogExporter, LogExportResult

    _cw_logs_client = _boto3.client("logs", region_name=region)
    _otel_log_stream = "otel-rt-logs"

    # Ensure the log stream exists (ADOT used to create it, but we removed ADOT)
    try:
        _cw_logs_client.create_log_stream(
            logGroupName=log_group, logStreamName=_otel_log_stream)
        logger.info(f"Created log stream: {log_group}/{_otel_log_stream}")
    except _cw_logs_client.exceptions.ResourceAlreadyExistsException:
        pass
    except Exception as e:
        logger.warning(f"Could not create log stream: {e}")

    class _CloudWatchDirectExporter(LogExporter):
        """Export OTEL log records directly to CloudWatch Logs via PutLogEvents."""

        def __init__(self, log_group: str, log_stream: str, region: str):
            self._log_group = log_group
            self._log_stream = log_stream
            self._cw = _boto3.client("logs", region_name=region)
            self._seq_token = None

        def export(self, batch):
            if not batch:
                return LogExportResult.SUCCESS
            try:
                events = []
                for record in batch:
                    # BatchLogRecordProcessor passes ReadableLogRecord objects
                    # which wrap the actual LogRecord in .log_record
                    lr = record.log_record if hasattr(record, 'log_record') else record

                    body = lr.body
                    attrs = dict(lr.attributes) if lr.attributes else {}
                    scope_name = record.instrumentation_scope.name if record.instrumentation_scope else ""
                    trace_id = format(lr.trace_id, '032x') if lr.trace_id else ""
                    span_id = format(lr.span_id, '016x') if lr.span_id else ""

                    doc = {
                        "resource": {"attributes": dict(_resource.attributes) if _resource else {}},
                        "scope": {"name": scope_name},
                        "timeUnixNano": lr.timestamp or int(time.time_ns()),
                        "observedTimeUnixNano": lr.observed_timestamp or int(time.time_ns()),
                        "severityNumber": lr.severity_number.value if lr.severity_number else 9,
                        "severityText": lr.severity_text or "",
                        "body": body,
                        "attributes": attrs,
                        "flags": lr.trace_flags or 1,
                        "traceId": trace_id,
                        "spanId": span_id,
                    }

                    events.append({
                        "timestamp": (lr.timestamp or int(time.time_ns())) // 1_000_000,
                        "message": json.dumps(doc, ensure_ascii=False, default=str),
                    })

                # Sort by timestamp (required by PutLogEvents)
                events.sort(key=lambda e: e["timestamp"])

                kwargs = {
                    "logGroupName": self._log_group,
                    "logStreamName": self._log_stream,
                    "logEvents": events,
                }
                if self._seq_token:
                    kwargs["sequenceToken"] = self._seq_token

                resp = self._cw.put_log_events(**kwargs)
                self._seq_token = resp.get("nextSequenceToken")
                return LogExportResult.SUCCESS
            except Exception as e:
                logger.warning(f"CloudWatch log export failed: {e}")
                # Reset sequence token on error
                self._seq_token = None
                return LogExportResult.FAILURE

        def shutdown(self):
            pass

        def force_flush(self, timeout_millis=None):
            return True

    _log_provider = LoggerProvider(resource=_resource)
    _log_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            _CloudWatchDirectExporter(
                log_group=log_group,
                log_stream="otel-rt-logs",
                region=region,
            )
        )
    )
    set_logger_provider(_log_provider)

    _struct_logger = _log_provider.get_logger(STRANDS_SCOPE)
    _bedrock_logger = _log_provider.get_logger(BEDROCK_SCOPE, schema_url=BEDROCK_SCHEMA_URL)

    OTEL_AVAILABLE = True
    logger.info(
        f"Manual OTEL SDK initialized: region={region}, "
        f"traces→xray, logs→{log_group}"
    )
    return True


def shutdown_otel():
    """Flush and shut down OTEL providers. Call on process exit."""
    if _provider:
        try:
            _provider.shutdown()
        except Exception:
            pass
    if _log_provider:
        try:
            _log_provider.shutdown()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tracer access
# ---------------------------------------------------------------------------

class _NoOpSpan:
    def set_attribute(self, key, value): pass
    def set_status(self, status, description=None): pass
    def add_event(self, name, attributes=None): pass
    def record_exception(self, exception): pass
    def end(self): pass
    def get_span_context(self): return None
    def __enter__(self): return self
    def __exit__(self, *args): pass


class _NoOpTracer:
    @contextmanager
    def start_as_current_span(self, name, **kwargs):
        yield _NoOpSpan()
    def start_span(self, name, **kwargs):
        return _NoOpSpan()


def get_tracer(name: str = STRANDS_SCOPE) -> Any:
    """Get the manual tracer (or no-op if OTEL not initialized)."""
    return _tracer if _tracer else _NoOpTracer()


# ---------------------------------------------------------------------------
# Log emission helpers
# ---------------------------------------------------------------------------

def _sanitize(value: str) -> str:
    """Replace surrogate characters that the OTLP exporter cannot encode."""
    return value.encode("utf-8", errors="replace").decode("utf-8")


def emit_structured_log(body: dict, attributes: dict | None = None):
    """Emit a structured I/O summary log under the strands.telemetry.tracer scope.

    This is the main log the evaluator reads for user_query and agent_response.
    """
    if not _struct_logger or not OTEL_AVAILABLE:
        return

    from opentelemetry import baggage, trace
    from opentelemetry.sdk._logs._internal import LogRecord
    from opentelemetry._logs import SeverityNumber

    span = trace.get_current_span()
    span_ctx = span.get_span_context() if span else None

    merged = dict(attributes) if attributes else {}
    merged.setdefault("event.name", STRANDS_SCOPE)
    sid = baggage.get_baggage("session.id")
    if sid:
        merged["session.id"] = sid

    record = LogRecord(
        timestamp=int(time.time_ns()),
        body=body,
        severity_number=SeverityNumber.INFO,
        severity_text="",
        trace_id=span_ctx.trace_id if span_ctx and span_ctx.is_valid else 0,
        span_id=span_ctx.span_id if span_ctx and span_ctx.is_valid else 0,
        trace_flags=span_ctx.trace_flags if span_ctx and span_ctx.is_valid else 0,
        attributes=merged or None,
    )
    _struct_logger.emit(record)


def emit_bedrock_log(body: dict, event_name: str, span_context: tuple | None = None):
    """Emit a per-message log under the bedrock-runtime scope.

    Args:
        body: Log body dict (e.g. {"content": [{"text": "..."}]})
        event_name: One of gen_ai.user.message, gen_ai.assistant.message,
                    gen_ai.choice, gen_ai.tool.message
        span_context: Optional (trace_id, span_id, trace_flags) tuple
                      for when called from async hooks outside active span
    """
    if not _bedrock_logger or not OTEL_AVAILABLE:
        return

    from opentelemetry import trace
    from opentelemetry.sdk._logs._internal import LogRecord
    from opentelemetry._logs import SeverityNumber

    if span_context:
        tid, sid, flags = span_context
    else:
        span = trace.get_current_span()
        span_ctx = span.get_span_context() if span else None
        tid = span_ctx.trace_id if span_ctx and span_ctx.is_valid else 0
        sid = span_ctx.span_id if span_ctx and span_ctx.is_valid else 0
        flags = span_ctx.trace_flags if span_ctx and span_ctx.is_valid else 0

    # Add session.id to bedrock events too (required for CloudWatch query filter)
    from opentelemetry import baggage as _bag
    attrs = {"event.name": event_name, "gen_ai.system": "aws.bedrock"}
    _sid = _bag.get_baggage("session.id")
    if _sid:
        attrs["session.id"] = _sid

    record = LogRecord(
        timestamp=int(time.time_ns()),
        body=body,
        severity_number=SeverityNumber.INFO,
        severity_text="",
        trace_id=tid,
        span_id=sid,
        trace_flags=flags,
        attributes=attrs,
    )
    _bedrock_logger.emit(record)


# ---------------------------------------------------------------------------
# Span context capture (for tool hooks running outside active span)
# ---------------------------------------------------------------------------

# tool_use_id → (trace_id, span_id, trace_flags)
_tool_span_contexts: dict[str, tuple] = {}


def capture_tool_span_context(tool_use_id: str):
    """Capture current span context for a tool use block.

    Call this in the main streaming loop when a ToolUseBlock is received,
    so that post_tool_use hooks can emit logs with the correct span context.
    """
    if not OTEL_AVAILABLE:
        return
    from opentelemetry import trace
    span = trace.get_current_span()
    if span:
        sc = span.get_span_context()
        if sc and sc.is_valid:
            _tool_span_contexts[tool_use_id] = (sc.trace_id, sc.span_id, sc.trace_flags)


def pop_tool_span_context(tool_use_id: str) -> tuple | None:
    """Retrieve and remove the saved span context for a tool use."""
    return _tool_span_contexts.pop(tool_use_id, None)


# ---------------------------------------------------------------------------
# Span helpers (kept for compatibility with existing code)
# ---------------------------------------------------------------------------

def trace_agent_invocation(
    tracer: Any,
    parent_id: str,
    session_id: str,
    model: str,
    prompt_preview: str = "",
):
    """Create a span for the entire agent invocation."""
    # Set session.id in baggage for BaggageSpanProcessor
    if OTEL_AVAILABLE:
        from opentelemetry import baggage, context
        ctx = baggage.set_baggage("session.id", session_id)
        context.attach(ctx)

    span = tracer.start_as_current_span(
        f"invoke_agent xxxx-agent",
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.system": "aws.bedrock",
            "gen_ai.agent.name": "xxxx-agent",
            "gen_ai.request.model": model,
            "user.input": _sanitize(prompt_preview[:200]),
        },
    )
    return span


@contextmanager
def trace_tool_call(tracer: Any, tool_name: str, tool_input: dict | None = None):
    """Create a span for a tool call."""
    input_str = ""
    if tool_input:
        try:
            input_str = json.dumps(tool_input, ensure_ascii=False)[:500]
        except (TypeError, ValueError):
            input_str = str(tool_input)[:500]

    with tracer.start_as_current_span(
        f"tool.{tool_name}",
        attributes={
            "gen_ai.tool.name": tool_name,
            "gen_ai.operation.name": "tool_call",
            "tool.input": input_str,
        },
    ) as span:
        start_time = time.monotonic()
        try:
            yield span
            duration_ms = (time.monotonic() - start_time) * 1000
            span.set_attribute("tool.duration_ms", duration_ms)
            if OTEL_AVAILABLE:
                from opentelemetry.trace import StatusCode
                span.set_status(StatusCode.OK)
        except Exception as e:
            if OTEL_AVAILABLE:
                from opentelemetry.trace import StatusCode
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
            raise


@contextmanager
def trace_memory_operation(tracer: Any, operation: str, **attributes):
    """Create a span for a memory operation (search/save)."""
    with tracer.start_as_current_span(
        f"memory.{operation}",
        attributes={
            "memory.operation": operation,
            **{f"memory.{k}": str(v) for k, v in attributes.items()},
        },
    ) as span:
        start_time = time.monotonic()
        try:
            yield span
            duration_ms = (time.monotonic() - start_time) * 1000
            span.set_attribute("memory.duration_ms", duration_ms)
            if OTEL_AVAILABLE:
                from opentelemetry.trace import StatusCode
                span.set_status(StatusCode.OK)
        except Exception as e:
            if OTEL_AVAILABLE:
                from opentelemetry.trace import StatusCode
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
            raise


# Legacy helper kept for backward compatibility
def add_trace_event(span: Any, event_name: str, attributes: dict[str, Any] | None = None):
    """Add an event to a span, safely handling attribute values."""
    if attributes is None:
        attributes = {}
    safe = {}
    for k, v in attributes.items():
        if isinstance(v, (str, int, float, bool)):
            safe[k] = v
        elif v is None:
            safe[k] = ""
        else:
            try:
                safe[k] = json.dumps(v, ensure_ascii=False)[:2000]
            except (TypeError, ValueError):
                safe[k] = str(v)[:2000]
    span.add_event(event_name, attributes=safe)
