"""LLM-written texts for the two expansion modules (Section 3.2).

Hypothetical skills (query-side expansion): two short texts per request describing a skill that would
solve it. Pseudo-requests (document-side expansion): two short task requests per skill. Both use
Qwen3.6-27B at temperature 0.3 with thinking disabled, served behind an OpenAI-compatible endpoint
(for example vLLM).
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from .data import as_str

HYPOTHETICAL_SYSTEM = "You write concise descriptions of AI agent skills. Output only the description, no preamble."
HYPOTHETICAL_PROMPTS = [
    "Write a short (~150 word) description of an AI agent skill that would help with this task: {q}",
    "Describe what a skill would do to solve this task, step by step (~150 word): {q}",
]

PSEUDO_SYSTEM = "You write realistic user task requests. Output only the request, no preamble."
PSEUDO_PROMPTS = [
    "Below is documentation for an AI agent skill. Write one short (~40 word) task request that a user "
    "would send, for which this skill is exactly the right tool.\n\n{d}",
    "Below is documentation for an AI agent skill. Write one short (~40 word) task request describing a "
    "concrete problem whose solution requires the steps this skill performs.\n\n{d}",
]
PSEUDO_BODY_MAX = 3000
TEMPERATURE = 0.3


def skill_prompt_text(skill):
    return (f"# {as_str(skill.get('name'))}\n{as_str(skill.get('description'))[:300]}\n\n"
            f"{as_str(skill.get('body'))[:PSEUDO_BODY_MAX]}")


def chat(api_url, model, system, prompt, max_tokens, api_key=None, retries=3, timeout=300):
    """One chat completion; doubles the token budget if the reply comes back empty because of length."""
    payload = {"model": model, "temperature": TEMPERATURE, "max_tokens": max_tokens,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
               "chat_template_kwargs": {"enable_thinking": False}}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(api_url, data=json.dumps(payload).encode(), headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                choice = json.loads(r.read())["choices"][0]
            text = (choice["message"].get("content") or "").strip()
            if text:
                return text
            payload["max_tokens"] *= 2
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(3 * 2 ** attempt)
                continue
            return ""
        except Exception:
            time.sleep(2 * 2 ** attempt)
    return ""


def generate(jobs, out_path, api_url, model, system, max_tokens, workers=64, api_key=None):
    """jobs: list of (key, slot, prompt), one slot per prompt variant. Results are written to the JSON file
    {key: [text per slot]} as they arrive ("" for a slot not done yet), so an interrupted run resumes by
    sending only the missing slots."""
    n_slots = 1 + max((slot for _, slot, _ in jobs), default=0)
    done = {}
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            done = json.load(f)
    for k in {k for k, _, _ in jobs}:
        done[k] = (list(done.get(k, [])) + [""] * n_slots)[:n_slots]
    todo = [(k, slot, p) for k, slot, p in jobs if not done[k][slot]]
    lock = threading.Lock()
    finished = 0

    def save():
        tmp = out_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(done, f, ensure_ascii=False)
        os.replace(tmp, out_path)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(chat, api_url, model, system, p, max_tokens, api_key): (k, slot) for k, slot, p in todo}
        for fu in as_completed(futures):
            text = fu.result()
            with lock:
                k, slot = futures[fu]
                if text:
                    done[k][slot] = text
                finished += 1
                if finished % 500 == 0:
                    save()
    save()
    return done


def hypothetical_jobs(requests):
    return [(r["task"], i, t.format(q=r["query"])) for r in requests for i, t in enumerate(HYPOTHETICAL_PROMPTS)]


def pseudo_jobs(pool):
    return [(str(s["pid"]), i, t.format(d=skill_prompt_text(s))) for s in pool for i, t in enumerate(PSEUDO_PROMPTS)]
