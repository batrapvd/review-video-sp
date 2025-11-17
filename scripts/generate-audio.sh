#!/bin/bash

# Audio generation script with TTS fallback

set +e

if [ -z "$1" ]; then
    echo "Usage: $0 <text-file>"
    exit 1
fi

TEXT_FILE="$1"

if [ ! -f "$TEXT_FILE" ]; then
    echo "Error: Text file not found: $TEXT_FILE"
    exit 1
fi

# Read the text content
TEXT_CONTENT=$(cat "$TEXT_FILE")

echo "Generating audio for text (${#TEXT_CONTENT} characters)..."

# Function to use Edge-TTS (Primary)
use_edge_tts() {
    echo ""
    echo "=========================================="
    echo "🎤 Using Edge-TTS (Microsoft) - Primary"
    echo "=========================================="

    # Check if edge-tts is installed, if not install it
    if ! command -v edge-tts &> /dev/null; then
        echo "Installing Edge-TTS..."
        pip install -q edge-tts
    fi

    # Save text to temp file to avoid command-line escaping issues
    TEMP_TEXT_FILE="output/temp_text_for_tts.txt"
    echo "$TEXT_CONTENT" > "$TEMP_TEXT_FILE"

    # Use Vietnamese voice from Edge-TTS
    # vi-VN-HoaiMyNeural is a female Vietnamese voice
    # vi-VN-NamMinhNeural is a male Vietnamese voice
    echo "Generating audio with Edge-TTS (Vietnamese voice: vi-VN-HoaiMyNeural)..."
    echo "Text length: ${#TEXT_CONTENT} characters"

    # Use file input to avoid command-line issues with special characters
    edge-tts --voice "vi-VN-HoaiMyNeural" --file "$TEMP_TEXT_FILE" --write-media output/voiceover.mp3 2>&1

    EXIT_CODE=$?

    # Clean up temp file
    rm -f "$TEMP_TEXT_FILE"

    if [ $EXIT_CODE -eq 0 ] && [ -f "output/voiceover.mp3" ]; then
        # Check if mp3 file has content
        FILE_SIZE=$(stat -f%z "output/voiceover.mp3" 2>/dev/null || stat -c%s "output/voiceover.mp3" 2>/dev/null || echo "0")

        if [ "$FILE_SIZE" -lt 1000 ]; then
            echo "❌ Edge-TTS generated empty or invalid audio file"
            rm -f output/voiceover.mp3
            return 1
        fi

        # Convert mp3 to wav for consistency
        echo "Converting mp3 to wav..."
        ffmpeg -i output/voiceover.mp3 -y output/voiceover.wav 2>&1 | tail -5

        if [ -f "output/voiceover.wav" ]; then
            # Check if wav file has content
            WAV_SIZE=$(stat -f%z "output/voiceover.wav" 2>/dev/null || stat -c%s "output/voiceover.wav" 2>/dev/null || echo "0")
            if [ "$WAV_SIZE" -gt 1000 ]; then
                rm -f output/voiceover.mp3
                echo "✅ Edge-TTS audio generated successfully: output/voiceover.wav"
                ls -lh output/voiceover.wav
                return 0
            else
                echo "❌ Edge-TTS generated empty wav file"
                rm -f output/voiceover.mp3 output/voiceover.wav
                return 1
            fi
        else
            echo "❌ Failed to convert Edge-TTS audio to wav"
            rm -f output/voiceover.mp3
            return 1
        fi
    else
        echo "❌ Edge-TTS failed (exit code: $EXIT_CODE)"
        return 1
    fi
}

# Function to use Zalo TTS as fallback
use_zalo_tts_fallback() {
    echo ""
    echo "=========================================="
    echo "🔄 Using Zalo TTS fallback"
    echo "=========================================="

    if [ -z "$ZALO_API_KEY" ]; then
        echo "⚠️ ZALO_API_KEY not set, skipping Zalo TTS fallback"
        return 1
    fi

    echo "Trying Zalo TTS API..."
    RESPONSE=$(curl -s --location 'https://api.zalo.ai/v1/tts/synthesize' \
      --header "apikey: $ZALO_API_KEY" \
      --header 'Content-Type: application/x-www-form-urlencoded' \
      --data-urlencode 'speaker_id=1' \
      --data-urlencode 'speed=1' \
      --data-urlencode "input=$TEXT_CONTENT")

    # Check if request was successful
    if [ $? -ne 0 ]; then
        echo "⚠️ Zalo TTS API request failed"
        return 1
    fi

    echo "Zalo API Response: $RESPONSE"

    # Extract error code
    ERROR_CODE=$(echo "$RESPONSE" | jq -r '.error_code')

    if [ "$ERROR_CODE" != "0" ]; then
        ERROR_MESSAGE=$(echo "$RESPONSE" | jq -r '.error_message')
        echo "⚠️ Zalo API Error: $ERROR_MESSAGE (code: $ERROR_CODE)"
        return 1
    fi

    # Extract audio URL
    AUDIO_URL=$(echo "$RESPONSE" | jq -r '.data.url')

    if [ -z "$AUDIO_URL" ] || [ "$AUDIO_URL" = "null" ]; then
        echo "⚠️ No audio URL in Zalo response"
        return 1
    fi

    echo "✅ Zalo Audio URL: $AUDIO_URL"

    # Download the audio file
    echo "Downloading audio file from Zalo..."
    curl -L -o output/voiceover.wav "$AUDIO_URL"

    if [ $? -eq 0 ] && [ -f "output/voiceover.wav" ]; then
        echo "✅ Zalo TTS audio downloaded successfully: output/voiceover.wav"
        ls -lh output/voiceover.wav
        return 0
    else
        echo "⚠️ Failed to download from Zalo"
        return 1
    fi
}

# Try Edge-TTS first (Primary)
if use_edge_tts; then
    echo ""
    echo "✅ Audio generation completed with Edge-TTS (Primary)"
    exit 0
fi

# If Edge-TTS fails, try Zalo TTS as fallback
echo ""
echo "⚠️ Edge-TTS failed, trying Zalo TTS fallback..."
if use_zalo_tts_fallback; then
    echo ""
    echo "✅ Audio generation completed with Zalo TTS (Fallback)"
    exit 0
fi

# Both failed
echo ""
echo "❌ Failed to generate audio with both Edge-TTS and Zalo TTS"
exit 1
