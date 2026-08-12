"""
Natural language in, musical intent out.

An agent says "tests are green again after the refactor" and something has to
decide that this is `resolved`, not `good`, and that it matters a medium
amount. That is a language judgement, so an LLM makes it — OpenRouter if you
have a key, a local Ollama model if you'd rather keep it on the machine.

Two things keep this honest:

  * The heuristic below is not a stub. It runs when no model is configured,
    when the model is slow, and when the model returns junk. The system has
    to be usable with no API key at all, so the fallback is written to be
    genuinely decent rather than a placeholder.
  * The model classifies. It does not compose. It cannot pick notes, set
    volume, or reach the synth — it returns one of five kinds and a number.
    Everything musical downstream is bounded by the conductor's rules, so a
    bad classification makes the soundtrack briefly wrong, never unpleasant.

That split is deliberate. Letting a language model drive audio parameters
directly is how you get something nobody can stand to leave running.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .events import KINDS


# Kept deliberately short. Every token here is paid on *every* event, and on
# a local model prompt evaluation dominates: the long teaching version of
# this prompt measured 78s per call against gemma4:12b, the compact one 28s.
# Neither is fast enough for live audio, which is the real lesson — but there
# is no reason to make a fast model slow.
SYSTEM_PROMPT = (
    "Classify software-agent activity for a live soundtrack. "
    "Reply with only JSON: {\"kind\":K,\"intensity\":N,\"summary\":S}\n"
    "K is one of: progress (routine work, nothing decided yet) | "
    "good (something worked) | bad (something failed, work continues) | "
    "blocked (work STOPPED, needs a human) | "
    "resolved (a bad or blocked thing is now fixed).\n"
    "N is 0.0-1.0, how much attention it deserves: routine 0.2, "
    "tests passing 0.6, production outage 1.0.\n"
    "S is under 8 words.\n"
    # Small models follow examples far better than definitions. These six were
    # chosen from measured failures: the first two because describing ordinary
    # work was being read as success or failure, and the rest because the
    # progress/good and bad/blocked boundaries are where errors concentrate.
    "\nExamples:\n"
    "reading main.py to map the call graph -> "
    "{\"kind\":\"progress\",\"intensity\":0.2,\"summary\":\"reading code\"}\n"
    "compiling, this takes a while -> "
    "{\"kind\":\"progress\",\"intensity\":0.2,\"summary\":\"compiling\"}\n"
    "all 240 tests passed -> "
    "{\"kind\":\"good\",\"intensity\":0.6,\"summary\":\"tests passed\"}\n"
    "TypeError in parse_headers -> "
    "{\"kind\":\"bad\",\"intensity\":0.6,\"summary\":\"type error\"}\n"
    "I need the staging password to continue -> "
    "{\"kind\":\"blocked\",\"intensity\":0.9,\"summary\":\"needs credentials\"}\n"
    "credentials received, deploy is green again -> "
    "{\"kind\":\"resolved\",\"intensity\":0.7,\"summary\":\"deploy recovered\"}"
)


@dataclass
class Intent:
    kind: str
    intensity: float
    summary: str
    via: str            # which brain decided this — shown by `doctor`
    confident: bool = True   # did the decider actually recognise something?

    def __post_init__(self):
        if self.kind not in KINDS:
            self.kind = "progress"
        self.intensity = max(0.0, min(1.0, float(self.intensity)))


# ── the fallback, which has to be good ───────────────────────────────────
# Ordered: the first pattern that matches wins, so the specific and serious
# cases are checked before the general ones.
_SECRET = r"(password|credential|secret|api ?key|token|access|permission|approval)"

_RULES: list[tuple[str, str, float]] = [
    # ── blocked: work has STOPPED. Checked first — a message can mention a
    #    failure and a request for help, and the request is what matters.
    (
        r"\b(blocked|blocker|stuck|unblock me"
        r"|can(not|'t) (proceed|continue|go on)"
        r"|before i can (proceed|continue)"
        # "I need the staging database password to continue" — the ask can sit
        # several words away from the noun, which the first version missed.
        rf"|need(s|ed)?\s+(\w+\s+){{0,4}}{_SECRET}"
        rf"|missing\s+(\w+\s+){{0,3}}{_SECRET}"
        r"|needs? (your |a |human )?(input|help|decision|review|sign.?off)"
        r"|waiting (on|for)"
        r"|requires? (your|human|manual|a human)"
        r"|permission denied|unauthorized|not authorized|access denied"
        r"|rate.?limit|quota exceeded"
        r"|timed? out waiting"
        r"|ask(ing)? (the )?(user|human|you)\b"
        r")", "blocked", 0.85),

    # ── resolved: a bad thing became good. Before `good`, because these are
    #    all phrased with positive words and would otherwise read as success.
    (
        r"\b(resolved|unblocked|unstuck|recovered"
        # "…is green again", "…passing again" — the first version demanded the
        # word "tests" immediately before, so most real phrasings escaped.
        r"|(green|passing|pass|working|works|healthy|up|online|stable) again"
        r"|back (online|up|to (green|normal|healthy))"
        r"|no longer (failing|broken|blocked|stuck)"
        r"|works now|working now|fixed now"
        r"|credentials? (received|provided|added|found)"
        r")", "resolved", 0.7),

    # ── bad: something is wrong, work continues
    (r"\b(error|exception|traceback|stack ?trace|assertion"
     r"|fail(ed|ing|ure|s)?|bug|broken|broke|regression|crash|panic"
     r"|invalid|couldn't|could not|unable to|denied)\b", "bad", 0.6),

    # ── good: something worked
    (r"\b(success(ful|fully)?|succeeded|passed|passing|green|complete[d]?"
     r"|done|finished|merged|deployed|shipped|fixed|built"
     r"|landed|approved|work(s|ed|ing))\b", "good", 0.6),
]

_INTENSIFIERS = re.compile(
    r"\b(critical|fatal|urgent|severe|production|outage|data ?loss|"
     r"security|breach|emergency|corrupt)\b", re.I)
_DIMINISHERS = re.compile(r"\b(minor|small|nit|trivial|cosmetic|typo|warning)\b", re.I)


def heuristic(text: str) -> Intent:
    """
    Keyword classification.

    `confident` records whether a rule actually fired. That distinction is
    load-bearing: measured over twenty real phrasings, every single miss was
    a fall-through to the default, and every rule that *did* fire was right.
    So a match is trustworthy and a fall-through means "I have no idea",
    which is exactly the case worth spending a model call on.
    """
    low = (text or "").lower()
    kind, intensity, matched = "progress", 0.25, False

    for pattern, k, base in _RULES:
        if re.search(pattern, low):
            kind, intensity, matched = k, base, True
            break

    if _INTENSIFIERS.search(low):
        intensity = min(1.0, intensity + 0.3)
    if _DIMINISHERS.search(low):
        intensity = max(0.05, intensity - 0.25)

    summary = " ".join(text.split()[:8]) if text else "activity"
    return Intent(kind, intensity, summary, via="heuristic", confident=matched)


# ── model-backed classification ──────────────────────────────────────────
def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _extract_json(text: str) -> dict | None:
    """Models like to wrap JSON in prose or fences. Dig it out."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


class Interpreter:
    """
    Picks a brain at construction time and stays with it.

    backend: "auto" (default), "openrouter", "ollama", or "heuristic".
    "auto" prefers OpenRouter when OPENROUTER_API_KEY is set, then a local
    Ollama if one is answering, then the heuristic.
    """

    def __init__(
        self,
        backend: str = "auto",
        model: str | None = None,
        timeout: float = 6.0,
        ollama_host: str = "http://127.0.0.1:11434",
    ):
        self.timeout = timeout
        self.ollama_host = os.environ.get("OLLAMA_HOST", ollama_host).rstrip("/")
        self.api_key = os.environ.get("OPENROUTER_API_KEY", "")
        self._cache: dict[str, Intent] = {}

        if backend == "auto":
            if self.api_key:
                backend = "openrouter"
            elif self._ollama_alive():
                backend = "ollama"
            else:
                backend = "heuristic"

        self.backend = backend
        self.model = (
            model
            or os.environ.get("AGENTISIZER_MODEL")
            or ("anthropic/claude-haiku-4.5" if backend == "openrouter"
                else self._first_ollama_model() or "llama3.2:3b")
        )

    def _ollama_models(self) -> list[str]:
        try:
            with urllib.request.urlopen(f"{self.ollama_host}/api/tags", timeout=1.5) as r:
                return [m["name"] for m in json.loads(r.read().decode()).get("models", [])]
        except Exception:
            return []

    def _first_ollama_model(self) -> str | None:
        """
        Use a model that is actually installed.

        Guessing a popular name and 404ing is a worse failure than picking
        whatever is there: the user sees "not working" for a model they never
        asked for. Prefer small ones — this is a one-word judgement, and big
        reasoning models are far too slow for live audio.
        """
        models = self._ollama_models()
        if not models:
            return None
        small = [m for m in models
                 if re.search(r"[:\-](0\.5|1|1\.5|2|3|3\.8|4)b", m, re.I)]
        return (small or models)[0]

    def _ollama_alive(self) -> bool:
        return bool(self._ollama_models())

    def describe(self) -> str:
        if self.backend == "heuristic":
            return "heuristic (no model configured)"
        return f"{self.backend}:{self.model}"

    # Above this, a model is technically working but useless here: the sound
    # would land long after the thing it describes. Reasoning models are the
    # usual culprit — they spend their token budget thinking before
    # answering, which is exactly the wrong trade for a one-word judgement.
    SLOW_SECONDS = 2.5

    def health(self, timeout: float | None = None) -> tuple[bool, str, float]:
        """
        Actually call the model once and report what happened, and how long.

        `doctor` needs the truth, not the configuration. A stale API key
        still *looks* configured, and the fallback is quiet by design, so
        without this the system would degrade silently and tell you it was
        fine — which is worse than failing.

        Latency is part of that truth. A classifier that takes thirty
        seconds is not a working classifier for live audio.
        """
        if self.backend == "heuristic":
            return False, "no model configured", 0.0

        saved, self.timeout = self.timeout, (timeout or max(self.timeout, 30.0))

        # Warm up first, and time the second call. Ollama unloads a model
        # after a few idle minutes, so a cold call measures disk-to-GPU load
        # rather than classification: llama3.2:3b timed 12.9s cold against
        # 2.1s warm. Reporting the cold number condemns a usable model.
        try:
            if self.backend == "openrouter":
                self._call_openrouter("warmup")
            else:
                self._call_ollama("warmup")
        except Exception:
            pass

        start = time.monotonic()
        try:
            raw = (self._call_openrouter("all tests passed")
                   if self.backend == "openrouter"
                   else self._call_ollama("all tests passed"))
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            return False, f"HTTP {e.code} — {body}", time.monotonic() - start
        except Exception as e:
            return False, f"{type(e).__name__}: {e}", time.monotonic() - start
        finally:
            self.timeout = saved

        elapsed = time.monotonic() - start
        if not raw or raw.get("kind") not in KINDS:
            return False, f"unusable reply: {str(raw)[:120]}", elapsed
        return True, f"{self.backend}:{self.model}", elapsed

    def _call_openrouter(self, text: str) -> dict | None:
        data = _post_json(
            "https://openrouter.ai/api/v1/chat/completions",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                "temperature": 0,
                "max_tokens": 120,
            },
            {"Authorization": f"Bearer {self.api_key}"},
            self.timeout,
        )
        return _extract_json(data["choices"][0]["message"]["content"])

    def _call_ollama(self, text: str) -> dict | None:
        data = _post_json(
            f"{self.ollama_host}/api/chat",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            },
            {},
            self.timeout,
        )
        return _extract_json(data["message"]["content"])

    def classify_with_model(self, text: str) -> Intent | None:
        """One model call. None on any failure — never raises."""
        if self.backend not in ("openrouter", "ollama"):
            return None
        try:
            raw = (self._call_openrouter(text) if self.backend == "openrouter"
                   else self._call_ollama(text))
        except Exception:
            return None            # network, timeout, malformed
        if not raw or raw.get("kind") not in KINDS:
            return None
        return Intent(
            raw["kind"],
            float(raw.get("intensity", 0.5)),
            str(raw.get("summary", ""))[:80],
            via=self.backend,
        )

    def interpret(self, text: str) -> Intent:
        """
        Rules first, model only when the rules have nothing to say.

        Not a fallback chain — an ordering chosen from measurement. The
        keyword rules are precise but narrow: when one fires it is reliable,
        and when none fires the answer is a shrug. A small local model is the
        opposite, good at reading a sentence with no keywords in it and prone
        to overreading plain ones ("permission denied, cannot continue" came
        back as `bad` rather than `blocked`).

        Asking each only what it is good at beat either alone, and it means
        the model is consulted on the minority of messages — so most events
        are classified in microseconds and the latency budget is spent only
        where it buys something.
        """
        if not text or not text.strip():
            return Intent("progress", 0.2, "", via="empty")

        key = text.strip()[:400]
        if key in self._cache:
            return self._cache[key]

        result = heuristic(text)
        if not result.confident:
            from_model = self.classify_with_model(text)
            if from_model is not None:
                result = from_model

        if len(self._cache) < 2000:
            self._cache[key] = result
        return result
