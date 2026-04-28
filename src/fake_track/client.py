from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings


class ApiError(RuntimeError):
    pass


@dataclass(slots=True)
class ApiResponse:
    code: int
    message: str
    data: Any
    raw: dict[str, Any]


class CampusRunClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = self._build_session()
        self.session_id = ""

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

    def _build_url(self, endpoint: str) -> str:
        endpoint = endpoint.strip()
        if endpoint.startswith(("http://", "https://")):
            return endpoint

        if endpoint.startswith("/"):
            # Keep canonical trailing slash for Django-style endpoints.
            return f"{self.settings.network.base_url_xcxapi}{endpoint}"

        if endpoint.startswith("xcxtapi/"):
            return f"{self.settings.network.base_url_root}/{endpoint}"

        return f"{self.settings.network.base_url_xcxapi}/{endpoint}"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": "",
            "X-Session-ID": self.session_id,
            "charset": "utf-8",
            "Referer": self.settings.network.referer,
            "User-Agent": self.settings.network.user_agent,
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        payload: dict | None = None,
    ) -> ApiResponse:
        url = self._build_url(endpoint)
        method_upper = method.upper()
        kwargs: dict[str, Any] = {
            "headers": self._headers(),
            "timeout": self.settings.network.timeout_sec,
        }
        if method_upper == "GET":
            kwargs["params"] = payload or {}
        else:
            kwargs["json"] = payload or {}

        try:
            response = self.session.request(method=method_upper, url=url, **kwargs)
        except requests.RequestException as exc:
            raise ApiError(f"Request failed for {endpoint}: {exc}") from exc

        if response.status_code >= 400:
            raise ApiError(
                f"HTTP {response.status_code} for {endpoint}: {response.text[:200]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ApiError(f"Invalid JSON response from {endpoint}") from exc

        cookie_session = self.session.cookies.get("sessionid")
        if cookie_session:
            self.session_id = cookie_session

        code = int(body.get("code", 0))
        message = str(body.get("message") or body.get("msg") or "")
        data = body.get("data")

        if code == -2:
            raise ApiError("Session invalid (code=-2)")
        if code != 1:
            raise ApiError(message or f"API error code={code} endpoint={endpoint}")
        return ApiResponse(code=code, message=message, data=data, raw=body)

    def authenticate_user(self) -> ApiResponse:
        response = self._request(
            "POST",
            "/userLogin/",
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
        return self._request(
            "GET",
            "xcxtapi/activity/randrunInfo",
            {"lat": lat, "lng": lng},
        )

    def create_run_record(
        self, student_id: int, pass_points: list[dict]
    ) -> ApiResponse:
        return self._request(
            "POST",
            "/createLine/",
            {
                "student_id": student_id,
                "pass_point": pass_points,
            },
        )

    def validate_run_payload(self, encrypted_payload: str) -> ApiResponse:
        return self._request("POST", "/checkRecord/", {"a": encrypted_payload})

    def submit_run_summary(self, encrypted_payload: str) -> ApiResponse:
        return self._request("POST", "/updateRecordNew/", {"a": encrypted_payload})

    def upload_path_batch(self, encrypted_batch_payload: str) -> ApiResponse:
        return self._request(
            "POST",
            "/uploadPathPointV3/",
            {"img_url": encrypted_batch_payload},
        )

    def fetch_record_info(self, record_id: int) -> ApiResponse:
        return self._request("GET", "/recordInfo/", {"id": record_id})

    def fetch_path_points(self, record_id: int) -> ApiResponse:
        return self._request("GET", "/GetPathPoints/", {"record_id": record_id})

    def fetch_run_counts(self, student_id: int) -> ApiResponse:
        return self._request(
            "GET",
            "/RunningData/",
            {"id": student_id},
        )
