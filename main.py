# Pakistani Comedy Shorts Studio â CLI/GitHub Action Version
import os, sys, re, json, time, shutil, subprocess, traceback, zipfile
from pathlib import Path
import yt_dlp
import requests
from faster_whisper import WhisperModel
from dotenv import load_dotenv

load_dotenv()

# Configuration from environment variables
SOURCE_URL = os.getenv('SOURCE_URL', 'https://www.youtube.com/watch?v=ZKx6VvrVXtc')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
SHOW_NAME = os.getenv('SHOW_NAME', 'Hansna Mana Hai')
AI_MODEL = os.getenv('AI_MODEL', 'google/gemma-4-26b-a4b-it:free')
WHISPER_MODEL_NAME = os.getenv('WHISPER_MODEL', 'small')
LANG_CODE = os.getenv('LANGUAGE', 'ur')
CLIP_COUNT = int(os.getenv('CLIP_COUNT', '5'))
MIN_LEN = int(os.getenv('MIN_LEN', '18'))
MAX_LEN = int(os.getenv('MAX_LEN', '55'))
BURN_SUBS = os.getenv('BURN_SUBS', 'True').lower() == 'true'
BLUR_BG = os.getenv('BLUR_BG', 'True').lower() == 'true'

# YouTube Cookies handling
COOKIES_FILE = Path('cookies.txt')
if os.getenv('YOUTUBE_COOKIES'):
    COOKIES_FILE.write_text(os.getenv('YOUTUBE_COOKIES'))

def cmd(args, capture=False, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=capture)

class QuietProgressLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): print(f"yt-dlp: {msg}")

def ydl_common():
    opts = {"quiet": True, "no_warnings": True, "logger": QuietProgressLogger(), "noplaylist": True}
    if COOKIES_FILE.exists():
        opts['cookiefile'] = str(COOKIES_FILE)
    return opts

def download_audio_only(video_url, job):
    template = str(job / "source_audio.%(ext)s")
    opts = ydl_common()
    opts.update({"format": "bestaudio[ext=m4a]/bestaudio", "outtmpl": template,
                 "overwrites": True, "continuedl": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([video_url])
    audio_files = [p for p in job.glob("source_audio.*") if p.suffix.lower() in (".m4a", ".webm", ".opus", ".mp3", ".aac", ".ogg")]
    if not audio_files:
        raise RuntimeError("yt-dlp did not produce an audio-only file.")
    return max(audio_files, key=lambda p: p.stat().st_size)

def download_video_range(video_url, start_sec, end_sec, target_dir):
    target_dir.mkdir(parents=True, exist_ok=True)
    template = str(target_dir / "selected_scene.%(ext)s")
    opts = ydl_common()
    opts.update({
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]",
        "outtmpl": template,
        "merge_output_format": "mp4",
        "download_ranges": yt_dlp.utils.download_range_func(None, [(float(start_sec), float(end_sec))]),
        "force_keyframes_at_cuts": True,
        "overwrites": True,
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([video_url])
    clips = [p for p in target_dir.glob("selected_scene.*") if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if not clips: raise RuntimeError(f"Could not download range {start_sec}-{end_sec}s.")
    clip = max(clips, key=lambda p: p.stat().st_size)
    return clip

ROOT = Path('pakistani_comedy_shorts'); ROOT.mkdir(parents=True, exist_ok=True)
SRC = ROOT/'source'; OUT = ROOT/'shorts'; REP = ROOT/'reports'
for p in (SRC, OUT, REP): p.mkdir(parents=True, exist_ok=True)
SHOWS = {'Hansna Mana Hai':['hansna mana hai','hasna mana hai'], 'Mazaaq Raat':['mazaaq raat','mazak raat','mazÄq raat']}

def parse_json(s):
    s = re.sub(r'^```(?:json)?\s*|\s*```$', '', s.strip(), flags=re.I)
    a, b = s.find('{'), s.rfind('}')
    return json.loads(s[a:b+1] if a>=0 and b>a else s)

def api(messages, max_tokens=1800):
    r = requests.post('https://openrouter.ai/api/v1/chat/completions', 
                      headers={'Authorization': 'Bearer ' + OPENROUTER_API_KEY, 'Content-Type': 'application/json'},
                      json={'model': AI_MODEL, 'messages': messages, 'temperature': 0.15, 'max_tokens': max_tokens}, timeout=120)
    if not r.ok: raise RuntimeError(f'OpenRouter error {r.status_code}: {r.text[:350]}')
    return r.json()['choices'][0]['message']['content']

def transcribe(wav):
    try: wm = WhisperModel(WHISPER_MODEL_NAME, device='cuda', compute_type='float16')
    except Exception: wm = WhisperModel(WHISPER_MODEL_NAME, device='cpu', compute_type='int8')
    segs, info = wm.transcribe(str(wav), language=None if LANG_CODE=='auto' else LANG_CODE, vad_filter=True)
    return [{'start':round(float(s.start),2),'end':round(float(s.end),2),'text':s.text.strip()} for s in segs if s.text.strip()], getattr(info,'language',LANG_CODE)

def chunks(rows, duration, seconds=210, overlap=20):
    out = []; start = 0.0
    while start < duration:
        end = min(duration, start + seconds); part = [x for x in rows if x['end']>=start and x['start']<=end]
        if part: out.append((start, end, part))
        if end >= duration: break
        start = end - overlap
    return out

def find_candidates(rows, duration, minlen, maxlen):
    found = []; parts = chunks(rows, duration)
    for i, (cs, ce, part) in enumerate(parts, 1):
        transcript = '\n'.join(f"[{x['start']:.2f}-{x['end']:.2f}] {x['text']}" for x in part)
        prompt = f'''Show: {SHOW_NAME}. Find funny scenes in this Urdu comedy transcript. Return JSON only: {{"candidates":[{{"start":12.2,"end":40.5,"score":90,"reason":"text"}}]}}. TRANSCRIPT:\n{transcript}'''
        try:
            data = parse_json(api([{'role':'system','content':'Select comedy scenes. Return valid JSON only.'},{'role':'user','content':prompt}]))
            for c in data.get('candidates', []):
                a, b = float(c['start']), float(c['end'])
                if a>=0 and b<=duration and minlen<=b-a<=maxlen: found.append(c)
        except Exception as e: print(f'Chunk {i} skipped: {e}')
    return found

def review(candidates, rows, duration, minlen, maxlen):
    kept = []
    for c in sorted(candidates, key=lambda x: x.get('score', 0), reverse=True):
        prompt = f'''Review clip from {SHOW_NAME}. Clip={json.dumps(c)}. Return JSON: {{"keep":true,"start":{c['start']},"end":{c['end']},"quality_score":90}}'''
        try:
            r = parse_json(api([{'role':'system','content':'Reviewer. JSON only.'},{'role':'user','content':prompt}], 500))
            if r.get('keep') and r.get('quality_score', 0) >= 60: kept.append(c)
        except: continue
    unique = []
    for c in sorted(kept, key=lambda x: x.get('score', 0), reverse=True):
        if all(min(c['end'], q['end']) - max(c['start'], q['start']) < 5 for q in unique):
            unique.append(c)
        if len(unique) >= CLIP_COUNT: break
    return sorted(unique, key=lambda x: x['start'])

def write_srt(rows, start, end, path):
    chosen = [r for r in rows if r['end']>start and r['start']<end]
    with open(path, 'w', encoding='utf-8') as f:
        for i, r in enumerate(chosen, 1):
            a, b = max(0, r['start']-start), min(end-start, r['end']-start)
            f.write(f"{i}\n{time.strftime('%H:%M:%S,000', time.gmtime(a))} --> {time.strftime('%H:%M:%S,000', time.gmtime(b))}\n{r['text']}\n\n")

def render(clip_video, rows, c, index):
    start, end = c['start'], c['end']; duration = end - start
    srt = OUT/f'clip_{index:02}.srt'; silent = OUT/f'tmp_{index:02}.mp4'; final = OUT/f'clip_{index:02}.mp4'
    write_srt(rows, start, end, srt)
    vf = 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1'
    if BLUR_BG: vf = '[0:v]split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:10[blur];[fg]scale=1080:1920:force_original_aspect_ratio=decrease[sharp];[blur][sharp]overlay=(W-w)/2:(H-h)/2,setsar=1'
    if BURN_SUBS:
        s = str(srt).replace('\\','/').replace(':','\\:')
        vf += f",subtitles='{s}':force_style='FontSize=20,Alignment=2'"
    cmd(['ffmpeg', '-y', '-i', str(clip_video), '-t', f'{duration:.3f}', '-vf', vf, '-c:v', 'libx264', '-crf', '23', '-preset', 'veryfast', str(silent)])
    cmd(['ffmpeg', '-y', '-i', str(silent), '-i', str(clip_video), '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-shortest', str(final)])
    return final

def run():
    try:
        if not SOURCE_URL or not OPENROUTER_API_KEY: raise ValueError("Missing SOURCE_URL or OPENROUTER_API_KEY.")
        job = SRC/str(int(time.time())); job.mkdir(parents=True, exist_ok=True)
        print(f"Processing: {SOURCE_URL}")
        info = json.loads(cmd(['yt-dlp', '--dump-single-json', '--no-playlist', SOURCE_URL], capture=True).stdout)
        duration = float(info.get('duration', 0))
        audio_source = download_audio_only(SOURCE_URL, job)
        wav = job/'audio.wav'
        cmd(['ffmpeg', '-y', '-i', str(audio_source), '-ac', '1', '-ar', '16000', str(wav)])
        rows, detected = transcribe(wav)
        candidates = find_candidates(rows, duration, MIN_LEN, MAX_LEN)
        selected = review(candidates, rows, duration, MIN_LEN, MAX_LEN)
        rendered = []
        for i, c in enumerate(selected, 1):
            clip_video = download_video_range(SOURCE_URL, c['start'], c['end'], job/f'scene_{i}')
            rendered.append(render(clip_video, rows, c, i))
        print(f"Done! Created {len(rendered)} shorts.")
    except Exception as e: print(f"Error: {e}"); traceback.print_exc()

if __name__ == "__main__":
    run()