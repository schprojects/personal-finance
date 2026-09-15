"""OpenRouter LLM consultation for auto-mode classification.

Stdlib-only (urllib). The network call is split from the pure parsing so the
rule-extraction logic is unit-testable without a key or network.
"""
import json
import os
import urllib.error
import urllib.request

API_URL = "https://openrouter.ai/api/v1/chat/completions"


def api():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit(
            "auto mode needs OPENROUTER_API_KEY (and it is required only there)")
    return key


def model():
    return os.environ.get("OPENROUTER_MODEL", "openrouter/auto")


def build_message(items, categories):
    return {
        "model": model(),
        "messages": [
            {"role": "system", "content":
                "You classify Indian bank-statement transactions into expense "
                "categories. Only use the categories you are given."},
            {"role": "user", "content":
                "Available categories: " + ", ".join(sorted(categories)) + "\n\n"
                "For each PAYEE line below (as NUMBER<TAB>PARTICULARS), choose "
                "exactly one existing category. Reply ONLY with NUMBER<TAB>CATEGORY "
                "lines, one per item, nothing else.\n\n" +
                "\n".join(f"{i}\t{it}" for i, it in enumerate(items))},
        ],
        "temperature": 0,
    }


def classify_unmatched(items, categories, timeout=120):
    """Ask OpenRouter to categorize unmatched payees.

    Returns {index: category} for the items the model placed inside the given
    category set. Network/API errors raise (the CLI reports and continues).
    """
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(build_message(items, categories)).encode("utf-8"),
        headers={"Authorization": f"Bearer {api()}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        raise SystemExit(f"openrouter request failed: {e}")
    text = payload["choices"][0]["message"]["content"]
    return parse_response(text, categories)


def parse_response(text, categories):
    """Parse 'N<tab|space>CATEGORY' lines; only known categories count."""
    known = set(categories)
    out = {}
    for line in (text or "").splitlines():
        parts = line.replace("\t", " ").split(None, 1)
        if len(parts) != 2:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        cat = parts[1].strip()
        if cat in known:
            out[idx] = cat
    return out