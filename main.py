# Pakistani Comedy Shorts Studio — one-cell Colab source
# Prefer opening the .ipynb in Google Colab for the widget interface.

#@title 🇵🇰 Pakistani Comedy Shorts Studio — Hansna Mana Hai / Mazaaq Raat
# Run this ONE cell in Google Colab. Select the show, paste a public YouTube URL, enter an OpenRouter key, click Create Shorts.
# Use only source videos you have permission to edit and publish.

import os, sys, re, json, time, shutil, subprocess, traceback, zipfile
import yt_dlp
from pathlib import Path

def cmd(args, capture=False, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=capture)

print('Installing required tools...')
cmd([sys.executable, '-m', 'pip', 'install', '-q', 'yt-dlp', 'faster-whisper', 'requests', 'ipywidgets'])
cmd(['apt-get', '-qq', 'install', '-y', 'ffmpeg'], check=False, capture=True)
import requests, ipywidgets as W
from IPython.display import display, clear_output
from faster_whisper import WhisperModel

class QuietProgressLogger:
    """Keep yt-dlp progress chatter from flooding or freezing Colab output."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): print(f"yt-dlp: {msg}")

def ydl_common():
    return {"quiet": True, "no_warnings": True, "logger": QuietProgressLogger(), "noplaylist": True}

def download_audio_only(video_url, job):
    """Download only the episode audio; never fetch the full-length video."""
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
    """Download only one selected timestamp range, not the complete episode."""
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
    if not clips:
        raise RuntimeError(f"Could not download selected video range {start_sec:.1f}-{end_sec:.1f}s. The source may not support range downloading.")
    clip = max(clips, key=lambda p: p.stat().st_size)
    # Guard against extractors that fail to cut the requested section. Never process an episode-sized file.
    probe = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(clip)],
                           text=True, capture_output=True, check=False)
    try:
        downloaded_duration = float(probe.stdout.strip())
    except (TypeError, ValueError):
        clip.unlink(missing_ok=True)
        raise RuntimeError("Could not verify the selected-range download duration; deleted it to avoid processing a full episode.")
    requested_duration = float(end_sec) - float(start_sec)
    if downloaded_duration > requested_duration + 20:
        clip.unlink(missing_ok=True)
        raise RuntimeError(f"Range download was {downloaded_duration:.1f}s, far longer than the requested {requested_duration:.1f}s scene. The source/extractor may not support range downloads; no full-length file will be processed.")
    return clip

ROOT=Path('/content/pakistani_comedy_shorts'); ROOT.mkdir(parents=True,exist_ok=True)
SRC=ROOT/'source'; OUT=ROOT/'shorts'; REP=ROOT/'reports'
for p in (SRC,OUT,REP): p.mkdir(parents=True,exist_ok=True)
SHOWS={'Hansna Mana Hai':['hansna mana hai','hasna mana hai'], 'Mazaaq Raat':['mazaaq raat','mazak raat','mazāq raat']}
show=W.Dropdown(options=list(SHOWS),description='Show:',layout=W.Layout(width='420px'))
url=W.Text(placeholder='Paste a public YouTube episode/clip URL',description='Source URL:',layout=W.Layout(width='98%'))
key=W.Password(placeholder='OpenRouter API key (not saved in notebook)',description='API key:',layout=W.Layout(width='98%'))
model=W.Text(value='openai/gpt-4.1-mini',description='AI model:',layout=W.Layout(width='420px'))
whisper=W.Dropdown(options=[('Fast: base','base'),('Balanced: small','small'),('Higher Urdu accuracy: medium','medium')],value='small',description='Whisper:')
lang=W.Dropdown(options=[('Urdu','ur'),('Auto detect','auto')],value='ur',description='Language:')
count=W.IntSlider(value=5,min=1,max=10,description='Number of clips:',continuous_update=False)
length=W.IntRangeSlider(value=[18,55],min=10,max=90,description='Duration sec:',continuous_update=False,layout=W.Layout(width='98%'))
subs=W.Checkbox(value=True,description='Burn subtitles'); blur=W.Checkbox(value=True,description='Blurred portrait background')
permission=W.Checkbox(value=False,description='I have permission to edit/use this source video')
button=W.Button(description='🎬 Create Shorts',button_style='success',icon='play')
out=W.Output(layout={'border':'1px solid #ccc','padding':'8px'})
display(W.VBox([W.HTML('<h2>Pakistani Comedy Shorts Studio</h2><p>Downloads only episode audio for transcription, then downloads only selected video time-ranges for final clips. Full-length video is never downloaded.</p>'),show,url,key,model,whisper,lang,count,length,W.HBox([subs,blur]),permission,button,out]))

def log(s):
    with out: print(s)

def parse_json(s):
    s=re.sub(r'^```(?:json)?\s*|\s*```$','',s.strip(),flags=re.I)
    a,b=s.find('{'),s.rfind('}')
    return json.loads(s[a:b+1] if a>=0 and b>a else s)

def api(messages, max_tokens=1800):
    r=requests.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':'Bearer '+key.value.strip(),'Content-Type':'application/json','HTTP-Referer':'https://colab.research.google.com','X-Title':'Pakistani Comedy Shorts Studio'},json={'model':model.value.strip() or 'openai/gpt-4.1-mini','messages':messages,'temperature':0.15,'max_tokens':max_tokens},timeout=120)
    if not r.ok: raise RuntimeError(f'OpenRouter API error {r.status_code}: {r.text[:350]}')
    return r.json()['choices'][0]['message']['content']

def srt_time(t):
    ms=int(round(t*1000)); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def transcribe(wav):
    try: wm=WhisperModel(whisper.value,device='cuda',compute_type='float16')
    except Exception: wm=WhisperModel(whisper.value,device='cpu',compute_type='int8')
    language=None if lang.value=='auto' else lang.value
    segs,info=wm.transcribe(str(wav),language=language,vad_filter=True,beam_size=5)
    rows=[{'start':round(float(s.start),2),'end':round(float(s.end),2),'text':s.text.strip()} for s in segs if s.text.strip()]
    return rows,getattr(info,'language',language)

def chunks(rows,duration,seconds=210,overlap=20):
    out=[]; start=0.0
    while start<duration:
        end=min(duration,start+seconds); part=[x for x in rows if x['end']>=start and x['start']<=end]
        if part: out.append((start,end,part))
        if end>=duration: break
        start=end-overlap
    return out

def find_candidates(rows,duration,minlen,maxlen):
    found=[]; parts=chunks(rows,duration)
    for i,(cs,ce,part) in enumerate(parts,1):
        transcript='\n'.join(f"[{x['start']:.2f}-{x['end']:.2f}] {x['text']}" for x in part)
        prompt=f'''Selected show: {show.value}. You are an Urdu/Pakistani TV comedy editor. Find up to 3 self-contained funny scenes in this transcript. Include enough setup AND punchline/reaction; start before the key line and end after the payoff. Prefer witty exchanges, misunderstandings, callbacks, playful arguments and clear verbal reactions. Reject intros, credits, ads, incomplete jokes and lines that need lots of unseen context. Do not invent dialogue or claim visual expressions from transcript alone. Duration must be {minlen}-{maxlen}s; timestamps are absolute and must be within {cs:.2f}-{ce:.2f}. Return JSON only: {"candidates":[{"start":12.2,"end":40.5,"score":0-100,"reason":"brief","hook":"brief"}]}. If none, return {"candidates":[]}.\nTRANSCRIPT:\n{transcript}'''
        try:
            data=parse_json(api([{'role':'system','content':'Select complete comedic scenes. Return valid JSON only.'},{'role':'user','content':prompt}]))
            for c in data.get('candidates',[]):
                a,b=float(c['start']),float(c['end'])
                if a>=0 and b<=duration and minlen<=b-a<=maxlen and a>=cs-0.5 and b<=ce+0.5:
                    c.update(start=round(a,2),end=round(b,2),score=int(c.get('score',0))); found.append(c)
        except Exception as e: log(f'Chunk {i}/{len(parts)} skipped: {e}')
        log(f'Analysed transcript chunk {i}/{len(parts)}; candidates: {len(found)}')
    return found

def review(candidates,rows,duration,minlen,maxlen):
    kept=[]
    for c in sorted(candidates,key=lambda x:x.get('score',0),reverse=True):
        a,b=c['start'],c['end']; ctx=[r for r in rows if r['end']>=a-12 and r['start']<=b+10]
        transcript='\n'.join(f"[{r['start']:.2f}-{r['end']:.2f}] {r['text']}" for r in ctx)
        prompt=f'''Strictly review this proposed clip from {show.value}. Does it have setup and payoff and clean speech boundaries? Reject incomplete/contextless/weak clips. Do not invent dialogue. Proposed clip={json.dumps(c,ensure_ascii=False)}. Total source duration={duration}. Target length={minlen}-{maxlen}s. Context:\n{transcript}\nReturn JSON only: {{"keep":true,"start":12.0,"end":40.0,"quality_score":0-100,"reason":"brief"}}'''
        try:
            r=parse_json(api([{'role':'system','content':'Be a strict second-pass comedy editor. Valid JSON only.'},{'role':'user','content':prompt}],500))
            a2,b2=float(r.get('start',a)),float(r.get('end',b)); score=int(r.get('quality_score',c['score']))
            if r.get('keep') and score>=62 and 0<=a2<b2<=duration and minlen<=b2-a2<=maxlen:
                c.update(start=round(a2,2),end=round(b2,2),score=score,review_reason=r.get('reason','')); kept.append(c)
        except Exception as e: log(f'Review skipped for {a:.1f}-{b:.1f}: {e}')
    unique=[]
    for c in sorted(kept,key=lambda x:x['score'],reverse=True):
        if all(min(c['end'],q['end'])-max(c['start'],q['start']) < .35*min(c['end']-c['start'],q['end']-q['start']) for q in unique): unique.append(c)
        if len(unique)>=count.value: break
    return sorted(unique,key=lambda x:x['start'])

def write_srt(rows,start,end,path):
    chosen=[r for r in rows if r['end']>start and r['start']<end]
    with open(path,'w',encoding='utf-8') as f:
        for i,r in enumerate(chosen,1):
            a=max(0,r['start']-start); b=min(end-start,r['end']-start); text=r['text'].replace('-->','→').strip()
            f.write(f'{i}\n{srt_time(a)} --> {srt_time(max(a+.15,b))}\n{text}\n\n')

def render(clip_video, rows, c, index):
    # clip_video is already a short timestamp-range download, so render from local t=0.
    start,end=c['start'],c['end']; duration=end-start
    srt=OUT/f'clip_{index:02}.srt'; silent=OUT/f'tmp_{index:02}.mp4'; audio=OUT/f'audio_{index:02}.m4a'; final=OUT/f'clip_{index:02}.mp4'
    write_srt(rows,start,end,srt)
    if blur.value:
        vf='[0:v]split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:10,eq=brightness=-0.12[blur];[fg]scale=1080:1920:force_original_aspect_ratio=decrease[sharp];[blur][sharp]overlay=(W-w)/2:(H-h)/2,setsar=1'
    else: vf='scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1'
    if subs.value:
        s=str(srt).replace('\\','/').replace(':','\\:')
        vf+=f",subtitles='{s}':force_style='FontName=DejaVu Sans,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=150'"
    cmd(['ffmpeg','-y','-i',str(clip_video),'-t',f'{duration:.3f}','-vf',vf,'-an','-c:v','libx264','-preset','veryfast','-crf','19','-pix_fmt','yuv420p','-r','30',str(silent)])
    cmd(['ffmpeg','-y','-i',str(clip_video),'-t',f'{duration:.3f}','-vn','-af','loudnorm=I=-16:TP=-1.5:LRA=11','-c:a','aac','-b:a','192k',str(audio)])
    cmd(['ffmpeg','-y','-i',str(silent),'-i',str(audio),'-c:v','copy','-c:a','aac','-shortest','-movflags','+faststart',str(final)])
    silent.unlink(missing_ok=True); audio.unlink(missing_ok=True); return final

def run(_):
    with out: clear_output()
    try:
        if not url.value.strip(): raise ValueError('Paste a public YouTube URL first.')
        if not key.value.strip(): raise ValueError('Enter your OpenRouter API key. It is not saved to the notebook.')
        if not permission.value: raise ValueError('Confirm that you have permission to edit/use this source video.')
        if not any(x in url.value.lower() for x in ['youtube.com','youtu.be']): raise ValueError('This version accepts YouTube URLs for source episodes/clips.')
        lo,hi=length.value; job=SRC/str(int(time.time())); job.mkdir(parents=True,exist_ok=True)
        log(f'Selected show: {show.value}\nReading source metadata...')
        info=json.loads(cmd(['yt-dlp','--no-warnings','--no-playlist','--dump-single-json',url.value.strip()],capture=True).stdout)
        title=(info.get('title') or '').strip(); duration=float(info.get('duration') or 0)
        log(f'Source: {title}\nDuration: {duration/60:.1f} min')
        if not any(a in title.lower() for a in SHOWS[show.value]): log('⚠️ Title does not clearly name this show; proceeding based on your manual selection.')
        log('STEP 1/3: Downloading AUDIO ONLY for full-episode transcription. No episode video is requested here...')
        audio_source=download_audio_only(url.value.strip(),job)
        wav=job/'audio.wav'
        cmd(['ffmpeg','-y','-i',str(audio_source),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)])
        # Release the downloaded compressed audio after the smaller Whisper WAV is ready.
        audio_source.unlink(missing_ok=True)
        log(f'Transcribing full video with Whisper {whisper.value}...')
        rows,detected=transcribe(wav)
        if not rows: raise RuntimeError('No speech transcribed. Try another source or Whisper model.')
        with open(REP/'transcript.json','w',encoding='utf-8') as f: json.dump({'show':show.value,'title':title,'language':detected,'segments':rows},f,ensure_ascii=False,indent=2)
        log(f'Transcribed {len(rows)} segments; language={detected}. Finding funny scenes...')
        candidates=find_candidates(rows,duration,lo,hi)
        with open(REP/'candidates.json','w',encoding='utf-8') as f: json.dump(candidates,f,ensure_ascii=False,indent=2)
        if not candidates: raise RuntimeError('No candidates found. Try another episode or increase the duration range.')
        log(f'Second-pass review of {len(candidates)} candidates...')
        selected=review(candidates,rows,duration,lo,hi)
        with open(REP/'selected_scenes.json','w',encoding='utf-8') as f: json.dump(selected,f,ensure_ascii=False,indent=2)
        if not selected: raise RuntimeError('Review rejected all candidates. Try another episode.')
        rendered=[]
        for i,c in enumerate(selected,1):
            log(f'STEP 3/3: Downloading ONLY selected scene {i}/{len(selected)} ({c["start"]:.1f}-{c["end"]:.1f}s), capped at 720p | score {c["score"]}')
            range_dir=job/f'scene_{i:02}'
            clip_video=download_video_range(url.value.strip(),c['start'],c['end'],range_dir)
            log(f'Processing short range file ({clip_video.stat().st_size/1024/1024:.1f} MB)...')
            rendered.append(render(clip_video,rows,c,i))
            # Clean temporary source-range downloads to save Colab disk space.
            shutil.rmtree(range_dir, ignore_errors=True)
        package=ROOT/f'{re.sub("[^a-zA-Z0-9_-]+","_",show.value)}_shorts_{int(time.time())}.zip'
        with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as z:
            for p in rendered:
                z.write(p,p.name)
                if p.with_suffix('.srt').exists(): z.write(p.with_suffix('.srt'),p.with_suffix('.srt').name)
            for p in REP.glob('*'):
                if p.is_file(): z.write(p,f'reports/{p.name}')
        log(f'\n✅ Finished: {len(rendered)} clips. Downloading {package.name}')
        try:
            from google.colab import files
            files.download(str(package))
        except Exception: log(f'Download from Colab Files panel: {package}')
    except Exception as e:
        log(f'\n❌ {e}\n{traceback.format_exc()}')

button.on_click(run)
print('Ready — fill the form above and click Create Shorts.')
