from __future__ import annotations

import argparse
import sys

from omni.openrouter import OpenRouterError, OpenRouterGateway


SYSTEM_PROMPT = """You are OMNI, a helpful desktop assistant running
inside a Hyprland Linux environment.

Be concise, accurate and practical.
Never claim to have executed an action unless it was actually executed.
Do not invent system information.
For destructive operations, request confirmation.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="OMNI desktop assistant"
    )
    parser.add_argument("query", nargs="?", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--status", action="store_true")

    args = parser.parse_args(argv)

    gateway = OpenRouterGateway()

    if args.status:
        import json
        print(json.dumps(gateway.status(), indent=2))
        return 0

    if not args.query:
        parser.error("A query is required")

    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": args.query},
        ]

        for chunk in gateway.stream(
            messages,
            model=args.model,
        ):
            sys.stdout.write(chunk)
            sys.stdout.flush()

        return 0

    except OpenRouterError as exc:
        print(f"OMNI: {exc}", file=sys.stderr)
        return 1

    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
