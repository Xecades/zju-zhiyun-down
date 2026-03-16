import argparse
import asyncio
import os
import signal
import ssl
from datetime import datetime
from pathlib import Path

import aiohttp
import yaml
from loguru import logger

from src.api import extract_videos_from_course, fetch_course_catalogue
from src.downloader import download_file


def load_config(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def process_course(
    course: dict,
    out_root: Path,
    session: aiohttp.ClientSession,
    dry_run: bool = False,
    stop_event: asyncio.Event | None = None,
):
    course_id = str(course.get("id"))
    course_name = course.get("name") or f"course_{course_id}"
    out_dir = out_root / course_name
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog = await fetch_course_catalogue(session, course_id)
    if not catalog:
        logger.warning(f"No catalogue for course {course_id}")
        return
    videos = extract_videos_from_course(catalog)

    # 顺序处理每个回放
    for v in videos:
        try:
            # if stop requested, abort processing this course immediately
            if stop_event is not None and stop_event.is_set():
                return

            sub_id = str(v.get("sub_id"))
            urls = v.get("urls", [])
            if not urls:
                continue

            # 只取第一个可用 mp4
            selected = None
            for u in urls:
                if u.endswith(".mp4"):
                    selected = u
                    break
            if not selected:
                selected = urls[0]

            # 用 start_at 时间来命名，格式 YYYYMMDD_HHMMSS_subid.ext
            start_at = str(v.get("start_at"))
            ts = None
            try:
                ts = int(start_at)
            except Exception:
                ts = None
            if ts:
                dt = datetime.fromtimestamp(ts)
                timestr = dt.strftime("%Y%m%d_%H%M%S")
            else:
                timestr = "unknown"

            ext = os.path.splitext(selected.split("?")[0])[1] or ".mp4"
            filename = f"{timestr}_{sub_id}{ext}"
            dest = out_dir / filename
            # skip if final file already exists
            if dest.exists():
                logger.info(f"Skipping existing file: {course_name} | {dest.name}")
                continue

            if dry_run:
                # human readable info: course name, start time, title
                title = v.get("title") or "(no title)"
                start_at = str(v.get("start_at"))
                try:
                    start_ts = int(start_at)
                    start_str = datetime.fromtimestamp(start_ts).isoformat(sep=" ")
                except Exception:
                    start_str = str(start_at)
                logger.info(
                    f"DRY-RUN: {course_name} | {start_str} | {title} -> {dest}\n  URL: {selected}"
                )
                continue

            # 简单重试（顺序）
            for attempt in range(3):
                try:
                    await download_file(session, selected, dest, stop_event=stop_event)
                    break
                except Exception as e:
                    logger.warning(f"Attempt {attempt + 1} failed for {selected}: {e}")
                    await asyncio.sleep(2**attempt)
        except Exception as e:
            logger.warning(f"Skip one video in {course_name} due to error: {e}")
            continue


async def main_async(args):
    cfg = load_config(Path(args.config))
    courses = cfg.get("courses", [])
    out_root = Path(args.out) if args.out else Path.home() / "Desktop" / "ZJUClass"
    interval = cfg.get("interval", 3600)
    stop_event = asyncio.Event()

    # handle signals for graceful shutdown
    def _handle_signum(signum, frame):
        logger.info(f"Received signal {signum}, requesting stop...")
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, _handle_signum)
        signal.signal(signal.SIGTERM, _handle_signum)
    except Exception:
        # signal handling may not be available in some environments
        pass

    # classroom.zju.edu.cn 使用了过短的 DH key，需降低 SSL 安全级别以允许连接
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(connector=connector) as session:
        # 一次性运行
        for c in courses:
            try:
                await process_course(
                    c,
                    out_root,
                    session,
                    dry_run=args.dry_run,
                    stop_event=stop_event,
                )
            except Exception as e:
                logger.warning(f"Skip one course due to error: {e}")
        if args.once:
            return

        # 持续轮询
        while True:
            logger.info(f"Sleeping for {interval} seconds before next poll...")

            # wait for either the interval to elapse or a stop event
            sleep_task = asyncio.create_task(asyncio.sleep(interval))
            stop_task = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait(
                {sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            # cancel whichever task is still pending
            for p in pending:
                p.cancel()

            if stop_event.is_set():
                logger.info("Stop requested, exiting main loop")
                return
            for c in courses:
                try:
                    await process_course(
                        c,
                        out_root,
                        session,
                        dry_run=args.dry_run,
                        stop_event=stop_event,
                    )
                except Exception as e:
                    logger.warning(f"Skip one course due to error: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--out", default=None)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run")
    args = parser.parse_args()
    logger.add("zju_downloader.log", rotation="10 MB")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
