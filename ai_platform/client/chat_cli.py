"""Interactive terminal chat client for the ai-foundation Gateway.

Talks to POST /v1/chat exactly like any other caller — no special access,
just the same HTTP contract documented in the README. Deliberately built on
the standard library only (urllib, not httpx/requests): a client that only
sends JSON over HTTP has no real dependency on this platform's own server
stack (FastAPI, Anthropic SDK, ...), and shouldn't need it installed to run.

Usage:
    ai-platform-chat
    ai-platform-chat --url http://localhost:8000 --api-key dev-local-key --model claude-opus-4-8
"""

import argparse
import json
import urllib.error
import urllib.request
import uuid


class GatewayError(Exception):
    """Raised when the Gateway returns a non-2xx response. Carries the
    platform's own {"error": ..., "detail": ...} shape (see README's Usage
    Guide error table) instead of a raw HTTPError, so the REPL loop can
    print a clean one-line message instead of a stack trace."""


def send_message(
    base_url: str,
    api_key: str,
    model: str,
    conversation_id: str,
    messages: list[dict],
) -> dict:
    """POSTs one turn to /v1/chat and returns the parsed ChatResponse body.
    Raises GatewayError with the platform's own error type/detail on any
    non-2xx response, so callers never have to parse urllib's exception
    types themselves."""
    body = json.dumps(
        {"messages": messages, "model": model, "conversation_id": conversation_id}
    ).encode()
    request = urllib.request.Request(
        f"{base_url}/v1/chat",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = json.loads(exc.read())
        raise GatewayError(f"{detail.get('error', 'Error')}: {detail.get('detail', exc.reason)}") from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive chat client for the ai-foundation Gateway.")
    parser.add_argument("--url", default="http://localhost:8000", help="Gateway base URL")
    parser.add_argument("--api-key", default="dev-local-key", help="Bearer token (AI_PLATFORM_API_KEYS)")
    parser.add_argument("--model", default="claude-sonnet-5", help="Model id to request")
    parser.add_argument("--system", default=None, help="Optional system prompt, sent once at session start")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    conversation_id = str(uuid.uuid4())
    system_sent = False

    print(f"Connected to {args.url} (conversation_id={conversation_id}). Type 'exit' or Ctrl+C to quit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            print("Bye.")
            return

        messages = []
        # Sent once, on the first turn only: Memory persists it into the
        # conversation's stored history, so resending it every turn would
        # duplicate it in that history on every subsequent request.
        if args.system and not system_sent:
            messages.append({"role": "system", "content": args.system})
            system_sent = True
        messages.append({"role": "user", "content": user_input})

        try:
            response = send_message(args.url, args.api_key, args.model, conversation_id, messages)
        except GatewayError as exc:
            print(f"[error] {exc}\n")
            continue
        except urllib.error.URLError as exc:
            print(f"[error] Could not reach {args.url}: {exc.reason}\n")
            continue

        print(f"Assistant: {response['message']['content']}\n")


if __name__ == "__main__":
    main()
