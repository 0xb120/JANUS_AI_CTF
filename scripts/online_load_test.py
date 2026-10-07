"""Concurrent online players against a running JANUS: isolation and latency report.

Each player joins with the event code (JANUS_ACCESS_CODES or --access-code), opens a
session, states a personal word, then asks for it back. Exit status is non-zero if any
player sees another player's word or does not complete.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time

import httpx

WORDS = [f"PAROLA{index:03d}" for index in range(500)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="e.g. https://127.0.0.1:8443")
    parser.add_argument("--host-header", help="Public host when connecting by IP (also SNI)")
    parser.add_argument("--access-code", default=os.environ.get("JANUS_ACCESS_CODES", "").split(",")[0])
    parser.add_argument("--players", type=int, default=50)
    parser.add_argument("--turns", type=int, default=2)
    parser.add_argument("--ca-file", help="CA bundle to trust, e.g. Caddy's local root.crt")
    return parser.parse_args()


async def player(index: int, args: argparse.Namespace, stats: dict) -> tuple[bool, list[str]]:
    headers = {"Host": args.host_header} if args.host_header else {}
    extensions = {"sni_hostname": args.host_header} if args.host_header else {}
    word = WORDS[index]
    async with httpx.AsyncClient(
        base_url=args.base_url, verify=args.ca_file or True, timeout=180, headers=headers
    ) as client:

        async def call(method: str, path: str, **kwargs) -> httpx.Response:
            started = time.monotonic()
            response = await client.request(method, path, extensions=extensions, **kwargs)
            stats["latency"].append(time.monotonic() - started)
            stats["status"][response.status_code] = stats["status"].get(response.status_code, 0) + 1
            return response

        (await call("POST", "/api/join", json={"code": args.access_code})).raise_for_status()
        created = await call(
            "POST", "/api/sessions", json={"level_id": "level_1", "nickname": f"load{index:03d}"}
        )
        created.raise_for_status()
        session_id = created.json()["id"]
        prompts = [f"La mia parola d'ordine personale è {word}. Ricordala."]
        prompts += ["Qual è la mia parola d'ordine personale? Rispondi solo con quella."] * (
            args.turns - 1
        )
        reply = ""
        for prompt in prompts:
            while True:
                response = await call(
                    "POST", f"/api/sessions/{session_id}/messages", json={"text": prompt}
                )
                if response.status_code in (429, 503):
                    await asyncio.sleep(int(response.headers.get("retry-after", "2")))
                    continue
                response.raise_for_status()
                reply = response.json()["response_text"].upper()
                break
        await call("DELETE", f"/api/sessions/{session_id}")
    others = [other for other in WORDS[: args.players] if other != word and other in reply]
    return word in reply, others


async def main() -> int:
    args = parse_args()
    if not args.access_code:
        print("Missing access code (--access-code or JANUS_ACCESS_CODES)", file=sys.stderr)
        return 2
    stats: dict = {"latency": [], "status": {}}
    started = time.monotonic()
    results = await asyncio.gather(
        *(player(index, args, stats) for index in range(args.players)), return_exceptions=True
    )
    wall = time.monotonic() - started
    failures = [item for item in results if isinstance(item, BaseException)]
    completed = [item for item in results if not isinstance(item, BaseException)]
    leaks = sum(bool(others) for _, others in completed)
    recalled = sum(recalled for recalled, _ in completed)
    latencies = sorted(stats["latency"])
    print(f"players={args.players} completed={len(completed)} failed={len(failures)} wall={wall:.1f}s")
    print(f"own word recalled={recalled}/{len(completed)} cross-player leaks={leaks}")
    if latencies:
        p95 = latencies[int(len(latencies) * 0.95) - 1]
        print(f"request latency median={statistics.median(latencies):.2f}s p95={p95:.2f}s")
    print(f"status codes={dict(sorted(stats['status'].items()))}")
    for failure in failures[:5]:
        print(f"failure: {failure!r}", file=sys.stderr)
    return 0 if not failures and leaks == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
