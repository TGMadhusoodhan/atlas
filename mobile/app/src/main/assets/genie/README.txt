The on-device NPU model is NOT bundled in the APK (too large). It is copied into
app-private storage (filesDir/genie/) on first run. Expected contents:

  genie_config.json   — Genie dialog config (points to the QNN model + tokenizer)
  <model>.bin / .serialized — the QNN context binary compiled for Snapdragon 8 Elite Gen 5
  tokenizer.json      — the model tokenizer

Produce these with Qualcomm AI Hub + the QNN SDK — see docs/GENIE_SETUP.md.
Until they exist, GenieEngine.isAvailable() is false and ATLAS uses the offline
placeholder / DeepSeek.
