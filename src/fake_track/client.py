from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings

HttpMethod = Literal["GET", "POST"]


class ApiError(RuntimeError):
    pass


@dataclass(slots=True)
class ApiResponse:
    code: int
    message: str
    data: Any
    raw: dict[str, Any]


class _ApiBase(StrEnum):
    XCXAPI = "xcxapi"
    XCXTAPI = "xcxtapi"


@dataclass(slots=True, frozen=True)
class _Endpoint:
    method: HttpMethod
    path: str
    base: _ApiBase = _ApiBase.XCXAPI


_LOGIN = _Endpoint("POST", "/userLogin/")
_RAND_RUN_INFO = _Endpoint("GET", "/activity/randrunInfo", _ApiBase.XCXTAPI)
_CREATE_LINE = _Endpoint("POST", "/createLine/")
_CHECK_RECORD = _Endpoint("POST", "/checkRecord/")
_UPDATE_RECORD = _Endpoint("POST", "/updateRecordNew/")
_UPLOAD_PATH_POINT = _Endpoint("POST", "/uploadPathPointV3/")
_RECORD_INFO = _Endpoint("GET", "/recordInfo/")
_GET_PATH_POINTS = _Endpoint("GET", "/GetPathPoints/")
_RUNNING_DATA = _Endpoint("GET", "/RunningData/")


class CampusRunClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = self._build_session()
        self.session_id = ""

    def authenticate_user(self) -> ApiResponse:
        response = self._request(
            _LOGIN,
            {
                "iphone": self.settings.phone,
                "password": self.settings.password,
            },
        )
        if isinstance(response.data, dict):
            session_key = response.data.get("session_keys")
            if session_key:
                self.session_id = str(session_key)
        return response

    def fetch_route_points(self, lat: float, lng: float) -> ApiResponse:
        return self._request(_RAND_RUN_INFO, {"lat": lat, "lng": lng})

    def create_run_record(
        self,
        student_id: int,
        pass_points: list[dict[str, Any]],
    ) -> ApiResponse:
        return self._request(
            _CREATE_LINE,
            {
                "student_id": student_id,
                "pass_point": pass_points,
            },
        )

    def validate_run_payload(self, encrypted_payload: str) -> ApiResponse:
        return self._request(_CHECK_RECORD, {"a": encrypted_payload})

    def submit_run_summary(self, encrypted_payload: str) -> ApiResponse:
        return self._request(_UPDATE_RECORD, {"a": encrypted_payload})

    def upload_path_batch(self, encrypted_batch_payload: str) -> ApiResponse:
        return self._request(_UPLOAD_PATH_POINT, {"img_url": encrypted_batch_payload})

    def fetch_record_info(self, record_id: int) -> ApiResponse:
        return self._request(_RECORD_INFO, {"id": record_id})

    def fetch_path_points(self, record_id: int) -> ApiResponse:
        return self._request(_GET_PATH_POINTS, {"record_id": record_id})

    def fetch_run_counts(self, student_id: int) -> ApiResponse:
        return self._request(_RUNNING_DATA, {"id": student_id})

    def _request(
        self,
        endpoint: _Endpoint,
        payload: Mapping[str, Any] | None = None,
    ) -> ApiResponse:
        url = self._build_url(endpoint)
        kwargs = self._request_kwargs(endpoint, payload)

        try:
            response = self.session.request(endpoint.method, url, **kwargs)
        except requests.RequestException as exc:
            raise ApiError(f"Request failed for {endpoint.path}: {exc}") from exc

        if response.status_code >= 400:
            raise ApiError(
                f"HTTP {response.status_code} for {endpoint.path}: "
                f"{response.text[:200]}"
            )

        body = self._parse_json_body(endpoint, response)
        self._refresh_session_id_from_cookie()
        return self._parse_api_response(endpoint, body)

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry_count = max(1, self.settings.network.retry_count)
        if retry_count <= 1:
            return session

        retries = Retry(
            total=retry_count - 1,
            connect=retry_count - 1,
            read=retry_count - 1,
            status=retry_count - 1,
            backoff_factor=0.6,
            status_forcelist=tuple(range(500, 600)),
            allowed_methods=None,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _build_url(self, endpoint: _Endpoint) -> str:
        path = endpoint.path if endpoint.path.startswith("/") else f"/{endpoint.path}"
        if endpoint.base is _ApiBase.XCXTAPI:
            return f"{self.settings.network.base_url_root}/{endpoint.base}{path}"
        return f"{self.settings.network.base_url_xcxapi}{path}"

    def _headers(self) -> dict[str, str]:
        network = self.settings.network
        return {
            "Content-Type": "application/json",
            "Authorization": "",
            "X-Session-ID": self.session_id,
            "charset": "utf-8",
            "Referer": network.referer,
            "User-Agent": network.user_agent,
        }

    def _request_kwargs(
        self,
        endpoint: _Endpoint,
        payload: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "headers": self._headers(),
            "timeout": self.settings.network.timeout_sec,
        }
        key = "params" if endpoint.method == "GET" else "json"
        kwargs[key] = dict(payload or {})
        return kwargs

    @staticmethod
    def _parse_json_body(
        endpoint: _Endpoint,
        response: requests.Response,
    ) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise ApiError(f"Invalid JSON response from {endpoint.path}") from exc

        if not isinstance(body, dict):
            raise ApiError(
                f"Invalid JSON object from {endpoint.path}: {type(body).__name__}"
            )
        return body

    def _refresh_session_id_from_cookie(self) -> None:
        cookie_session = self.session.cookies.get("sessionid")
        if cookie_session:
            self.session_id = cookie_session

    @staticmethod
    def _parse_api_response(
        endpoint: _Endpoint,
        body: dict[str, Any],
    ) -> ApiResponse:
        try:
            code = int(body.get("code", 0))
        except (TypeError, ValueError) as exc:
            raise ApiError(f"Invalid API code from {endpoint.path}") from exc

        message = str(body.get("message") or body.get("msg") or "")
        data = body.get("data")

        if code == -2:
            raise ApiError("Session invalid (code=-2)")
        if code != 1:
            raise ApiError(message or f"API error code={code} endpoint={endpoint.path}")
        return ApiResponse(code=code, message=message, data=data, raw=body)
