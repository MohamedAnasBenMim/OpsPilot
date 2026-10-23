"""Générateur de trafic minimal pour la démo (GET et POST /orders en continu).

python app/loadgen.py --url http://localhost:8001 --rps 20
"""

from __future__ import annotations

import argparse
import asyncio
import random

import httpx


async def worker(client: httpx.AsyncClient, url: str, delay: float) -> None:
    while True:
        try:
            if random.random() < 0.7:
                await client.get(f"{url}/orders")
            else:
                await client.post(f"{url}/orders")
        except httpx.HTTPError:
            pass
        await asyncio.sleep(delay)


async def main(url: str, rps: float, workers: int) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        await asyncio.gather(*(worker(client, url, workers / rps) for _ in range(workers)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8001")
    ap.add_argument("--rps", type=float, default=20)
    ap.add_argument("--workers", type=int, default=40)  # assez de concurrence pour saturer le pool DB en chaos
    a = ap.parse_args()
    asyncio.run(main(a.url, a.rps, a.workers))
