#!/usr/bin/env python3
"""Extract audio, transcribe, rank likely funny moments, and render a compilation."""
import argparse, json, math, os, re, shutil, subprocess, sys
from pathlib import Path

def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RuntimeError(f"Command failed: {' '.join(map(str,cmd))}\n{p.stderr[-4000:]}")
    return p.stdout

def probe_duration(path):
    raw = run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)])
    return max(0.0, float(raw.strip()))

def extract_audio(video, wav):
    run(["ffmpeg","-y","-i",str(video),"-vn","-ac","1","-ar","16000","-c:a","pcm_s16le",str(wav)])

def transcribe(wav):
    try:
        from faster_whisper import WhisperModel
        model_name = os.getenv("WHISPER_MODEL","base")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, info = model.transcribe(str(wav), vad_filter=True, word_timestamps=False)
        result = []
        for s in segments:
            result.append({"start":float(s.start),"end":float(s.end),"text":s.text.strip()})
        return result
    except Exception as e:
        print(f"WARNING: transcription unavailable ({e}). Continuing with audio-only ranking.", file=sys.stderr)
        return []

FUNNY_WORDS = re.compile(r"\b(lol|lmao|haha+|hilarious|funny|joke|kidding|laugh|laughed|laughing|omg|what\?|seriously|no way|oh my god|damn|bruh|bro|idiot|stupid|crazy|wild|awkward|oops|fail|wtf)\b", re.I)

def audio_features(wav, duration, window=2.0):
    """RMS and zero-crossing/energy variation as rough excitement proxies, not semantic truth."""
    try:
        import numpy as np
        import soundfile as sf
        y, sr = sf.read(str(wav), dtype="float32")
        if y.ndim > 1: y = y.mean(axis=1)
        frames = []
        for start in [i*window for i in range(math.ceil(duration/window))]:
            chunk = y[int(start*sr):int(min(duration,start+window)*sr)]
            if len(chunk) < sr*0.15: continue
            rms = float(np.sqrt(np.mean(chunk*chunk)+1e-12))
            # RMS in dBFS, clamped for silence
            db = 20*math.log10(max(rms, 1e-6))
            zcr = float(np.mean(np.abs(np.diff(np.signbit(chunk).astype("float32")))))
            frames.append({"start":start,"end":min(duration,start+window),"rms":rms,"db":db,"zcr":zcr})
        return frames
    except Exception as e:
        print(f"WARNING: audio feature extraction unavailable: {e}", file=sys.stderr)
        return []

def build_candidates(duration, clip_length, transcript, features):
    # Sliding windows overlap by 50%; transcript and energy scores help rank likely highlights.
    step = max(2.0, clip_length * 0.5)
    starts = []
    t = 0.0
    while t < duration:
        starts.append(t)
        t += step
    rms_values = [f["rms"] for f in features]
    rms_mean = sum(rms_values)/len(rms_values) if rms_values else 0.0
    rms_sd = (sum((x-rms_mean)**2 for x in rms_values)/len(rms_values))**0.5 if rms_values else 1.0
    candidates = []
    for start in starts:
        end = min(duration, start + clip_length)
        if end - start < min(4.0, clip_length*0.5): continue
        text = " ".join(s["text"] for s in transcript if s["start"] < end and s["end"] > start)
        word_score = min(1.0, len(FUNNY_WORDS.findall(text))/3.0)
        matching_features = [f for f in features if f["start"] < end and f["end"] > start]
        if matching_features:
            # reward energy spikes relative to the whole recording
            energy = sum(max(0.0,min(3.0,(f["rms"]-rms_mean)/(rms_sd+1e-6))) for f in matching_features)/len(matching_features)/3.0
            loudness = sum(f["rms"] for f in matching_features)/len(matching_features)
            loudness_score = min(1.0, loudness/(rms_mean*2+1e-6))
        else:
            energy = loudness_score = 0.0
        # Transcript density and punctuation provide weak conversational proxies.
        density = min(1.0, len(text.split()) / max(1.0, (end-start)*2.5))
        punctuation = min(1.0, (text.count("!") + text.count("?"))/4.0)
        score = 0.45*energy + 0.20*loudness_score + 0.25*word_score + 0.07*density + 0.03*punctuation
        candidates.append({"start":round(start,2),"end":round(end,2),"score":round(score,4),"transcript":text})
    return candidates

def select_top(candidates, top_n, min_gap):
    # Greedy score order, enforce distance between clip centers to reduce duplicates.
    chosen = []
    for c in sorted(candidates, key=lambda x:x["score"], reverse=True):
        center = (c["start"]+c["end"])/2
        if all(abs(center-(x["start"]+x["end"])/2) >= min_gap for x in chosen):
            chosen.append(c)
        if len(chosen) >= top_n: break
    return sorted(chosen, key=lambda x:x["start"])

def render(video, song, moments, out_dir, music_seconds):
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(exist_ok=True)
    clip_paths = []
    for i,m in enumerate(moments,1):
        cp = clips_dir / f"moment_{i:02d}.mp4"
        run(["ffmpeg","-y","-ss",str(m["start"]),"-i",str(video),"-t",str(m["end"]-m["start"]),
             "-map","0:v:0","-map","0:a?","-c:v","libx264","-preset","veryfast","-crf","21",
             "-c:a","aac","-b:a","128k","-avoid_negative_ts","make_zero",str(cp)])
        m["clip_file"] = str(cp)
        clip_paths.append(cp)

    # Use a 4-second music-only bumper between every pair of clips (not after the last clip).
    # Song is looped/trimmed; if no song supplied, use a short black silent bumper.
    segments = []
    concat_file = out_dir / "concat.txt"
    for i,cp in enumerate(clip_paths):
        segments.append(("clip",cp))
        if i < len(clip_paths)-1 and music_seconds > 0:
            bumper = clips_dir / f"music_break_{i+1:02d}.mp4"
            if song and Path(song).exists():
                # Freeze a black frame with music, normalized to a common 1280x720 canvas.
                run(["ffmpeg","-y","-f","lavfi","-i","color=c=black:s=1280x720:r=30:d="+str(music_seconds),
                     "-stream_loop","-1","-i",str(song),"-t",str(music_seconds),
                     "-vf","format=yuv420p","-c:v","libx264","-preset","veryfast","-c:a","aac",
                     "-b:a","160k","-shortest",str(bumper)])
            else:
                run(["ffmpeg","-y","-f","lavfi","-i","color=c=black:s=1280x720:r=30:d="+str(music_seconds),
                     "-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=44100","-t",str(music_seconds),
                     "-vf","format=yuv420p","-c:v","libx264","-preset","veryfast","-c:a","aac","-shortest",str(bumper)])
            segments.append(("bumper",bumper))

    # Re-encode all segments to matching geometry/codecs for reliable concat.
    normalized=[]
    for i,(kind,p) in enumerate(segments):
        npth = clips_dir / f"normalized_{i:03d}.mp4"
        # preserve clip aspect ratio with letterboxing, standardize video/audio streams
        if kind == "clip":
            cmd=["ffmpeg","-y","-i",str(p),"-vf","scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p",
                 "-af","aresample=44100, aformat=sample_fmts=fltp:channel_layouts=stereo",
                 "-c:v","libx264","-preset","veryfast","-crf","22","-c:a","aac","-b:a","160k","-shortest",str(npth)]
        else:
            cmd=["ffmpeg","-y","-i",str(p),"-vf","scale=1280:720,fps=30,format=yuv420p",
                 "-af","aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo",
                 "-c:v","libx264","-preset","veryfast","-crf","22","-c:a","aac","-b:a","160k","-shortest",str(npth)]
        run(cmd); normalized.append(npth)
    concat_file.write_text("".join("file '"+str(p.resolve()).replace("'","'\\''")+"'\n" for p in normalized))
    final = out_dir / "final_compilation.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat_file),"-c","copy","-movflags","+faststart",str(final)])
    return final

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--config",required=True)
    args=ap.parse_args()
    cfg=json.loads(Path(args.config).read_text())
    video=Path(cfg["video_path"]).resolve()
    out=Path(cfg["output_dir"]).resolve(); out.mkdir(parents=True,exist_ok=True)
    if not video.exists(): raise FileNotFoundError(video)
    duration=probe_duration(video)
    if duration <= 0: raise ValueError("Could not determine video duration.")
    wav=out/"extracted_audio.wav"
    extract_audio(video,wav)
    transcript=transcribe(wav)
    (out/"transcript.json").write_text(json.dumps(transcript,indent=2,ensure_ascii=False))
    (out/"transcript.txt").write_text("\n".join(f'[{s["start"]:.2f}-{s["end"]:.2f}] {s["text"]}' for s in transcript),encoding="utf-8")
    features=audio_features(wav,duration)
    candidates=build_candidates(duration,float(cfg.get("clip_length",15)),transcript,features)
    moments=select_top(candidates,int(cfg.get("top_n",10)),float(cfg.get("min_gap",10)))
    if not moments: raise RuntimeError("No moments could be selected; check that the video contains a valid audio/video stream.")
    (out/"moments.json").write_text(json.dumps(moments,indent=2,ensure_ascii=False))
    final=render(video,cfg.get("song_path"),moments,out,int(cfg.get("music_seconds",4)))
    print(f"Duration: {duration:.1f}s")
    print(f"Selected {len(moments)} moments")
    for i,m in enumerate(moments,1): print(f'{i:02d}. {m["start"]:.1f}-{m["end"]:.1f}s score={m["score"]:.3f} {m["transcript"][:120]}')
    print(f"Final output: {final}")

if __name__=="__main__":
    main()
