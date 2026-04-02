#!/bin/bash

# List of the YouTube URLs you want to download
URLS=(
    "https://www.youtube.com/watch?v=gs74wUqWMA8"
    "https://www.youtube.com/watch?v=ZaLpJBP-Kko"
    "https://www.youtube.com/watch?v=019jQZP0bB0"
    "https://www.youtube.com/watch?v=aiY04RmNcpY"
)

# Loop through the URLs and download the highest quality audio
for url in "${URLS[@]}"; do
    echo "Starting download for: $url"
    # --extract-audio extracts the audio stream
    # --audio-format mp3 converts it to mp3 (you can change this to flac, wav, m4a, etc.)
    # --audio-quality 0 specifies the best possible quality
    yt-dlp --extract-audio --audio-format mp3 --audio-quality 0 "$url"
done

echo "All audio downloads completed successfully!"d