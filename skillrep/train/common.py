"""Shared pieces of the fine-tuning pipeline."""
import json
import time
import urllib.error
import urllib.request

# The instruction the encoder is trained with. At retrieval time SkillRep uses skillrep.data.QUERY_INSTRUCTION,
# which reads "Given a coding task description"; all reported results use that retrieval-time instruction.
TRAIN_INSTRUCTION = ("Instruct: Given a task description, retrieve the most relevant "
                     "skill document that would help an agent complete the task\nQuery:")


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def skill_id(skill):
    """Skills are keyed by "pid" (our pool format) or "skill_id"."""
    return str(skill["pid"] if "pid" in skill else skill["skill_id"])


def format_skill(skill, desc_max=500, body_max=8000):
    """Document text used for training and hard-negative mining: "name | description | body"."""
    name = skill.get("name") or ""
    desc = (skill.get("description") or "")[:desc_max]
    body = (skill.get("body") or "")[:body_max]
    return f"{name} | {desc} | {body}"


def chat(api_url, model, messages, temperature, max_tokens, api_key=None, extra=None, retries=3, timeout=180):
    """One OpenAI-compatible chat completion; returns the message text or None."""
    payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    payload.update(extra or {})
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(api_url, data=json.dumps(payload).encode(), headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = json.loads(r.read())["choices"][0]["message"].get("content")
            return text.strip() if text else None
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                return None
        except Exception:
            pass
        time.sleep(2 * (attempt + 1))
    return None
