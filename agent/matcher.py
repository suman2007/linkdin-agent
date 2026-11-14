"""Two-stage match: keyword prefilter, then a Claude check."""
import os
import re

import anthropic

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

SYSTEM = (
    "You judge LinkedIn posts. Answer whether the post is substantially about "
    "LTE or 4G mobile network technology (not just a passing mention, a job-spam "
    "hashtag list, or an unrelated 4G/LTE-named product). Reply with exactly one "
    "line: 'YES: <short reason>' or 'NO: <short reason>'."
)


def build_pattern(keywords: list[str]) -> re.Pattern:
    parts = [r"(?<![A-Za-z0-9])" + re.escape(k) + r"(?![A-Za-z0-9])" for k in keywords]
    return re.compile("|".join(parts), re.IGNORECASE)


def keyword_match(text: str, pattern: re.Pattern) -> bool:
    return bool(pattern.search(text))


def matched_keywords(text: str, pattern: re.Pattern) -> list[str]:
    seen, out = set(), []
    for m in pattern.finditer(text):
        k = m.group(0)
        if k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return out


def parse_verdict(reply: str) -> tuple[bool, str]:
    """Find the first 'YES:'/'NO:' line, ignoring any <think>...</think> reasoning block."""
    reply = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.DOTALL | re.IGNORECASE)
    for line in reply.splitlines():
        m = re.match(r"\s*\W*(YES|NO)\b\W*(.*)", line, re.IGNORECASE)
        if m:
            return m.group(1).upper() == "YES", m.group(2).strip()
    return False, ""


def _prompt(text: str) -> str:
    return 'Post:\n"""\n' + text[:3000] + '\n"""'


def _nvidia_client():
    from openai import OpenAI

    key = os.environ.get("NVIDIA_API_KEY", "").removeprefix("Bearer ").strip()
    return OpenAI(base_url=NVIDIA_BASE_URL, api_key=key)


def llm_match(text: str, model: str, client=None) -> tuple[bool, str]:
    """Claude models go to Anthropic; any other model id goes to NVIDIA's OpenAI-compatible API."""
    if model.startswith("claude"):
        client = client or anthropic.Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": _prompt(text)}],
        )
        if resp.stop_reason == "refusal":
            return False, "model refused"
        return parse_verdict("".join(b.text for b in resp.content if b.type == "text"))
    client = client or _nvidia_client()
    resp = client.chat.completions.create(
        model=model,
        max_tokens=512,
        temperature=0,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": _prompt(text)}],
    )
    return parse_verdict(resp.choices[0].message.content or "")
