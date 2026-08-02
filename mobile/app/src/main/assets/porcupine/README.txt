Put the "Hey Atlas" wake-word file here:

  Hey-Atlas.ppn

How to get it (free, ~5 min):
  1. Sign in at https://console.picovoice.ai (free account) and copy your AccessKey.
  2. Console → Porcupine → "Create Wake Word" → type "Hey Atlas" → platform ANDROID →
     train → download the .ppn. Make sure it targets Porcupine v3 (the SDK this app uses).
  3. Rename it to Hey-Atlas.ppn and drop it in this folder.
  4. Paste the AccessKey in the app: ⚙ Settings → "Picovoice AccessKey".

Without this file (and the key) the wake word is disabled; the rest of the app works.
This file is gitignored (per-device), so it won't be committed.
