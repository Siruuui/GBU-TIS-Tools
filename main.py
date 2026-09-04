#!/usr/bin/env python3
"""GBU 教务系统选课助手。"""

from __future__ import annotations

import time
import argparse
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from colorama import Fore, Style, init

from gbu_client import CourseTask, GBUClient, GBUError, LOGIN_URL, Semester


ROOT = Path(__file__).resolve().parent
CLASS_FILE = ROOT / "Class.txt"
COOKIE_FILE = ROOT / "Cookie.txt"


@dataclass(frozen=True)
class CourseRequest:
    text: str


def load_requests(path: Path = CLASS_FILE) -> list[CourseRequest]:
    if not path.exists():
        path.write_text("", encoding="utf-8")
        return []
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [CourseRequest(line) for line in lines if line and not line.startswith("#")]


def match_request(request: CourseRequest, tasks: list[CourseTask]) -> tuple[CourseTask | None, str | None]:
    text = request.text.strip()
    exact = [task for task in tasks if task.task_name and task.task_name == text]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, f"“{text}”对应多个任务，无法自动选择"

    parts = text.replace("|", " ").split()
    if len(parts) >= 2:
        code, class_hint = parts[0], " ".join(parts[1:])
        candidates = [
            task
            for task in tasks
            if task.course_code == code
            and class_hint
            in " ".join(filter(None, (task.task_name, task.task_code, str(task.raw.get("kcxx") or ""))))
        ]
        if len(candidates) == 1:
            return candidates[0], None
        if len(candidates) > 1:
            names = "、".join(task.task_name or task.task_code for task in candidates)
            return None, f"“{text}”匹配多个班级：{names}"

    candidates = [task for task in tasks if task.course_name == text]
    if len(candidates) == 1:
        return candidates[0], None
    if len(candidates) > 1:
        names = "、".join(task.task_name or task.task_code for task in candidates)
        return None, f"课程名“{text}”对应多个班级：{names}；请改填完整任务名称"
    return None, f"未找到课程任务：{text}"


def load_cookie(path: Path = COOKIE_FILE) -> str:
    if not path.exists():
        raise ValueError(
            f"未找到 {path.name}。请创建该文件并填入浏览器复制的 Cookie。"
        )
    cookie = path.read_text(encoding="utf-8").strip()
    if not cookie:
        raise ValueError(f"{path.name} 为空，请填入浏览器复制的 Cookie。")
    return cookie


def wait_until(start_at: str) -> None:
    try:
        target = datetime.strptime(start_at, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise ValueError("--start-at 必须使用 YYYY-MM-DD HH:MM:SS 格式") from exc
    now = datetime.now()
    if target <= now:
        print(f"指定时间 {start_at} 已到或已过去，立即开始提交。")
        return
    print(f"已完成准备，将在 {start_at}（本机时间）自动开始提交。")
    while True:
        remaining = (target - datetime.now()).total_seconds()
        if remaining <= 0:
            break
        time.sleep(min(remaining, 1.0))


def semester_label(semester: Semester) -> str:
    labels = {"1": "秋季", "2": "春季", "3": "夏季"}
    return f"{semester.year} 学年第 {semester.term} 学期（{labels.get(semester.term, '未知')}）"


def run(start_at: str | None = None) -> int:
    init(autoreset=True)
    requests_to_make = load_requests()
    if not requests_to_make:
        print(f"{Fore.YELLOW}Class.txt 为空，请先填写待选课程。{Style.RESET_ALL}")
        return 0

    try:
        cookie = load_cookie()
        client = GBUClient(cookie)
        semester = client.current_semester()
    except (ValueError, GBUError) as exc:
        print(f"{Fore.RED}登录状态验证失败：{exc}{Style.RESET_ALL}")
        print(f"请先在浏览器打开并完成登录：{LOGIN_URL}")
        return 1

    print(f"{Fore.GREEN}登录成功，当前学期：{semester_label(semester)}{Style.RESET_ALL}")
    try:
        categories = client.open_categories(semester)
        tasks = client.course_tasks(semester, categories)
    except GBUError as exc:
        print(f"{Fore.RED}读取课程任务失败：{exc}{Style.RESET_ALL}")
        return 1
    print(f"读取到 {len(tasks)} 个课程任务，开放类别：{', '.join(categories)}")

    queue: list[CourseTask] = []
    for request in requests_to_make:
        task, error = match_request(request, tasks)
        if task is None:
            print(f"{Fore.YELLOW}{error}{Style.RESET_ALL}")
            continue
        if task.is_lottery:
            print(f"{Fore.YELLOW}{task.task_name or task.course_name} 是抽签/志愿模式，第一版不自动提交。{Style.RESET_ALL}")
            continue
        queue.append(task)
        print(f"[待选] {task.task_name or task.course_name} ({task.task_id})")

    if not queue:
        print("没有可自动提交的先到先得课程。")
        return 0
    if start_at:
        try:
            wait_until(start_at)
        except ValueError as exc:
            print(f"{Fore.RED}{exc}{Style.RESET_ALL}")
            return 2
    elif input("按回车开始按 Class.txt 顺序提交；输入其他字符取消："):
        print("已取消，未提交任何课程。")
        return 0

    for task in queue:
        finished = False
        for attempt in range(1, 4):
            try:
                result = client.submit_first_come(semester, task)
            except GBUError as exc:
                print(f"{task.task_name or task.course_name} 第 {attempt} 次失败：{exc}")
                if attempt < 3:
                    time.sleep(1)
                continue
            print(f"{task.task_name or task.course_name}：{result.message}")
            message = result.message
            if result.success or any(word in message for word in ("已选", "已满", "冲突", "结束", "关闭")):
                finished = True
                break
            if attempt < 3:
                time.sleep(1)
        if not finished:
            print(f"{task.task_name or task.course_name} 未得到明确结果，停止处理后续课程。")
            break
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GBU 教务系统选课助手")
    parser.add_argument(
        "--start-at",
        metavar="YYYY-MM-DD HH:MM:SS",
        help="按本机时间自动开始提交；不传入时按回车开始",
    )
    args = parser.parse_args()
    try:
        raise SystemExit(run(args.start_at))
    except KeyboardInterrupt:
        print("\n已中止，未执行退课操作。")
        raise SystemExit(130)
