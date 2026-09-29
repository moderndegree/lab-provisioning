# voice-chatter

OpenAI-compatible TTS for the lab presence. Resemble AI's **Chatterbox Turbo**
on the workstation GPU, zero-shot cloned from a short reference clip.

The gateway already talks `/v1/audio/speech` with `response_format: pcm`. This
process is the other end of that call. Piper on ser5 stays as the fallback so
the presence still has a voice when this box is asleep.

## Run (workstation, RTX 4080)

```bash
uv venv ~/.local/share/voice-chatter/venv --python 3.12
UV_PROJECT_ENVIRONMENT=~/.local/share/voice-chatter/venv uv pip install \
  torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
UV_PROJECT_ENVIRONMENT=~/.local/share/voice-chatter/venv uv pip install \
  -e "packages/voice-chatter[engine]"
mkdir -p ~/.local/share/voice-chatter/voices
# optional: drop a 5+ second wav, named for the voice the gateway will request
# cp ~/presence.wav ~/.local/share/voice-chatter/voices/presence.wav

CHATTER_VOICES_DIR=~/.local/share/voice-chatter/voices \
  ~/.local/share/voice-chatter/venv/bin/python -m voice_chatter
```

Or as a user unit that survives a closed terminal:

```bash
cp packages/voice-chatter/voice-chatter.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now voice-chatter
```

Listens on `0.0.0.0:8774` (tailnet name `desktop`). ser5 points
`VOICE_TTS_URL=http://desktop:8774` at it.

## Clone a voice

Files in `$CHATTER_VOICES_DIR`, five seconds or longer. The OpenAI `voice`
field is the filename stem:

| file | request |
|---|---|
| *(none)* | `"voice": "default"` — Chatterbox built-in speaker |
| `presence.wav` | `"voice": "presence"` |

Replace the file and the next request re-clones; there is no name map in code.

## Env

| var | default | |
|---|---|---|
| `CHATTER_HOST` | `0.0.0.0` | |
| `CHATTER_PORT` | `8774` | |
| `CHATTER_VOICES_DIR` | `~/.local/share/voice-chatter/voices` | |
| `CHATTER_DEVICE` | `auto` (`cuda` / `mps` / `cpu`) | |
| `CHATTER_VARIANT` | `turbo` (`nano` for CPU) | |
| `CHATTER_DEFAULT_VOICE` | `default` | |
