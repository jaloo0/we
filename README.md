# FunnyCut Studio — video funny-moment pipeline

A self-hostable Streamlit dashboard plus a Python/FFmpeg pipeline. Upload a video, transcribe its audio, rank up to 10 candidate moments, cut clips, and assemble them with a configurable music-only break (4 seconds by default) between clips.

## What is included

- `app.py`: dashboard, upload form, job history, outputs, reusable song library, optional GitHub Actions dispatch.
- `pipeline.py`: audio extraction, Whisper transcription, heuristic scoring, clip extraction, and final compilation.
- `.github/workflows/process-video.yml`: manually dispatched GitHub Actions workflow.
- `Dockerfile` and `docker-compose.yml`: reproducible local deployment.
- `requirements.txt` and `.env.example`.

## Important accuracy note

There is no universally reliable automatic “funny” detector. This starter uses speech-to-text cues and audio energy as weak signals, then chooses non-overlapping windows. It ranks *likely highlights*, not objectively funny scenes. A true production system should add a trained laughter detector, face/reaction cues, and a human review step. The dashboard shows the selected time ranges and transcript so you can review them.

## Requirements

- Python 3.11 recommended
- FFmpeg and FFprobe installed and available on `PATH`
- 4 GB+ RAM recommended for the default Whisper model; CPU processing can be slow
- Internet connection on first run to download the Whisper model

Install FFmpeg:
- Ubuntu/Debian: `sudo apt-get update && sudo apt-get install -y ffmpeg`
- macOS with Homebrew: `brew install ffmpeg`
- Windows: install an FFmpeg build and add its `bin` directory to `PATH`.

## Run locally

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open the local Streamlit address printed in the terminal. Upload a video and click **Start processing**. Outputs are saved under `data/outputs/<job_id>/`.

## Run with Docker

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:8501`.

## Output files

Each job folder contains:
- `extracted_audio.wav`
- `transcript.json` and `transcript.txt`
- `moments.json` — chosen timestamps, scores, and transcript text
- `clips/moment_01.mp4` etc.
- `final_compilation.mp4`
- Intermediate normalized segments and `concat.txt`

Music breaks appear **between** selected clips, not after the final clip. They are black video with the selected song for 4 seconds (or silence if no song was selected). If you want the song laid over footage instead, adapt `render()` in `pipeline.py`.

## GitHub Actions mode

GitHub-hosted runners cannot access a file on your computer or a local Streamlit server. The input video (and optional song) must be available to the runner through a public URL or short-lived signed URL. For private videos, use object storage such as an S3-compatible bucket and create expiring signed URLs.

1. Push this project to a GitHub repository.
2. In repository **Settings → Actions → General**, ensure workflow permissions allow the workflow to run. The workflow only needs read access to repository contents.
3. Enable Actions and commit `.github/workflows/process-video.yml`.
4. Create a fine-grained GitHub token with **Actions: write** permission for this repository.
5. Set `GITHUB_TOKEN` and `GITHUB_REPOSITORY=owner/repo` in the Streamlit host environment. Do not commit the token or put it in browser-side code.
6. In the dashboard, check **Dispatch processing to GitHub Actions**, provide a reachable input video URL, then dispatch.

The workflow uses `workflow_dispatch` inputs and uploads the result directory as a GitHub Actions artifact for 14 days. Download artifacts from the repository's Actions run page. The starter dashboard does not automatically import remote artifacts into its local job list; that requires GitHub API artifact download/polling or object-storage output upload.

### Security checklist for public deployment

- Do not expose the dashboard directly to the public internet without authentication.
- Keep GitHub tokens server-side, use least-privilege tokens, rotate them, and never put secrets in repository files.
- Signed URLs can appear in workflow logs. Use short expiry and avoid logging full URLs.
- Set upload-size limits and clean up old jobs for production.
- Confirm you have permission to process the video and use the music track.

## Troubleshooting

- `ffmpeg not found`: install FFmpeg and verify `ffmpeg -version` and `ffprobe -version`.
- Model download is slow: set `WHISPER_MODEL=tiny` for faster, less accurate transcription.
- No audio track: audio extraction may fail; this pipeline expects a video with an audio stream.
- Processing is slow: CPU Whisper can be slow on long recordings. Use a smaller model or a machine with a supported GPU.
- Output concat error: check that the input is a valid video; ensure disk space is available.

## Suggested production upgrades

1. Use an actual laughter/non-speech classifier instead of keyword heuristics.
2. Add scene-change detection and keep clip boundaries aligned to shot changes.
3. Add authenticated job storage and persistent database (SQLite/Postgres).
4. Put uploads and outputs in S3/R2; store signed URLs, not local paths, for distributed workers.
5. Poll GitHub run status and automatically import artifacts, or have Actions upload outputs to object storage.
6. Add subtitles, title cards, aspect-ratio presets, loudness normalization, and a human review timeline.
