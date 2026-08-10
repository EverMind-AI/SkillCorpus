"""SkillCorpus match server — embedding + reranker over HTTP.

Loads a bi-encoder (embedding) and a cross-encoder (reranker) once on GPU and
serves them behind three endpoints:

    GET  /health   -> {"ok": true}
    POST /embed    {"texts":   [...]} -> {"embeddings": [[...], ...]}
    POST /score    {"prompts": [...]} -> {"scores":     [0.0-1.0, ...]}

Configuration is read from the environment; see README.md.
"""

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("match_server")


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


RERANKER_MODEL = os.environ.get("RERANKER_MODEL", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = _env_int("PORT", 9000)
BATCH_SIZE = _env_int("BATCH_SIZE", 8)
MAX_LENGTH = _env_int("MAX_LENGTH", 4096)
EMBED_MAX_LENGTH = _env_int("EMBED_MAX_LENGTH", 4096)
MAX_BODY_BYTES = _env_int("MAX_BODY_BYTES", 64 * 1024 * 1024)

DTYPE = getattr(torch, os.environ.get("DTYPE", "bfloat16"))
device = torch.device(os.environ.get("DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu"))

_PREFIX = (
    '<|im_start|>system\nJudge whether the Document meets the requirements '
    'based on the Query and the Instruct provided. Note that the answer can '
    'only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
)
_SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'

tokenizer = None
model = None
emb_tokenizer = None
emb_model = None

_PREFIX_TOKENS: list[int] = []
_SUFFIX_TOKENS: list[int] = []
_TRUE_ID = None
_FALSE_ID = None
_PAD_ID = 0

# Requests are served concurrently, but a single GPU cannot run overlapping
# forward passes without fighting over memory — serialize the model calls.
_gpu_lock = threading.Lock()


def _check_rope(cfg, label: str) -> None:
    """Warn when RoPE theta looks like a silently-defaulted value.

    Checkpoints saved by transformers >= 5 keep ``rope_theta`` under the nested
    ``rope_parameters`` key only. Loading such a config on transformers 4.x
    falls back to the architecture default (10000 for Qwen3 instead of the
    trained 1000000) without raising — retrieval quality degrades silently.
    """
    theta = getattr(cfg, "rope_theta", None)
    if theta is None:
        theta = (getattr(cfg, "rope_parameters", None) or {}).get("rope_theta")
    if theta is not None and theta < 100_000:
        log.warning(
            "%s: rope_theta=%s looks like an architecture default. If the "
            "checkpoint was saved by transformers >= 5, upgrade transformers "
            "or add a top-level \"rope_theta\" to its config.json.",
            label, theta,
        )


def load_models() -> None:
    global tokenizer, model, emb_tokenizer, emb_model
    global _PREFIX_TOKENS, _SUFFIX_TOKENS, _TRUE_ID, _FALSE_ID, _PAD_ID

    missing = [n for n, v in (("RERANKER_MODEL", RERANKER_MODEL),
                              ("EMBEDDING_MODEL", EMBEDDING_MODEL)) if not v]
    if missing:
        raise SystemExit(
            f"{' and '.join(missing)} must be set to a model directory. "
            "See README.md."
        )

    log.info("Loading reranker %s", RERANKER_MODEL)
    tokenizer = AutoTokenizer.from_pretrained(RERANKER_MODEL, padding_side="left")
    model = AutoModelForCausalLM.from_pretrained(RERANKER_MODEL, dtype=DTYPE)
    if tokenizer.pad_token is None and tokenizer.eos_token is not None:
        tokenizer.pad_token = tokenizer.eos_token
    model.to(device).eval()
    _check_rope(model.config, "reranker")

    log.info("Loading embedding %s", EMBEDDING_MODEL)
    emb_tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL, padding_side="left")
    emb_model = AutoModel.from_pretrained(EMBEDDING_MODEL, dtype=DTYPE)
    if emb_tokenizer.pad_token is None and emb_tokenizer.eos_token is not None:
        emb_tokenizer.pad_token = emb_tokenizer.eos_token
    emb_model.to(device).eval()
    _check_rope(emb_model.config, "embedding")

    _PREFIX_TOKENS = tokenizer.encode(_PREFIX, add_special_tokens=False)
    _SUFFIX_TOKENS = tokenizer.encode(_SUFFIX, add_special_tokens=False)
    _TRUE_ID = tokenizer.convert_tokens_to_ids("yes")
    _FALSE_ID = tokenizer.convert_tokens_to_ids("no")
    _PAD_ID = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else 0

    log.info("Models ready on %s", device)


def _last_token_pool(hidden, attention_mask):
    left_padding = attention_mask[:, -1].sum() == attention_mask.shape[0]
    if left_padding:
        return hidden[:, -1]
    seq_len = attention_mask.sum(dim=1) - 1
    bs = hidden.shape[0]
    return hidden[torch.arange(bs, device=hidden.device), seq_len]


def embed(texts: list[str]) -> list[list[float]]:
    """L2-normalized last-token embeddings; cosine similarity = dot product."""
    if not texts:
        return []
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        encoded = emb_tokenizer(
            batch, padding=True, truncation=True,
            max_length=EMBED_MAX_LENGTH, return_tensors="pt",
        )
        encoded = {k: v.to(device) for k, v in encoded.items()}
        with _gpu_lock, torch.no_grad():
            hidden = emb_model(**encoded).last_hidden_state
            emb = _last_token_pool(hidden, encoded["attention_mask"])
            emb = F.normalize(emb, p=2, dim=1)
        out.extend(emb.float().cpu().tolist())
    return out


def _tokenize(text: str) -> list[int]:
    inputs = tokenizer(
        text, padding=False, truncation=True,
        max_length=MAX_LENGTH - len(_PREFIX_TOKENS) - len(_SUFFIX_TOKENS),
        return_attention_mask=False,
    )
    return _PREFIX_TOKENS + inputs["input_ids"] + _SUFFIX_TOKENS


def score(prompts: list[str]) -> list[float]:
    """P(yes) for each prompt, from the yes/no logits at the last position."""
    if not prompts:
        return []
    tokenized = [_tokenize(p) for p in prompts]
    scores: list[float] = []
    for i in range(0, len(tokenized), BATCH_SIZE):
        batch = tokenized[i : i + BATCH_SIZE]
        max_len = max(len(x) for x in batch)
        input_ids = torch.full((len(batch), max_len), _PAD_ID, dtype=torch.long)
        attention_mask = torch.zeros((len(batch), max_len), dtype=torch.long)
        for j, ids in enumerate(batch):
            input_ids[j, max_len - len(ids):] = torch.tensor(ids)
            attention_mask[j, max_len - len(ids):] = 1
        with _gpu_lock, torch.no_grad():
            out = model(
                input_ids=input_ids.to(device),
                attention_mask=attention_mask.to(device),
            )
        logits = out.logits[:, -1, :]
        pair = torch.stack([logits[:, _FALSE_ID], logits[:, _TRUE_ID]], dim=-1)
        scores.extend(torch.softmax(pair, dim=-1)[:, 1].float().cpu().tolist())
    return scores


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, payload: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, b'{"ok":true}')
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY_BYTES:
            self._send(413, b'{"error":"request body too large"}')
            return
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            if self.path == "/score":
                resp = {"scores": score(data["prompts"])}
            elif self.path == "/embed":
                resp = {"embeddings": embed(data["texts"])}
            else:
                self._send(404, b'{"error":"not found"}')
                return
            self._send(200, json.dumps(resp).encode())
        except Exception as e:
            log.exception("request failed")
            self._send(500, json.dumps({"error": str(e)}).encode())


def main() -> None:
    load_models()
    log.info("Listening on %s:%d", HOST, PORT)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
