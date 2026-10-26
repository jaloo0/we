import os, json, requests, subprocess, tempfile
from pathlib import Path
import streamlit as st
import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
OUTPUTS = DATA / "outputs"
SONGS = DATA / "songs"
for p in (UPLOADS, OUTPUTS, SONGS): p.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="FunnyCut Studio", page_icon="🎬", layout="wide")
st.title("🎬 FunnyCut Studio")
st.caption("Upload a video, find likely funny moments, and render a compilation with short music breaks.")

with st.sidebar:
    st.header("Pipeline settings")
    top_n = st.slider("Moments to extract", 1, 10, 10)
    clip_len = st.slider("Target clip length (seconds)", 5, 35, 15)
    music_seconds = st.slider("Music break between clips (seconds)", 1, 8, 4)
    min_gap = st.slider("Minimum gap between selected moments (seconds)", 3, 30, 10)
    st.info("Funny-moment ranking is heuristic: laughter cues, energetic audio, and transcript signals. Review the clips before publishing.")

tab1, tab2, tab3 = st.tabs(["New job", "Jobs & outputs", "Music library"])
with tab1:
    uploaded = st.file_uploader("Upload a video", type=["mp4","mov","mkv","webm","avi","m4v"])
    song_files = sorted([p for p in SONGS.iterdir() if p.is_file() and p.suffix.lower() in (".mp3",".wav",".m4a",".aac",".ogg")])
    song_choice = st.selectbox("Music break song", ["No song"] + [p.name for p in song_files])
    new_song = st.file_uploader("Or upload a song for this job", type=["mp3","wav","m4a","aac","ogg"], key="job_song")
    repo = st.text_input("GitHub repository (owner/repo)", value=os.getenv("GITHUB_REPOSITORY", ""))
    workflow = st.text_input("Workflow file", value="process-video.yml")
    branch = st.text_input("Branch", value="main")
    run_github = st.checkbox("Dispatch processing to GitHub Actions", value=False)
    if uploaded and st.button("🚀 Start processing", type="primary"):
        safe_name = Path(uploaded.name).name
        input_path = UPLOADS / safe_name
        input_path.write_bytes(uploaded.getbuffer())
        job_id = input_path.stem + "_" + str(int(__import__("time").time()))
        job_dir = OUTPUTS / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        song_path = None
        if new_song:
            song_path = job_dir / Path(new_song.name).name
            song_path.write_bytes(new_song.getbuffer())
        elif song_choice != "No song":
            song_path = SONGS / song_choice
        config = {
            "job_id": job_id, "video_path": str(input_path), "output_dir": str(job_dir),
            "top_n": int(top_n), "clip_length": int(clip_len), "music_seconds": int(music_seconds),
            "min_gap": int(min_gap), "song_path": str(song_path) if song_path else None
        }
        (job_dir / "job.json").write_text(json.dumps(config, indent=2))
        if run_github:
            token = os.getenv("GH_TOKEN", "")
            if not token or not repo or "/" not in repo:
                st.error("Set GITHUB_TOKEN in the Streamlit environment and enter owner/repo.")
            else:
                api = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
                # GitHub-hosted runners cannot read a local Streamlit upload. The workflow
                # needs a reachable input URL or object-storage integration to fetch the video.
                input_url = st.text_input("Publicly reachable video URL for GitHub runner", key="gh_url")
                st.warning("For GitHub Actions, upload the video to accessible object storage and provide its signed/public URL. A local file path is not accessible to GitHub.")
                if input_url and st.button("Dispatch GitHub workflow"):
                    r = requests.post(api, headers={"Authorization": f"Bearer {token}", "Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28"}, json={"ref": branch, "inputs":{"video_url":input_url, "job_id":job_id}})
                    if r.status_code == 204: st.success("GitHub Actions workflow dispatched.")
                    else: st.error(f"GitHub API error {r.status_code}: {r.text}")
        else:
            st.info("Processing locally. This may take several minutes on the first run while speech models download.")
            try:
                result = subprocess.run(
                    ["python", str(ROOT / "pipeline.py"), "--config", str(job_dir / "job.json")],
                    capture_output=True, text=True, timeout=60*60*6
                )
                if result.returncode == 0:
                    st.success("Processing complete.")
                    st.code(result.stdout[-5000:])
                else:
                    st.error("Processing failed.")
                    st.code(result.stderr[-6000:])
            except subprocess.TimeoutExpired:
                st.error("Processing exceeded the 6-hour dashboard limit. Run pipeline.py from a terminal instead.")

with tab2:
    jobs = sorted([p for p in OUTPUTS.iterdir() if p.is_dir()], key=lambda p:p.stat().st_mtime, reverse=True)
    if not jobs: st.info("No jobs yet. Upload a video in New job.")
    for job in jobs:
        with st.expander(job.name, expanded=False):
            result_file = job / "moments.json"
            final_video = job / "final_compilation.mp4"
            if result_file.exists():
                moments = json.loads(result_file.read_text())
                st.dataframe(pd.DataFrame(moments), use_container_width=True)
            if final_video.exists():
                st.video(str(final_video))
                st.download_button("Download final compilation", final_video.read_bytes(), file_name=final_video.name, mime="video/mp4", key=f"dl_{job.name}")
            for p in sorted(job.iterdir()):
                if p.suffix.lower() in (".mp4", ".wav", ".json", ".srt", ".txt") and p.name not in ("final_compilation.mp4",):
                    st.caption(p.name)
                    if p.suffix.lower() == ".mp4": st.video(str(p))
                    elif p.suffix.lower() in (".txt",".srt"): st.download_button(f"Download {p.name}", p.read_bytes(), file_name=p.name, key=f"file_{job.name}_{p.name}")

with tab3:
    st.write("Upload reusable music tracks to the local music library.")
    song_upload = st.file_uploader("Add a song", type=["mp3","wav","m4a","aac","ogg"], key="library_song")
    if song_upload and st.button("Save to music library"):
        dest = SONGS / Path(song_upload.name).name
        dest.write_bytes(song_upload.getbuffer())
        st.success(f"Saved {dest.name}")
    if song_files:
        st.write("Available tracks:")
        for p in song_files: st.write("🎵", p.name)
