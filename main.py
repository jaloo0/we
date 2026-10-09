from pytubefix import YouTube
from pytubefix.cli import on_progress
import os

def download_youtube_video(url, output_path="."):
    """
    Downloads a YouTube video at medium resolution (480p) using pytubefix.
    """
    try:
        yt = YouTube(url, on_progress_callback=on_progress)
        print(f"Fetching: {yt.title}")

        # Filter for 480p progressive stream (contains both audio and video)
        stream = yt.streams.filter(res="480p", progressive=True).first()
        
        # Fallback if 480p is not available as a progressive stream
        if not stream:
            print("480p progressive stream not found. Searching for best available...")
            stream = yt.streams.get_highest_resolution()
        
        if stream:
            print(f"Downloading resolution: {stream.resolution} to: {os.path.abspath(output_path)}")
            stream.download(output_path=output_path)
            print("\nDownload finished successfully.")
        else:
            print("No suitable stream found.")
            
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    # Replace with your desired video URL
    sample_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    download_youtube_video(sample_url)