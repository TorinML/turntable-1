"""Turntable-1's language model, for Beepnik's lines, his obituaries and Ground Control.

[llm] provider picks where it runs:
  ollama       a local Ollama server ([llm] url, model): everything stays at home (the default)
  gemini       Google's Gemini API (free tier at aistudio.google.com, no card needed)
  groq         Groq (free tier at console.groq.com; very fast open models)
  openrouter   OpenRouter (several free models at openrouter.ai)
  openai       any other OpenAI-compatible service, at [llm] api_url

The online providers need an API key (the settings page keeps it in secrets.toml). [llm] api_model
picks their model; blank uses the recommended one below. Free tiers and model names change: if a
model stops working, set a current one in api_model.
"""
import datetime
import json
import logging
import os
import threading
import time

try:
    import requests
except ImportError:                     # an environment without it (the web app's, on a hand-built install)
    requests = None
import urllib.error
import urllib.request

log = logging.getLogger("turntable-llm")


class _Resp:
    """The few parts of a response this module uses, from requests or from the standard library."""
    def __init__(self, status: int, body: bytes):
        self.status_code, self._body = status, body
        self.text = body.decode("utf-8", "replace")
        self.reason = ""

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text[:200]}")


def _post(url: str, body: dict, headers: dict | None = None, timeout: float = 20):
    """POST JSON: with requests when it's installed, else with Python's own HTTP client."""
    if requests is not None:
        return requests.post(url, json=body, headers=headers or {}, timeout=timeout)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _Resp(r.status, r.read())
    except urllib.error.HTTPError as e:
        return _Resp(e.code, e.read() or b"")

PRESETS = {
    # provider: (OpenAI-compatible base URL, recommended model)
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-flash-lite-latest"),  # an alias: always the current Flash-Lite
    "groq": ("https://api.groq.com/openai/v1", "llama-3.1-8b-instant"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"),
    "openai": ("", ""),
}

_key = {"value": "", "read": 0.0}
_usage_lock = threading.Lock()


def _usage_file() -> str:
    try:
        import turntable_config
        return os.path.join(turntable_config.load()["paths"]["data"], "llm-usage.json")
    except Exception:
        return "/tmp/turntable-llm-usage.json"


def _quota_day(provider: str) -> str:
    """The provider's quota day: Google's daily limits reset at midnight Pacific time."""
    if provider == "gemini":
        try:
            from zoneinfo import ZoneInfo
            return datetime.datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
        except Exception:
            return (datetime.datetime.utcnow() - datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
    return datetime.datetime.now().strftime("%Y-%m-%d")


def usage(provider: str) -> int:
    """Online requests made so far in the provider's current quota day."""
    try:
        with open(_usage_file()) as f:
            u = json.load(f)
        return u.get("count", 0) if u.get("day") == _quota_day(provider) and u.get("provider") == provider else 0
    except (OSError, ValueError):
        return 0


def _spend(provider: str, limit: int):
    """Count one online request, or refuse if today's budget is used up."""
    with _usage_lock:
        n = usage(provider)
        if limit and n >= limit:
            raise ValueError(f"{provider}: today's request limit ({limit}) is reached; using built-in lines until it resets")
        path = _usage_file()
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"provider": provider, "day": _quota_day(provider), "count": n + 1}, f)
            os.replace(tmp, path)
        except OSError as e:
            log.warning("Couldn't record the request count: %s", e)


def api_key() -> str:
    """The API key, from secrets.toml (re-read at most once a minute, so a new key applies soon)."""
    if time.time() - _key["read"] > 60:
        try:
            import turntable_config
            _key["value"] = turntable_config.load()["secrets"].get("llm", {}).get("api_key", "")
        except Exception as e:
            log.warning("Couldn't read the API key: %s", e)
        _key["read"] = time.time()
    return _key["value"]


def _reason(r) -> str:
    """The provider's own explanation of an error, if it gave one."""
    try:
        err = r.json()
        err = err[0] if isinstance(err, list) and err else err
        msg = (err.get("error") or {}).get("message") if isinstance(err, dict) else None
        return (msg or r.text or r.reason)[:200]
    except ValueError:
        return (r.text or r.reason)[:200]


def describe(llm: dict) -> str:
    p = llm.get("provider", "ollama")
    if p == "ollama":
        return f"Ollama, {llm.get('model', '')}"
    return f"{p}, {llm.get('api_model') or PRESETS.get(p, ('', ''))[1] or '(no model set)'}"


def generate(llm: dict, system: str, prompt: str, temperature: float = 0.9, max_tokens: int = 200,
             timeout: float = None) -> tuple:
    """(text, truncated). Raises on any failure, so callers fall back to their built-in lines."""
    provider = llm.get("provider", "ollama") or "ollama"
    timeout = timeout or llm.get("timeout_seconds", 20)
    if provider == "ollama":
        body = {"model": llm["model"], "prompt": prompt, "stream": False, "keep_alive": llm.get("keep_alive", "15m"),
                "options": {"temperature": temperature, "num_predict": max_tokens, "top_p": 0.9}}
        if system:
            body["system"] = system
        if "think" in llm:
            body["think"] = bool(llm["think"])           # e.g. false to skip a reasoning model's thinking
        r = _post(llm["url"].rstrip("/") + "/api/generate", body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("the response wasn't a JSON object")
        return str(data.get("response", "")), data.get("done_reason") == "length"

    base, recommended = PRESETS.get(provider, ("", ""))
    base = (llm.get("api_url") or base).rstrip("/")
    model = llm.get("api_model") or recommended
    key = api_key()
    if not base or not model:
        raise ValueError(f"{provider}: set the API address and model in the settings")
    if not key:
        raise ValueError(f"{provider}: no API key set (Settings, Language model)")
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if llm.get("reasoning"):
        # "none" or "low" keeps a thinking model (like Gemini Flash) quick: Beepnik's lines don't need reasoning,
        # and on Gemini the thinking would also use up the reply's token budget
        body["reasoning_effort"] = llm["reasoning"]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for attempt in (1, 2):
        _spend(provider, int(llm.get("daily_limit") or 0))      # every request counts, retries too
        r = _post(f"{base}/chat/completions", body, headers=headers, timeout=timeout)
        if r.status_code in (500, 502, 503, 504) and attempt == 1:
            time.sleep(3)                 # overloaded for a moment (common on free tiers): one more try
            continue
        break
    if r.status_code == 429:
        raise ValueError(f"{provider}: rate limited (the free tier's limit); using built-in lines for now")
    if r.status_code >= 400:
        raise ValueError(f"{provider}: HTTP {r.status_code} from {model}: {_reason(r)}")
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content")) or ""
    return str(text), choice.get("finish_reason") == "length"
