import os
import yt_dlp
from dotenv import load_dotenv

load_dotenv()

def download_audio(url):
    print(f"Downloading audio stream from {url}...")
    
    # Path to cookies file
    cookies_file = "cookies.txt"
    
    # If cookies are provided via environment variable (GitHub Secrets), write them to a file
    env_cookies = os.getenv("YOUTUBE_COOKIES")
    if env_cookies:
        with open(cookies_file, "w") as f:
            f.write(env_cookies)

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'input_audio.%(ext)s',
        'quiet': False,
        'no_warnings': False,
        # Using android client often bypasses the 'Sign in to confirm you are not a bot' error
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
            }
        },
    }

    # Use cookies if the file exists
    if os.path.exists(cookies_file):
        ydl_opts['cookiefile'] = cookies_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        print("Download successful.")
    except Exception as e:
        print(f"Fatal Error: {e}")
        raise e

if __name__ == "__main__":
    # Example usage for the reported URL
    video_url = "https://www.youtube.com/watch?v=4-dRxvg7Okk"
    try:
        download_audio(video_url)
    except Exception:
        exit(1)