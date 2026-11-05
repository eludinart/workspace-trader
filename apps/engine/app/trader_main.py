"""Trader worker: acquire the bot lease, then run paper trading loops."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import uuid

from app.lease import LeaseManager
from app.migrate import run_migrations
from app.runtime import LeaseLost, TradingRuntime
from app.store import Store

logger = logging.getLogger(__name__)

_ACQUIRE_BACKOFF_SEC = 5.0


async def _supervise(runtime: TradingRuntime, stop: asyncio.Event) -> None:
    """Block until SIGTERM or the trade loop exits (lease lost / fail-closed)."""
    stop_wait = asyncio.create_task(stop.wait())
    loop_wait = asyncio.create_task(runtime.wait_loop())
    try:
        done, _pending = await asyncio.wait(
            {stop_wait, loop_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_wait in done:
            logger.info("shutdown requested")
        else:
            logger.info("trade loop ended")
    finally:
        for task in (stop_wait, loop_wait):
            if not task.done():
                task.cancel()
        for task in (stop_wait, loop_wait):
            try:
                await task
            except asyncio.CancelledError:
                pass


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_migrations()
    store = Store()
    store.maybe_migrate_json()
    owner = f"trader-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    lease = LeaseManager(owner)
    if not lease.acquire():
        logger.error("could not acquire lease; backing off %.0fs", _ACQUIRE_BACKOFF_SEC)
        await asyncio.sleep(_ACQUIRE_BACKOFF_SEC)
        raise SystemExit("could not acquire lease")

    logger.info("paper trader acquired lease owner=%s", owner)
    runtime = TradingRuntime(store=store, lease=lease)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        logger.info("shutdown signal")
        stop.set()

    try:
        loop.add_signal_handler(signal.SIGTERM, _request_stop)
        loop.add_signal_handler(signal.SIGINT, _request_stop)
    except NotImplementedError:
        signal.signal(signal.SIGTERM, lambda *_args: stop.set())
        signal.signal(signal.SIGINT, lambda *_args: stop.set())

    try:
        runtime.hydrate()
        await runtime.start()
        await _supervise(runtime, stop)
    except LeaseLost:
        logger.error("lease lost before loops started")
    finally:
        await runtime.stop()
        await runtime.shutdown_streams()
        await runtime.stop_heartbeat()
        runtime.close()
        try:
            released = lease.release()
            logger.info("lease release owner=%s ok=%s", owner, released)
        except Exception:
            logger.exception("lease release failed")


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
