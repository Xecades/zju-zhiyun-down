import asyncio
from pathlib import Path

import aiohttp
import loguru
from tqdm import tqdm


async def download_file(
    session: aiohttp.ClientSession,
    url: str,
    dest: Path,
    chunk_size: int = 1024 * 64,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Download with resumable support using HTTP Range.

    Leaves a .part file if interrupted; resumes on next run.
    """
    # if stop requested before we even start, don't create .part or progress bar
    if stop_event is not None and stop_event.is_set():
        loguru.logger.info(f"Stop requested before starting download: {dest}")
        return

    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.parent.mkdir(parents=True, exist_ok=True)

    existing = tmp.stat().st_size if tmp.exists() else 0
    headers = {"User-Agent": "zju-downloader/0.1"}
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"

    # allow long downloads
    timeout = aiohttp.ClientTimeout(total=None)
    async with session.get(url, headers=headers, timeout=timeout) as resp:
        # handle servers that ignore Range and return 200
        if existing > 0 and resp.status == 200:
            # server doesn't support range: restart from scratch
            existing = 0
            tmp.unlink(missing_ok=True)

        resp.raise_for_status()

        # determine total size for progress
        content_length = resp.headers.get("Content-Length")
        total: int | None
        if content_length is not None:
            try:
                total = int(content_length) + existing if existing > 0 else int(content_length)
            except Exception:
                total = None
        else:
            total = None

        mode = "ab" if existing > 0 else "wb"
        written = existing

        pbar = None
        # only create pbar if we are still running
        if stop_event is None or not stop_event.is_set():
            pbar = tqdm(
                total=total if total is not None else 0,
                initial=existing,
                unit="B",
                unit_scale=True,
                desc=dest.parent.name + "/" + dest.name,
            )

        with tmp.open(mode) as f:
            async for chunk in resp.content.iter_chunked(chunk_size):
                if stop_event is not None and stop_event.is_set():
                    # graceful stop: leave .part for resume
                    if pbar:
                        pbar.close()
                    return
                f.write(chunk)
                written += len(chunk)
                if pbar:
                    pbar.update(len(chunk))

        if pbar:
            pbar.close()

    tmp.replace(dest)
    loguru.logger.info(f"Finished download -> {dest}")
