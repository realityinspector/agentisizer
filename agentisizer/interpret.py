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
import urllib.error
import urllib.request
from dataclasses import dataclass

from .events import KINDS


SYSTEM_PROMPT = """\
You are the ear of a system that turns software-agent activity into a live \
background soundtrack, played through Sonic Pi. A human listens to this for \
hours while working on something else, instead of reading agent transcripts.

Classify the message into exactly one kind:

  progress  - routine work happening; the common case. Reading files, running \
a command, thinking. Nothing has succeeded or failed yet.
  good      - something genuinely worked. Tests pass, a build is green, a fix \
landed, a task completed.
  bad       - something is wrong but work continues. A test failed, an error \
was hit, a bug was found.
  blocked   - work has STOPPED and needs a human. Waiting on input, missing \
credentials, an unrecoverable failure, a question the agent cannot answer \
alone. Use this sparingly; it triggers an escalating alarm.
  resolved  - a previously bad or blocked situation has been cleared.

Also give intensity from 0.0 to 1.0: how much this deserves the listener's \
attention. Routine progress is 0.1-0.3. A passing test suite is around 0.6. \
A production outage is 1.0.

Reply with only a JSON object, no prose:
{"kind": "...", "intensity": 0.0, "summary": "under 8 words"}"""


@dataclass
class Intent:
    kind: str
    intensity: float
    summary: str
    via: str          # which brain decided this — shown by `doctor`

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
    """Keyword classification. Used when there is no model, or it failed."""
    low = (text or "").lower()
    kind, intensity = "progress", 0.25

    for pattern, k, base in _RULES:
        if re.search(pattern, low):
            kind, intensity = k, base
            break

    if _INTENSIFIERS.search(low):
        intensity = min(1.0, intensity + 0.3)
    if _DIMINISHERS.search(low):
        intensity = max(0.05, intensity - 0.25)

    summary = " ".join(text.split()[:8]) if text else "activity"
    return Intent(kind, intensity, summary, via="heuristic")


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
        self.model = model or os.environ.get(
            "AGENTISIZER_MODEL",
            "anthropic/claude-haiku-4.5" if backend == "openrouter" else "llama3.2",
        )

    def _ollama_alive(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.ollama_host}/api/tags", timeout=1.5):
                return True
        except Exception:
            return False

    def describe(self) -> str:
        if self.backend == "heuristic":
            return "heuristic (no model configured)"
        return f"{self.backend}:{self.model}"

    def health(self) -> tuple[bool, str]:
        """
        Actually call the model once and report what happened.

        `doctor` needs the truth, not the configuration. A stale API key
        still *looks* configured, and the fallback is quiet by design, so
        without this the system would degrade silently and tell you it was
        fine — which is worse than failing.
        """
        if self.backend == "heuristic":
            return False, "no model configured"
        try:
            raw = (self._call_openrouter("all tests passed")
                   if self.backend == "openrouter"
                   else self._call_ollama("all tests passed"))
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            return False, f"HTTP {e.code} — {body}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        if not raw or raw.get("kind") not in KINDS:
            return False, f"unusable reply: {str(raw)[:120]}"
        return True, f"{self.backend}:{self.model}"

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

    def interpret(self, text: str) -> Intent:
        """Classify. Never raises, never blocks the music."""
        if not text or not text.strip():
            return Intent("progress", 0.2, "", via="empty")

        key = text.strip()[:400]
        if key in self._cache:
            return self._cache[key]

        result: Intent | None = None
        if self.backend in ("openrouter", "ollama"):
            try:
                raw = (self._call_openrouter(text) if self.backend == "openrouter"
                       else self._call_ollama(text))
                if raw and raw.get("kind") in KINDS:
                    result = Intent(
                        raw["kind"],
                        float(raw.get("intensity", 0.5)),
                        str(raw.get("summary", ""))[:80],
                        via=self.backend,
                    )
            except Exception:
                result = None      # network, timeout, malformed — fall through

        if result is None:
            result = heuristic(text)

        if len(self._cache) < 2000:
            self._cache[key] = result
        return result
