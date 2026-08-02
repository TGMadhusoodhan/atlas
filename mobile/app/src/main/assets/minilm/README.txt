Place the on-device embedding model here (used by memory/Embedder.kt):

  model.onnx   — all-MiniLM-L6-v2 exported for ONNX Runtime (384-d output)
  vocab.txt    — the BERT WordPiece vocabulary (one token per line)

Where to get them:
  - model.onnx: from sentence-transformers/all-MiniLM-L6-v2, ONNX export
    (e.g. optimum-cli export onnx --model sentence-transformers/all-MiniLM-L6-v2 out/)
    then copy out/model.onnx here. A quantized int8 ONNX also works and is smaller.
  - vocab.txt: the tokenizer vocab from the same model repo.

If these files are absent the app still runs — semantic memory just stays disabled
(AtlasContainer logs "Embedder unavailable"), matching the desktop fail-soft behaviour.
