import os
import yt_dlp
from dotenv import load_dotenv
from scenedetect import detect, ContentDetector, split_video_ffmpeg
from moviepy.editor import VideoFileClip, concatenate_videoclips, vfx

load_dotenv()

def download_video(url):
    print(f"Downloading video (480p) from {url}...")
    
    cookies_file = "cookies.txt"
    env_cookies = os.getenv("YOUTUBE_COOKIES")
    if env_cookies:
        with open(cookies_file, "w") as f:
            f.write(env_cookies)

    ydl_opts = {
        # Select 480p specifically, or best available up to 480p
        'format': 'bestvideo[height<=480]+bestaudio/best[height<=480]',
        'outtmpl': 'input_video.%(ext)s',
        'merge_output_format': 'mp4',
        'quiet': False,
        'no_warnings': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
            }
        },
    }

    if os.path.exists(cookies_file):
        ydl_opts['cookiefile'] = cookies_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            # If it merged to mp4, the filename extension might need adjustment
            if not os.path.exists(filename) and os.path.exists("input_video.mp4"):
                filename = "input_video.mp4"
        print(f"Download successful: {filename}")
        return filename
    except Exception as e:
        print(f"Fatal Error during download: {e}")
        raise e

def detect_scenes(video_path):
    print(f"Detecting scenes in {video_path}...")
    # Returns a list of tuples (start_time, end_time)
    scene_list = detect(video_path, ContentDetector())
    print(f"Detected {len(scene_list)} scenes.")
    return scene_list

def process_and_edit(video_path, scene_list):
    if not scene_list:
        print("No scenes detected to process.")
        return

    print("Processing slices and performing simple edit...")
    clips = []
    
    # Example: Take the first 3 scenes and apply a slight speed increase
    # to simulate an automated 'highlight' edit
    for i, scene in enumerate(scene_list[:3]):
        start_t = scene[0].get_seconds()
        end_t = scene[1].get_seconds()
        
        clip = VideoFileClip(video_path).subclip(start_t, end_t)
        # Apply a simple effect: Fade in/out
        clip = clip.fx(vfx.fadein, 0.5).fx(vfx.fadeout, 0.5)
        clips.append(clip)

    if clips:
        final_clip = concatenate_videoclips(clips)
        output_file = "final_edit.mp4"
        final_clip.write_videofile(output_file, codec="libx264", audio_codec="aac")
        print(f"Editing complete. Saved to {output_file}")
        
        # Close clips to release memory
        for c in clips:
            c.close()
        final_clip.close()

if __name__ == "__main__":
    video_url = "https://www.youtube.com/watch?v=4-dRxvg7Okk"
    try:
        # 1. Download
        file_path = download_video(video_url)
        
        # 2. Scene Detection
        scenes = detect_scenes(file_path)
        
        # 3. Slicing & Editing
        process_and_edit(file_path, scenes)
        
    except Exception as e:
        print(f"Pipeline failed: {e}")
        exit(1)