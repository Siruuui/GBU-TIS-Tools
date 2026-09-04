#!/usr/bin/env python3
"""GBU 教务系统选课接口客户端。

登录由用户在浏览器中完成，本模块只接收当前进程使用的 Cookie。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import requests


BASE_URL = "https://jwxt.gbu.edu.cn"
LOGIN_URL = f"{BASE_URL}/authentication/main"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
}


class GBUError(RuntimeError):
    """GBU 请求或响应无法使用时抛出的异常。"""


@dataclass(frozen=True)
class Semester:
    year: str
    term: str
    code: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Semester":
        year = str(payload.get("p_xn") or "").strip()
        term = str(payload.get("p_xq") or "").strip()
        code = str(payload.get("p_xnxq") or "").strip()
        if not year or not term or not code:
            raise GBUError("当前学期接口未返回完整的学年学期信息")
        return cls(year, term, code)


@dataclass(frozen=True)
class CourseTask:
    task_id: str
    task_code: str
    task_name: str
    course_code: str
    course_name: str
    mode_code: str
    mode: str
    capacity: str
    selected: str
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CourseTask":
        return cls(
            task_id=str(payload.get("id") or "").strip(),
            task_code=str(payload.get("rwh") or "").strip(),
            task_name=str(payload.get("rwmc") or "").strip(),
            course_code=str(payload.get("kcdm") or "").strip(),
            course_name=str(payload.get("kcmc") or "").strip(),
            mode_code=str(payload.get("xkfsdm") or "").strip(),
            mode=str(payload.get("xkms") or "").strip(),
            capacity=str(payload.get("zrl") or "").strip(),
            selected=str(payload.get("yxzrs") or "").strip(),
            raw=payload,
        )

    @property
    def is_lottery(self) -> bool:
        return self.mode == "2"


@dataclass(frozen=True)
class SubmissionResult:
    success: bool
    message: str
    raw: dict[str, Any]


class GBUClient:
    def __init__(self, cookie_header: str, timeout: float = 15.0):
        if not cookie_header or not cookie_header.strip():
            raise ValueError("Cookie 不能为空")
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.timeout = timeout
        self._set_cookie_header(cookie_header)

    def _set_cookie_header(self, cookie_header: str) -> None:
        value = cookie_header.strip()
        if value.lower().startswith("cookie:"):
            value = value.split(":", 1)[1].strip()
        pairs: dict[str, str] = {}
        for item in value.split(";"):
            if "=" not in item:
                continue
            name, cookie_value = item.split("=", 1)
            name = name.strip()
            if name:
                pairs[name] = cookie_value.strip()
        if not pairs:
            raise ValueError("Cookie 格式无效，应为 name=value; name2=value2")
        self.session.cookies.update(pairs)

    def _post_json(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.post(
                f"{BASE_URL}{path}", data=data, timeout=self.timeout
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise GBUError(f"请求 {path} 失败：{exc}") from exc
        except ValueError as exc:
            raise GBUError(f"请求 {path} 返回的不是 JSON，可能是登录已失效") from exc
        if not isinstance(payload, dict):
            raise GBUError(f"请求 {path} 返回格式异常")
        return payload

    def current_semester(self) -> Semester:
        return Semester.from_payload(self._post_json("/Xsxk/queryXkdqXnxq", {"mxpylx": 1}))

    def open_categories(self, semester: Semester) -> list[str]:
        payload = self._post_json(
            "/Xsxk/queryKkxqList",
            {
                "p_xn": semester.year,
                "p_xq": semester.term,
                "p_xnxq": semester.code,
                "mxpylx": 1,
            },
        )
        values: list[Any] = []
        for key in ("list", "rows", "data", "kkxqList", "kxqList"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                values.extend(candidate)
            elif isinstance(candidate, dict):
                values.extend(candidate.get("list", []))
        categories: list[str] = []
        for item in values:
            if isinstance(item, str):
                code = item.strip()
            elif isinstance(item, dict):
                code = str(
                    item.get("xkfsdm") or item.get("dm") or item.get("code") or ""
                ).strip()
            else:
                code = ""
            if code and code not in categories:
                categories.append(code)
        return categories or ["de_xk"]

    def course_tasks(
        self, semester: Semester, categories: Iterable[str], page_size: int = 100
    ) -> list[CourseTask]:
        tasks: list[CourseTask] = []
        seen: set[str] = set()
        for category in categories:
            page = 1
            while True:
                payload = self._post_json(
                    "/Xsxk/queryKxrw",
                    {
                        "p_xn": semester.year,
                        "p_xq": semester.term,
                        "p_xnxq": semester.code,
                        "p_pylx": 1,
                        "mxpylx": 1,
                        "p_xkfsdm": category,
                        "pageNum": page,
                        "pageSize": page_size,
                    },
                )
                block = payload.get("kxrwList") or payload.get("data") or payload
                rows = block.get("list", []) if isinstance(block, dict) else []
                if not isinstance(rows, list):
                    rows = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    task = CourseTask.from_payload(row)
                    if task.task_id and task.task_id not in seen:
                        tasks.append(task)
                        seen.add(task.task_id)
                total = block.get("total") if isinstance(block, dict) else None
                if not rows or (isinstance(total, int) and page * page_size >= total):
                    break
                if len(rows) < page_size:
                    break
                page += 1
        return tasks

    def selected_courses(self, semester: Semester) -> dict[str, Any]:
        return self._post_json(
            "/Xsxk/queryYxkc",
            {"p_xn": semester.year, "p_xq": semester.term, "p_xnxq": semester.code},
        )

    def cart(self, semester: Semester) -> dict[str, Any]:
        return self._post_json(
            "/Xsxk/queryXkgwc",
            {"p_xn": semester.year, "p_xq": semester.term, "p_xnxq": semester.code},
        )

    def submit_first_come(self, semester: Semester, task: CourseTask) -> SubmissionResult:
        payload = self._post_json(
            "/Xsxk/addGouwuche",
            {
                "p_pylx": 1,
                "mxpylx": 1,
                "p_xktjz": "rwtjzyx",
                "p_xn": semester.year,
                "p_xq": semester.term,
                "p_xnxq": semester.code,
                "p_xkfsdm": "de_xk",
                "p_id": task.task_id,
            },
        )
        message = str(payload.get("message") or payload.get("msg") or "未知结果")
        raw_success = payload.get("jg") in (1, "1", True)
        success = raw_success or "成功" in message
        return SubmissionResult(success, message, payload)
