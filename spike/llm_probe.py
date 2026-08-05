"""Find out which Gemini models your key can actually use.

Listing is not permission. This key returns 200 from `/v1beta/models` and lists
forty-two models supporting `generateContent`, then fails on every one of them:

    gemini-2.5-flash    403 PERMISSION_DENIED  your project has been denied access
    gemini-3.6-flash    403 PERMISSION_DENIED  your project has been denied access
    gemini-2.0-flash    429 RESOURCE_EXHAUSTED free-tier token quota spent

Exactly the lesson the Caspian capability probe taught on day 1, in a different
API: ask what works, do not read what is advertised. So this calls each candidate
with a real one-token prompt and reports what came back.

    python spike/llm_probe.py
    python spike/llm_probe.py --all        every listed model, not just the shortlist
    python spike/llm_probe.py --quiet      print only the best usable model id
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spike.probe import _load_dotenv  # noqa: E402

BASE = "https://generativelanguage.googleapis.com/v1beta"

#: Best first. The rewriter wants low latency and short outputs, so a flash-class
#: model beats a pro one here; quality of a one-line rewrite is not the bottleneck.
SHORTLIST = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

_SKIP = ("tts", "image", "embedding", "aqa", "lyria", "computer-use", "veo", "imagen")


def _request(path: str, key: str, payload: dict | None = None, timeout: int = 60):
    url = f"{BASE}/{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": key,
            # Same Cloudflare-style trap as the Caspian probe: give a real UA.
            "User-Agent": "hearsay-llm-probe/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def list_models(key: str) -> list[str]:
    models = _request("models?pageSize=200", key).get("models", [])
    return [
        m["name"].removeprefix("models/")
        for m in models
        if "generateContent" in m.get("supportedGenerationMethods", [])
        and not any(word in m["name"] for word in _SKIP)
    ]


def try_model(model: str, key: str, timeout: int = 120) -> tuple[str, str]:
    """Actually call it. Returns (status, detail)."""
    payload = {
        "contents": [{"parts": [{"text": "Reply with the single word: ready"}]}],
        "generationConfig": {"maxOutputTokens": 512, "temperature": 0},
    }
    try:
        data = _request(f"models/{model}:generateContent", key, payload, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            error = json.load(exc).get("error", {})
        except Exception:
            return f"{exc.code}", "unreadable error body"
        return f"{exc.code} {error.get('status', '')}".strip(), error.get("message", "")[:70]
    except urllib.error.URLError as exc:
        return "unreachable", str(exc.reason)[:70]
    except TimeoutError:
        # A thinking model can spend minutes before its first byte. Reporting
        # this as a distinct outcome matters: a slow model is not a broken one,
        # and an uncaught read timeout used to crash the whole probe.
        return "timeout", f"no response within {timeout}s"
    except OSError as exc:
        return "error", f"{type(exc).__name__}: {exc}"

    candidate = (data.get("candidates") or [{}])[0]
    parts = candidate.get("content", {}).get("parts") or []
    # Concatenate: a thinking model can put text in a later part, and reading
    # parts[0] alone silently yields "".
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        return "empty", f"finishReason={candidate.get('finishReason')}"
    return "OK", text[:40]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="probe every listed model")
    parser.add_argument("--quiet", action="store_true", help="print only the best usable id")
    args = parser.parse_args()

    _load_dotenv()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY is not set. Copy .env.example to .env.", file=sys.stderr)
        return 2

    try:
        listed = list_models(key)
    except urllib.error.HTTPError as exc:
        print(f"cannot list models: {exc.code}", file=sys.stderr)
        return 1

    candidates = listed if args.all else [m for m in SHORTLIST if m in listed]
    if not candidates:
        candidates = listed[:10]

    if not args.quiet:
        print(f"{len(listed)} models listed; calling {len(candidates)}\n")

    usable: list[str] = []
    for model in candidates:
        status, detail = try_model(model, key)
        if status == "OK":
            usable.append(model)
        if not args.quiet:
            mark = "\033[32mOK  \033[0m" if status == "OK" else "\033[31mfail\033[0m"
            print(f"  {mark} {model:<32} {status:<22} {detail}")

    if args.quiet:
        print(usable[0] if usable else "")
        return 0 if usable else 1

    print()
    if not usable:
        print("\033[31mNo model on this key can generate.\033[0m")
        print("The rewriter falls back to the offline scripted backend, so the game")
        print("still runs — but the tampering will read as templated rather than human.")
        print("Fix: enable billing, or make a fresh key at https://aistudio.google.com/apikey")
        return 1

    print(f"\033[32musable: {', '.join(usable)}\033[0m")
    print(f"pick:   {usable[0]}   (set GEMINI_MODEL to override)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
