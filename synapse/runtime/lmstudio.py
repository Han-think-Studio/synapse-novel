"""Dry-run and response normalization adapter for LM Studio's local API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urljoin

from synapse.runtime.contracts import (
    PreparedRequest,
    RuntimeAdapterError,
    RuntimeErrorKind,
    RuntimeRequest,
    RuntimeResponse,
    copy_json_data,
)
from synapse.runtime.http_transport import JsonTransport, UrllibJsonTransport
from synapse.runtime.policy import validate_endpoint_policy


class LMStudioAdapter:
    """Prepare OpenAI-compatible LM Studio requests without network I/O."""

    provider = "lmstudio"
    execution_scope = "service"
    network_scope = "loopback"
    data_egress = "local_only"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234/v1",
        *,
        responses_schema_supported: bool = False,
    ) -> None:
        if type(responses_schema_supported) is not bool:
            raise RuntimeAdapterError(
                RuntimeErrorKind.CONFIGURATION, "Responses 형식 지원 여부는 bool이어야 합니다."
            )
        self.responses_schema_supported = responses_schema_supported
        normalized = base_url.strip()
        if not normalized:
            raise RuntimeAdapterError(
                RuntimeErrorKind.CONFIGURATION, "LM Studio base_url이 비어 있습니다."
            )
        validate_endpoint_policy(
            normalized,
            network_scope=self.network_scope,
            data_egress=self.data_egress,
        )
        self.base_url = normalized.rstrip("/") + "/"

    def prepare(self, request: RuntimeRequest) -> PreparedRequest:
        if isinstance(request.parameters.get("reasoning"), Mapping):
            return self._prepare_responses(request)
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [dict(message) for message in request.messages],
        }
        body.update(dict(request.parameters))
        if request.response_schema is not None:
            # LM Studio's OpenAI-compatible server only accepts "json_schema" or
            # "text" for response_format.type -- verified live 2026-09-03
            # (a bare {"type": "json_object"} request is rejected with HTTP 400).
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "synapse_structured_output",
                    "strict": True,
                    "schema": copy_json_data(request.response_schema),
                },
            }
        return PreparedRequest(
            request_id=request.request_id,
            provider=self.provider,
            method="POST",
            url=urljoin(self.base_url, "chat/completions"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body=body,
        )

    def _prepare_responses(self, request: RuntimeRequest) -> PreparedRequest:
        if request.response_schema is not None and not self.responses_schema_supported:
            raise RuntimeAdapterError(
                RuntimeErrorKind.CONFIGURATION,
                "이 연결은 Responses의 JSON 스키마 지원이 확인되지 않았습니다. "
                "구조화 요청은 chat/completions를 사용하거나 해당 서버 지원을 먼저 확인하세요.",
            )
        # Explicit nested reasoning selects LM Studio's documented Responses API.
        parameters = dict(request.parameters)
        reasoning = dict(parameters.pop("reasoning"))
        max_tokens = parameters.pop("max_tokens", None)
        body: dict[str, Any] = {
            "model": request.model,
            "input": [dict(message) for message in request.messages],
            **parameters,
            "reasoning": reasoning,
            "store": False,
        }
        if max_tokens is not None:
            body["max_output_tokens"] = max_tokens
        if request.response_schema is not None:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "synapse_structured_output",
                    "strict": True,
                    "schema": copy_json_data(request.response_schema),
                }
            }
        return PreparedRequest(
            request_id=request.request_id,
            provider=self.provider,
            method="POST",
            url=urljoin(self.base_url, "responses"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body=body,
        )

    def _normalize_responses(
        self, request: RuntimeRequest, payload: Mapping[str, Any]
    ) -> RuntimeResponse:
        status = payload.get("status")
        output = payload.get("output")
        if (
            not isinstance(status, str)
            or status not in {"completed", "incomplete"}
            or not isinstance(output, list)
            or payload.get("error") is not None
        ):
            raise RuntimeAdapterError(
                RuntimeErrorKind.MALFORMED_RESPONSE,
                "LM Studio Responses 응답이 끝나지 않았거나 형식이 잘못됐습니다.",
            )
        if request.response_schema is not None and "text" in payload:
            metadata = payload["text"]
            response_format = metadata.get("format") if isinstance(metadata, Mapping) else None
            if (
                not isinstance(response_format, Mapping)
                or response_format.get("type") != "json_schema"
            ):
                raise RuntimeAdapterError(
                    RuntimeErrorKind.MALFORMED_RESPONSE,
                    "LM Studio가 요청한 JSON 스키마를 적용하지 않았습니다. 서버의 형식 지원을 확인하세요.",
                    invocation_finished=status == "completed",
                )
        texts: list[str] = []
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "message":
                continue
            if (
                item.get("role", "assistant") != "assistant"
                or item.get("status", "completed") != "completed"
            ):
                raise RuntimeAdapterError(
                    RuntimeErrorKind.MALFORMED_RESPONSE,
                    "LM Studio Responses 최종 메시지의 완료 상태가 잘못됐습니다.",
                )
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if (
                    isinstance(part, Mapping)
                    and part.get("type") == "output_text"
                    and isinstance(part.get("text"), str)
                ):
                    texts.append(part["text"])
        if not texts or not any(texts):
            raise RuntimeAdapterError(
                RuntimeErrorKind.MALFORMED_RESPONSE,
                "LM Studio Responses 응답에 최종 본문이 없습니다. 추론은 본문으로 쓰지 않습니다.",
            )
        raw_usage = payload.get("usage")
        usage = dict(raw_usage) if isinstance(raw_usage, Mapping) else {}
        for source, target in (
            ("input_tokens", "prompt_tokens"),
            ("output_tokens", "completion_tokens"),
        ):
            if source in usage:
                usage[target] = usage[source]
        return RuntimeResponse(
            request_id=request.request_id,
            provider=self.provider,
            model=str(payload.get("model") or request.model),
            text="".join(texts),
            raw=payload,
            usage=usage,
            finish_reason="stop" if status == "completed" else "length",
        )

    def normalize(self, request: RuntimeRequest, payload: Mapping[str, Any]) -> RuntimeResponse:
        if isinstance(request.parameters.get("reasoning"), Mapping):
            return self._normalize_responses(request, payload)
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise RuntimeAdapterError(
                RuntimeErrorKind.MALFORMED_RESPONSE,
                "LM Studio 응답에 choices[0]가 없습니다.",
            )
        message = choices[0].get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise RuntimeAdapterError(
                RuntimeErrorKind.MALFORMED_RESPONSE,
                "LM Studio 응답의 choices[0].message.content가 문자열이 아닙니다.",
            )
        usage = payload.get("usage", {})
        if not isinstance(usage, Mapping):
            usage = {}
        finish_reason = choices[0].get("finish_reason")
        return RuntimeResponse(
            request_id=request.request_id,
            provider=self.provider,
            model=str(payload.get("model") or request.model),
            text=message["content"],
            raw=payload,
            usage=usage,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
        )

    def normalize_creative(
        self,
        *,
        stage: str,
        response: RuntimeResponse | Mapping[str, Any] | str,
        seed_hash: str,
        global_map: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Pass an existing LM Studio result through the creative boundary."""
        from synapse.core.creative_runtime_bridge import normalize_adapter_creative_response

        return normalize_adapter_creative_response(
            stage=stage, response=response, seed_hash=seed_hash, global_map=global_map
        )

    def complete(
        self,
        request: RuntimeRequest,
        *,
        transport: JsonTransport | None = None,
    ) -> RuntimeResponse:
        """Explicitly send one request; never called during adapter construction."""

        prepared = self.prepare(request)
        payload = (transport or UrllibJsonTransport()).send(
            prepared,
            timeout_seconds=request.timeout_seconds,
        )
        return self.normalize(request, payload)
