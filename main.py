import os
import sys
import json
import time
import random
import re
import torch
import subprocess
import argparse
import requests
from base64 import b64encode

# Ensure dependencies are loaded
try:
    import yt_dlp
    import whisper
    import moviepy
    from moviepy.editor import VideoFileClip, concatenate_videoclips, vfx, afx
    from google import genai
except ImportError:
    print("Installing dependencies...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "yt-dlp", "moviepy", "requests", "google-genai", "git+https://github.com/openai/whisper.git"], check=True)
    import yt_dlp
    import whisper
    from moviepy.editor import VideoFileClip, concatenate_videoclips, vfx, afx
    from google import genai

# System dependency check for CI
def check_system_deps():
    if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0:
        print("Installing system FFmpeg...")
        subprocess.run(["sudo", "apt-get", "-qq", "update"], check=True)
        subprocess.run(["sudo", "apt-get", "-qq", "install", "-y", "ffmpeg"], check=True)

    if subprocess.run(["which", "deno"], capture_output=True).returncode != 0:
        print("Installing Deno JS Runtime...")
        subprocess.run("curl -fsSL https://deno.land/install.sh | sh", shell=True, check=True)
        os.environ["PATH"] += ":" + os.path.expanduser("~/.deno/bin")

# --- CONFIG ---
DEFAULT_URL = "https://www.youtube.com/watch?v=4-dRxvg7Okk"
OUTPUT_FILENAME = "bulbulay_unified_remix.mp4"
TRANSCRIPT_CACHE = "downloads/transcript_cache.json"
AUDIO_PATH = "downloads/temp_audio.m4a"
TARGET_W, TARGET_H = 1080, 1920
SLOW_MO_SPEED = 0.45
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

os.makedirs("downloads", exist_ok=True)
os.makedirs("clips_unified", exist_ok=True)

# Deno Worker Script
WITH_DENO_SCRIPT = """
import { parse } from "https://deno.land/std@0.177.0/flags/mod.ts";
const args = parse(Deno.args);
const videoUrl = args.url;
const outputPath = args.out;
const mode = args.mode || "audio";

try {
  const videoId = new URL(videoUrl).searchParams.get("v");
  if (!videoId) throw new Error("Invalid YouTube URL");

  const res = await fetch("https://www.youtube.com/youtubei/v1/player", {
    method: "POST",
    headers: {
      "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      videoId,
      context: { client: { clientName: "ANDROID", clientVersion: "17.31.35", hl: "en", gl: "US" } }
    })
  });

  const data = await res.json();
  const adaptiveFormats = data.streamingData?.adaptiveFormats || [];
  let selectedFormat = null;

  if (mode === "audio") {
    selectedFormat = adaptiveFormats.find((f: any) => f.mimeType.includes("audio/mp4"));
  } else {
    selectedFormat = adaptiveFormats.find((f: any) => f.mimeType.includes("video/mp4") && f.height <= 480) ||
                     adaptiveFormats.find((f: any) => f.mimeType.includes("video/mp4")) ||
                     data.streamingData?.formats?.find((f: any) => f.mimeType.includes("video/mp4"));
  }

  if (!selectedFormat || !selectedFormat.url) {
    throw new Error("Could not retrieve direct stream URL via Deno.");
  }

  const streamResponse = await fetch(selectedFormat.url);
  const file = await Deno.open(outputPath, { write: true, create: true, truncate: true });
  await streamResponse.body?.pipeTo(file.writable);
} catch (err) {
  Deno.exit(1);
}
"""

with open("download_worker.ts", "w", encoding="utf-8") as f:
    f.write(WITH_DENO_SCRIPT)

def download_audio_deno_clean(url):
    if os.path.exists(AUDIO_PATH):
        print("Found cached audio track, skipping download.")
        return
    
    print(f"Downloading audio stream from {url}...")
    result = subprocess.run([
        os.path.expanduser("~/.deno/bin/deno"), "run", "--allow-net", "--allow-read", "--allow-write",
        "download_worker.ts", "--url", url, "--out", AUDIO_PATH, "--mode", "audio"
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        print("Deno download issue. Falling back to yt-dlp...")
        ydl_opts = {'format': 'bestaudio[ext=m4a]/bestaudio', 'outtmpl': AUDIO_PATH, 'overwrites': True, 'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    else:
        print("Audio stream saved.")

def download_video_segment_deno_clean(url, output_path, start_t, end_t):
    temp_full_video = "downloads/temp_full_video.mp4"
    if not os.path.exists(temp_full_video):
        print("Fetching full episode stream via Deno...")
        result = subprocess.run([
            os.path.expanduser("~/.deno/bin/deno"), "run", "--allow-net", "--allow-read", "--allow-write",
            "download_worker.ts", "--url", url, "--out", temp_full_video, "--mode", "video"
        ], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError("Failed to acquire YouTube video source stream.")

    subprocess.run([
        "ffmpeg", "-y", "-ss", str(start_t), "-to", str(end_t),
        "-i", temp_full_video, "-c:v", "copy", "-c:a", "copy", output_path
    ], capture_output=True, check=True)

def run_whisper_transcription():
    if os.path.exists(TRANSCRIPT_CACHE):
        print("Loading Urdu transcript from cache...")
        with open(TRANSCRIPT_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
            
    print(f"Transcribing with Whisper on {DEVICE.upper()}...")
    model = whisper.load_model("tiny", device=DEVICE)
    result = model.transcribe(AUDIO_PATH, language="ur")
    
    with open(TRANSCRIPT_CACHE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result

def get_local_heuristics(whisper_data):
    selected_scenes = []
    for seg in whisper_data.get("segments", []):
        text = seg["text"].strip()
        start, end = seg["start"], seg["end"]
        speaking_speed = len(text) / max(0.1, end - start)
        score = 0
        if any(char in text for char in ["!", "؟", "?"]): score += 5
        if speaking_speed > 4.0: score += 3
        if any(k in text for k in ["مومو", "خوبصورت", "محمود", "نبیل", "ہائے", "ارے"]): score += 4
        if score >= 5:
            selected_scenes.append({"start": max(0, start - 9.0), "end": min(start + 5.0, end + 3.0), "reason": f"Score: {score}"})
    return selected_scenes

def get_gemini_selection(whisper_data, api_key):
    client = genai.Client(api_key=api_key)
    formatted = [{"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()} for s in whisper_data.get("segments", [])]
    prompt = f"Analyze these Urdu transcript segments and select the top 4 funniest scenes. Duration 10-15s. Raw JSON list output only: {json.dumps(formatted, ensure_ascii=False)}"
    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
    text_res = response.text.strip().strip('`').replace('json', '').strip()
    return json.loads(text_res)

def get_openrouter_selection(whisper_data, api_key):
    formatted = [{"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()} for s in whisper_data.get("segments", [])]
    prompt = f"Urdu Sitcom. Find 4 funniest parts (10-15s). JSON list only with start, end, reason.\nSegments: {json.dumps(formatted, ensure_ascii=False)}"
    r = requests.post("https://openrouter.ai/api/v1/chat/completions", 
                      headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, 
                      json={"model": "thinkingmachines/inkling-small:free", "messages": [{"role": "user", "content": prompt}]})
    res = r.json()["choices"][0]["message"]["content"].strip().strip('`').replace('json', '').strip()
    return json.loads(res)

def convert_to_vertical(clip):
    scaled = clip.resize(height=TARGET_H)
    x_center = scaled.w / 2
    cropped = scaled.crop(x1=max(0, x_center - (TARGET_W / 2)), y1=0, x2=min(scaled.w, x_center + (TARGET_W / 2)), y2=TARGET_H)
    return cropped.resize((TARGET_W, TARGET_H))

def main():
    parser = argparse.ArgumentParser(description="Bulbulay Vertical Comedy Pipeline (Headless)")
    parser.add_argument("--url", default=DEFAULT_URL, help="YouTube Video URL")
    parser.add_argument("--mode", choices=['local', 'gemini', 'openrouter'], default='local', help="Selection algorithm")
    parser.add_argument("--gemini_key", default=os.environ.get('GOOGLE_API_KEY'), help="Gemini API Key")
    parser.add_argument("--openrouter_key", default=os.environ.get('OPENROUTER_API_KEY'), help="OpenRouter API Key")
    args = parser.parse_args()

    check_system_deps()
    
    try:
        download_audio_deno_clean(args.url)
        whisper_data = run_whisper_transcription()

        print(f"Processing with mode: {args.mode}")
        scenes = []
        if args.mode == 'gemini' and args.gemini_key:
            scenes = get_gemini_selection(whisper_data, args.gemini_key)
        elif args.mode == 'openrouter' and args.openrouter_key:
            scenes = get_openrouter_selection(whisper_data, args.openrouter_key)
        else:
            scenes = get_local_heuristics(whisper_data)

        unique_scenes = []
        for s in scenes:
            if len(unique_scenes) >= 4: break
            if not any(not (s['end'] < u['start'] or s['start'] > u['end']) for u in unique_scenes):
                unique_scenes.append(s)

        print(f"Selected {len(unique_scenes)} scenes.")
        processed_clips = []
        for idx, scene in enumerate(unique_scenes):
            start_t, end_t = float(scene["start"]), float(scene["end"])
            clip_out = f"clips_unified/scene_{idx}.mp4"
            print(f"Cutting scene {idx+1}: {start_t}s to {end_t}s")
            download_video_segment_deno_clean(args.url, clip_out, start_t, end_t)
            
            s_clip = VideoFileClip(clip_out)
            if s_clip.duration > 2.0:
                split = s_clip.duration * 0.75
                dialogue = convert_to_vertical(s_clip.subclip(0, split))
                reaction = convert_to_vertical(s_clip.subclip(split, s_clip.duration)).fx(vfx.speedx, SLOW_MO_SPEED).fx(afx.audio_fadeout, 0.3)
                processed_clips.append(concatenate_videoclips([dialogue, reaction]))

        if processed_clips:
            print("Generating final vertical video...")
            final = concatenate_videoclips(processed_clips, method="compose")
            final.write_videofile(OUTPUT_FILENAME, codec="libx264", audio_codec="aac", fps=30, verbose=False, logger=None)
            print(f"Pipeline Complete! File: {OUTPUT_FILENAME}")
        else:
            print("No clips generated.")

    except Exception as e:
        print(f"Fatal Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
