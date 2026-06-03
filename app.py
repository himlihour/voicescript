import os
import uuid
import tempfile
import gc
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# --- Render ffmpeg Hack using imageio-ffmpeg ---
try:
    import imageio_ffmpeg
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    ffmpeg_dir = os.path.dirname(ffmpeg_path)
    os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
    print(f"[OK] Injected ffmpeg path: {ffmpeg_dir}")
except Exception as e:
    print(f"[WARN] Failed to inject ffmpeg path via imageio-ffmpeg: {e}")

# --- RAM Optimization for Render Free Tier (512MB limit) ---
try:
    import torch
    torch.set_num_threads(1)  # Restrict to 1 CPU thread to avoid RAM spikes
    print("[OK] Torch CPU threads limited to 1")
except Exception as e:
    print(f"[WARN] Failed to configure torch threads: {e}")

import whisper

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

# Max upload size: 500MB (for large video files)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

# Read model size from environment (default: base)
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")

print(f"[*] Loading Whisper '{WHISPER_MODEL}' model...")
model = whisper.load_model(WHISPER_MODEL)
print("[OK] Whisper model loaded!")

ALLOWED_EXTENSIONS = {
    "mp4", "mp3", "wav", "mkv", "webm", "m4a",
    "ogg", "flac", "aac", "mov", "avi", "wma"
}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": f"File type not supported. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"}), 400

    # Save to a temp file
    ext = file.filename.rsplit(".", 1)[1].lower()
    temp_path = os.path.join(tempfile.gettempdir(), f"whisper_{uuid.uuid4().hex}.{ext}")

    try:
        file.save(temp_path)
        print(f"[FILE] Saved to: {temp_path}")

        # Transcribe with Whisper (auto language detection)
        print("[*] Transcribing...")
        result = model.transcribe(
            temp_path,
            task="transcribe",       # keep original language (no translation)
            verbose=False,
            word_timestamps=False,
        )

        transcript = result.get("text", "").strip()
        detected_language = result.get("language", "unknown")
        language_probability = result.get("language_probability", None)

        # Build segments with timestamps
        segments = []
        for seg in result.get("segments", []):
            start = seg.get("start", 0)
            end = seg.get("end", 0)
            text = seg.get("text", "").strip()
            segments.append({
                "start": format_time(start),
                "end": format_time(end),
                "start_raw": start,
                "end_raw": end,
                "text": text,
            })

        word_count = len(transcript.split()) if transcript else 0
        char_count = len(transcript)

        print(f"[OK] Done! Language: {detected_language}, Words: {word_count}")

        return jsonify({
            "success": True,
            "transcript": transcript,
            "language": detected_language,
            "language_probability": language_probability,
            "segments": segments,
            "word_count": word_count,
            "char_count": char_count,
        })

    except Exception as e:
        print(f"[ERR] Error: {e}")
        return jsonify({"error": str(e)}), 500

    finally:
        # Clean up temp file
        if os.path.exists(temp_path):
            os.remove(temp_path)
        # Free memory
        gc.collect()


def format_time(seconds):
    """Convert seconds float to MM:SS format."""
    mins = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{mins:02d}:{secs:02d}"


def format_time_srt(seconds):
    """Convert seconds float to SRT format (HH:MM:SS,mmm)."""
    hours = int(seconds) // 3600
    minutes = (int(seconds) % 3600) // 60
    secs = int(seconds) % 60
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt(segments):
    """Generate SRT subtitle content from segments."""
    srt_content = ""
    for idx, seg in enumerate(segments, 1):
        # Parse timestamp strings back to floats for SRT formatting
        start_str = seg.get("start_raw", 0)
        end_str = seg.get("end_raw", 0)
        
        start_srt = format_time_srt(start_str)
        end_srt = format_time_srt(end_str)
        text = seg.get("text", "").strip()
        
        srt_content += f"{idx}\n{start_srt} --> {end_srt}\n{text}\n\n"
    
    return srt_content.strip()


@app.route("/download-srt", methods=["POST"])
def download_srt():
    """Generate and return SRT file for download."""
    try:
        data = request.get_json()
        segments = data.get("segments", [])
        filename = data.get("filename", "transcript").replace(".json", "")
        
        if not segments:
            return jsonify({"error": "No segments provided"}), 400
        
        srt_content = generate_srt(segments)
        
        return jsonify({
            "success": True,
            "srt_content": srt_content,
            "filename": f"{filename}.srt"
        })
    except Exception as e:
        print(f"[ERR] SRT generation error: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n[*] Starting Transcription Server on port {port}...")
    app.run(debug=False, host="0.0.0.0", port=port)
