# BestNAI

BestNAI is a production-oriented NovelAI image generation gateway with a built-in web UI.

## For users

Start here:
- `docs/USER_GUIDE.md` — user guide and workflow

## For deployment

- Docker supported
- Default deployment mode uses `BESTNAI_DEFAULT_MAX_ANLAS=0`
- Supported fixed resolutions and official-like streaming are built in

## Main features

- Text-to-image
- Image-to-image
- Inpainting
- Character Reference
- Vibe Transfer / ControlNet
- Multi-character positioning
- Realtime Anlas estimation
- Streaming generation
- Upload and reuse via `upload_id:<id>`
- JSON / binary / ZIP output
- BYOK support

## Run

```bash
pip install -r requirements.txt
copy .env.example .env
python run.py
```

Open:

```text
http://127.0.0.1:8010/
```
