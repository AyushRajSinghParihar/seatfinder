"""Thin HTTP layer over curl_cffi with optional fingerprint rotation."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from curl_cffi import requests as curl_requests

from .errors import BlockedError, UpstreamError


@dataclass(slots=True)
class HttpResponse:
    status_code: int
    text: str
    headers: Mapping[str, str]
    url: str

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise UpstreamError(
                "Upstream returned invalid JSON",
                detail=f"{self.url}: {exc}",
            ) from exc


class HttpClient:
    """A cookie-preserving client with per-request fingerprint rotation."""

    def __init__(
        self,
        *,
        profiles: Iterable[str] = (),
        timeout: float = 30.0,
        proxy: str | None = None,
        headers: Mapping[str, str] | None = None,
        warmup_url: str | None = None,
    ) -> None:
        self.profiles = [p for p in profiles if p]
        self.timeout = timeout
        self.proxy = proxy
        self.headers = dict(headers or {})
        self._profile_index = 0
        self._session = self._make_session(self.profiles[0] if self.profiles else None)
        if warmup_url:
            self.get(warmup_url, max_attempts=1)

    def _make_session(self, profile: str | None):
        kwargs: dict[str, Any] = {}
        if profile:
            kwargs["impersonate"] = profile
        if self.proxy:
            kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
        return curl_requests.Session(**kwargs)

    def rotate(self) -> None:
        if len(self.profiles) <= 1:
            return
        self._profile_index = (self._profile_index + 1) % len(self.profiles)
        self._session = self._make_session(self.profiles[self._profile_index])

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        data: bytes | str | None = None,
        json_body: Any = None,
        max_attempts: int = 1,
        rotate_on: tuple[int, ...] = (403, 429),
        backoff: float = 2.0,
        allow_statuses: tuple[int, ...] = (),
    ) -> HttpResponse:
        merged_headers = {**self.headers, **(headers or {})}
        last_error: Exception | None = None
        response: HttpResponse | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                raw = self._session.request(
                    method,
                    url,
                    params=dict(params or {}),
                    headers=merged_headers,
                    data=data,
                    json=json_body,
                    timeout=self.timeout,
                )
                response = HttpResponse(
                    status_code=raw.status_code,
                    text=raw.text,
                    headers=dict(raw.headers),
                    url=str(raw.url),
                )
            except Exception as exc:  # curl_cffi raises several transport errors
                last_error = exc
                if attempt < max_attempts:
                    time.sleep(backoff * attempt)
                    continue
                break

            if response.status_code in allow_statuses:
                return response
            if 200 <= response.status_code < 300:
                return response
            if response.status_code in rotate_on and attempt < max_attempts:
                self.rotate()
                time.sleep(backoff * attempt)
                continue
            break

        if response is None:
            raise UpstreamError(
                f"Request failed: {last_error}",
                hint="Check your network connection or proxy",
                detail=url,
            )
        if response.status_code in (403, 429):
            raise BlockedError(
                f"Upstream blocked the request (HTTP {response.status_code})",
                hint="Use an Indian residential connection or set --proxy; BookMyShow needs a browser TLS profile",
                detail=url,
            )
        raise UpstreamError(
            f"Unexpected upstream response: HTTP {response.status_code}",
            detail=url,
        )

    def get(self, url: str, **kwargs: Any) -> HttpResponse:
        return self.request("GET", url, **kwargs)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.get(url, **kwargs).json()

    def post_json(self, url: str, body: Any, **kwargs: Any) -> HttpResponse:
        headers = dict(kwargs.pop("headers", {}))
        headers.setdefault("Content-Type", "application/json")
        return self.request("POST", url, headers=headers, json_body=body, **kwargs)


def new_request_id() -> str:
    return str(uuid.uuid4())


def multipart_fields(fields: Mapping[str, str]) -> tuple[bytes, str]:
    """Encode fields as multipart/form-data without extra dependencies."""

    boundary = f"----seatfinder{uuid.uuid4().hex}"
    lines: list[bytes] = []
    for name, value in fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b"")
        lines.append(str(value).encode())
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"
