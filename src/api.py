import asyncio
import json
from typing import Any, cast

import aiohttp
from loguru import logger


async def fetch_course_catalogue(
    session: aiohttp.ClientSession,
    course_id: str,
) -> dict[str, Any] | None:
    url = f"https://classroom.zju.edu.cn/courseapi/v2/course/catalogue?course_id={course_id}"
    timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_connect=10)
    last_error: Exception | None = None

    for attempt in range(3):
        try:
            async with session.get(
                url,
                headers={"User-Agent": "zju-downloader/0.1"},
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"Fetch catalogue failed for course {course_id}, status={resp.status}"
                    )
                    return None
                return cast(dict[str, Any], await resp.json())
        except (TimeoutError, aiohttp.ClientError, OSError, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(2**attempt)
                continue

    logger.warning(f"Fetch catalogue failed for course {course_id}: {last_error}")
    return None


def extract_videos_from_course(
    course_json: dict[str, Any],
) -> list[dict[str, Any]]:
    """从 API 返回中提取回放条目，返回包含 sub_id, title, start_at, url 的字典列表"""
    out: list[dict[str, Any]] = []
    if not course_json:
        return out
    data = course_json.get("result", {}).get("data", [])
    for item in data:
        sub_id = item.get("sub_id")
        title = item.get("title")
        start_at = item.get("start_at")
        content_str = item.get("content")
        urls: list[str] = []
        if content_str:
            try:
                content = json.loads(content_str)
                # 常见位置
                if isinstance(content.get("playback"), dict):
                    pb = content.get("playback")
                    if isinstance(pb.get("url"), list):
                        urls.extend(pb.get("url") or [])
                    elif isinstance(pb.get("url"), str):
                        urls.append(pb.get("url"))
                if isinstance(content.get("save_playback"), dict):
                    sp = content.get("save_playback")
                    c = sp.get("contents")
                    if isinstance(c, str) and c.startswith("http"):
                        urls.append(c)
                # 直接在 item 中也可能存在 playback 字段
            except Exception:
                # 忽略解析错误
                pass
        # 另一些课程直接把 playback 放在 item['playback']
        if not urls and isinstance(item.get("playback"), dict):
            pb = item.get("playback")
            u = pb.get("url")
            if isinstance(u, list):
                urls.extend(u)
            elif isinstance(u, str):
                urls.append(u)

        # file_list
        fl: list[dict[str, Any]] = []
        try:
            cont = json.loads(content_str) if content_str else {}
            fl = cont.get("file_list") or []
        except Exception:
            fl = []
        for f in fl:
            fn = f.get("file_name")
            if fn and isinstance(fn, str) and fn.startswith("http"):
                urls.append(fn)

        # 去重并只保留 http(s) 链接
        clean_urls = []
        for u in urls:
            if isinstance(u, str) and u.startswith("http") and u not in clean_urls:
                clean_urls.append(u)

        if clean_urls:
            out.append(
                {
                    "sub_id": sub_id,
                    "title": title,
                    "start_at": start_at,
                    "urls": clean_urls,
                }
            )

    return out
