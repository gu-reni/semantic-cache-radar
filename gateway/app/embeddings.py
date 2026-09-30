from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from gateway.app.config import Settings


class EmbeddingService:
    """Generate normalized multilingual embeddings with ONNX Runtime.

    为什么不用 sentence-transformers：
      它会连带装上 PyTorch，镜像 1GB 起步；纯 CPU 机器上 ONNX 运行时的
      内存占用约为其三分之一，镜像也小得多。

    为什么必须自己做池化：
      导出的 ONNX 只有一个输出 last_hidden_state，即逐 token 的隐状态，
      并不是句向量。句向量要按 e5 官方的方式做 mean pooling 再 L2 归一化，
      否则直接拿去做余弦检索得到的是错误结果。
    """

    # e5 系列的上下文上限；超长输入在此截断，避免 ONNX 图报维度错误。
    MAX_TOKENS = 512

    def __init__(self, settings: Settings) -> None:
        model_dir = Path(settings.embedding_model_path)
        model_file = model_dir / "model.onnx"
        tokenizer_file = model_dir / "tokenizer.json"

        if not model_file.is_file() or not tokenizer_file.is_file():
            raise FileNotFoundError(
                f"Embedding 模型不完整：{model_dir} 缺少 model.onnx 或 tokenizer.json。"
                "请先运行 bash scripts/download_embedding_model.sh"
            )

        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, settings.embedding_threads)
        options.inter_op_num_threads = 1

        self._session = ort.InferenceSession(
            str(model_file),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(str(tokenizer_file))
        self._tokenizer.enable_truncation(max_length=self.MAX_TOKENS)
        self._input_names = {item.name for item in self._session.get_inputs()}
        self._output_names = [item.name for item in self._session.get_outputs()]

    def encode_query(self, text: str) -> list[float]:
        """Embed a search query; e5 要求查询侧加 query: 前缀。"""
        return self._encode(f"query: {text}")

    def encode_passage(self, text: str) -> list[float]:
        """Embed a stored passage; e5 要求被检索侧加 passage: 前缀。"""
        return self._encode(f"passage: {text}")

    def _encode(self, text: str) -> list[float]:
        encoding = self._tokenizer.encode(text)
        input_ids = np.array([encoding.ids], dtype=np.int64)
        attention_mask = np.array([encoding.attention_mask], dtype=np.int64)

        feeds = {"input_ids": input_ids, "attention_mask": attention_mask}
        # 这张图声明了 token_type_ids，漏传会直接报错。
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)

        hidden_state = self._session.run(self._output_names, feeds)[0]
        return self._mean_pool(hidden_state, attention_mask)

    @staticmethod
    def _mean_pool(hidden_state: np.ndarray, attention_mask: np.ndarray) -> list[float]:
        """Mean pooling over real tokens, then L2 normalize."""
        mask = attention_mask.astype(np.float32)[..., None]
        summed = (hidden_state * mask).sum(axis=1)
        counted = np.clip(mask.sum(axis=1), 1e-9, None)
        pooled = summed / counted
        normalized = pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
        return normalized[0].astype(np.float32).tolist()
