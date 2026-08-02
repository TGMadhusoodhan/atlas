Put an unzipped Vosk speech-recognition model in THIS folder (assets/vosk-model/).

Recommended (small, ~40 MB): vosk-model-small-en-us-0.15
  1. Download from https://alphacephei.com/vosk/models
  2. Unzip it.
  3. Copy the CONTENTS of the unzipped folder directly into assets/vosk-model/ so this
     folder ends up containing: am/  conf/  graph/  ivector/  README (etc.)
     — not a nested vosk-model-small-en-us-0.15/ subfolder.

At first run the app unpacks this into app storage and loads it (fully offline STT).
Without it, voice STT is disabled; the rest of the app works.
The model files are gitignored (large), so they won't be committed.
