import time
from dataclasses import dataclass
from typing import Any

import requests

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
        self.session = requests.Session()
        self.session_id = "1111"

    def _build_url(self, endpoint: str) -> str:
        endpoint = endpoint.strip()
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        if endpoint.startswith("xcxtapi/"):
            return f"{self.settings.base_url_root}/{endpoint}"
        if endpoint.startswith("/"):
            return f"{self.settings.base_url_xcxapi}{endpoint}"
        if "xcxtapi" in endpoint:
            return f"{self.settings.base_url_root}/{endpoint}"
        return f"{self.settings.base_url_xcxapi}/{endpoint}"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": "",
            "X-Session-ID": self.session_id or "1111",
            "charset": "utf-8",
            "Referer": self.settings.referer,
            "User-Agent": self.settings.user_agent,
        }

    def _request(
        self, method: str, endpoint: str, payload: dict | None = None
    ) -> ApiResponse:
        url = self._build_url(endpoint)
        last_error: Exception | None = None

        for attempt in range(1, self.settings.retry_count + 1):
            try:
                kwargs: dict[str, Any] = {
                    "headers": self._headers(),
                    "timeout": self.settings.timeout_sec,
                }
                if method.upper() == "GET":
                    kwargs["params"] = payload or {}
                else:
                    kwargs["json"] = payload or {}

                resp = self.session.request(method=method.upper(), url=url, **kwargs)
                resp.raise_for_status()
                body = resp.json()
                if "sessionid" in self.session.cookies:
                    self.session_id = self.session.cookies.get(
                        "sessionid", self.session_id
                    )

                code = int(body.get("code", 0))
                msg = str(body.get("message") or body.get("msg") or "")
                data = body.get("data")
                if code == -2:
                    raise ApiError("Session invalid (code=-2)")
                if code != 1:
                    raise ApiError(msg or f"API error code={code} endpoint={endpoint}")
                return ApiResponse(code=code, message=msg, data=data, raw=body)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt >= self.settings.retry_count:
                    break
                sleep_sec = 1.2**attempt
                time.sleep(sleep_sec)

        raise ApiError(f"Request failed for {endpoint}: {last_error}")

    def login(self) -> ApiResponse:
        result = self._request(
            "POST",
            "/userLogin/",
            {
                "iphone": self.settings.phone,
                "password": self.settings.password,
            },
        )
        if isinstance(result.data, dict):
            self.session_id = str(result.data.get("session_keys") or self.session_id)
        return result

    def rand_run_info(self, lat: float, lng: float) -> ApiResponse:
        return self._request(
            "GET", "xcxtapi/activity/randrunInfo", {"lat": lat, "lng": lng}
        )

    def create_line(self, student_id: int, pass_point: list[dict]) -> ApiResponse:
        return self._request(
            "POST",
            "/createLine/",
            {
                "student_id": student_id,
                "pass_point": pass_point,
            },
        )

    def check_record(self, encrypted_a: str) -> ApiResponse:
        return self._request("POST", "/checkRecord/", {"a": encrypted_a})

    def update_record(self, encrypted_a: str) -> ApiResponse:
        return self._request("POST", "/updateRecordNew/", {"a": encrypted_a})

    def upload_path_points(self, encrypted_img_url: str) -> ApiResponse:
        return self._request(
            "POST", "/uploadPathPointV3/", {"img_url": encrypted_img_url}
        )

    def record_info(self, record_id: int) -> ApiResponse:
        return self._request("GET", "/recordInfo/", {"id": record_id})

    def get_path_points(self, record_id: int) -> ApiResponse:
        return self._request("GET", "/GetPathPoints/", {"record_id": record_id})
