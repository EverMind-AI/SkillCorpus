"""Dense encoder: Qwen3-Embedding style models with last-token pooling and L2 normalization."""
import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer

MAX_LENGTH = 4096         # requests, skill bodies, hypothetical skills
PSEUDO_MAX_LENGTH = 512   # pseudo-requests (about 40 words each)


def last_token_pool(hidden, mask):
    if mask[:, -1].sum() == mask.shape[0]:  # left padding
        return hidden[:, -1]
    idx = mask.sum(dim=1) - 1
    return hidden[torch.arange(hidden.shape[0], device=hidden.device), idx]


class Encoder:
    """Encodes texts with a (fine-tuned or untuned) Qwen3-Embedding checkpoint.

    Requests and pseudo-requests carry the instruction prefix (see data.query_text);
    skill bodies and hypothetical skills are encoded as documents, without a prefix.
    """

    def __init__(self, model_path, device="cuda:0", max_length=MAX_LENGTH, dtype=torch.bfloat16):
        self.device = device
        self.max_length = max_length
        self.tok = AutoTokenizer.from_pretrained(model_path, padding_side="left")
        if self.tok.pad_token is None and self.tok.eos_token is not None:
            self.tok.pad_token = self.tok.eos_token
        self.model = AutoModel.from_pretrained(model_path, dtype=dtype).to(device).eval()

    @torch.no_grad()
    def __call__(self, texts, batch_size=8, max_length=None, sort_by_length=True):
        """Returns a float32 CPU tensor [len(texts), dim] of unit-norm embeddings, in input order.

        Sorting by length keeps padding small; in bfloat16 the batch composition shifts the embeddings
        slightly, so sort_by_length=False reproduces runs that encoded the texts in their given order."""
        max_length = max_length or self.max_length
        order = np.argsort([-len(t) for t in texts]) if sort_by_length else np.arange(len(texts))
        out = [None] * len(texts)
        for k in range(0, len(texts), batch_size):
            idx = order[k:k + batch_size]
            batch = self.tok([texts[j] if texts[j].strip() else " " for j in idx], padding=True, truncation=True,
                             max_length=max_length, return_tensors="pt").to(self.device)
            hidden = self.model(**batch).last_hidden_state
            vec = F.normalize(last_token_pool(hidden, batch["attention_mask"]).float(), dim=-1).cpu()
            for r, j in enumerate(idx):
                out[j] = vec[r]
        return torch.stack(out)
