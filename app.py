import os
import uuid
import tempfile
import gc
import sys
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
    torch.set_num_threads(1)
    print("[OK] Torch CPU threads limited to 1")
except Exception as e:
    print(f"[WARN] Failed to configure torch threads: {e}")

import whisper

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

# Strict upload size: 200MB (balance between Render limits and app performance)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

# Performance settings
MAX_FILE_SIZE_BYTES = 200 * 1024 * 1024  # 200MB
MIN_FILE_SIZE_BYTES = 1024  # 1KB

# Read model size from environment (default: tiny for speed)
# Options (speed vs accuracy):
#   - "tiny"    : Fastest  (≈1min for 10min video)  - Lower accuracy
#   - "small"   : Fast     (≈2min for 10min video)  - Medium accuracy
#   - "base"    : Balanced (≈4min for 10min video)  - Good accuracy
#   - "medium"  : Slower   (≈10min for 10min video) - High accuracy
# Set via: export WHISPER_MODEL=tiny
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
    temp_path = None
    try:
        # Validate file exists
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No file selected"}), 400

        # Validate file type early
        if not allowed_file(file.filename):
            return jsonify({"error": f"Unsupported format. Use: MP4, MP3, WAV, MKV, WebM, M4A, OGG, FLAC, AAC, MOV, AVI, WMA"}), 400

        # Validate file size
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size < MIN_FILE_SIZE_BYTES:
            return jsonify({"error": "File too small (less than 1KB)"}), 400
        
        if file_size > MAX_FILE_SIZE_BYTES:
            size_mb = file_size / (1024 * 1024)
            return jsonify({"error": f"File too large ({size_mb:.1f}MB). Max: 200MB"}), 413

        # Aggressive memory cleanup before processing
        gc.collect()
        
        # Save to temp file
        ext = file.filename.rsplit(".", 1)[1].lower()
        temp_path = os.path.join(tempfile.gettempdir(), f"whisper_{uuid.uuid4().hex}.{ext}")
        
        try:
            file.save(temp_path)
        except Exception as save_err:
            return jsonify({"error": f"Failed to save file: {str(save_err)}"}), 500
        
        if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
            return jsonify({"error": "File upload failed or empty"}), 400

        print(f"[FILE] Saved {file_size/(1024*1024):.1f}MB: {temp_path}")

        # Transcribe with optimized settings
        print("[*] Starting transcription with model:", WHISPER_MODEL)
        try:
            result = model.transcribe(
                temp_path,
                task="transcribe",
                verbose=False,
                word_timestamps=False,
                language=None,
                beam_size=1,
                best_of=1,
                no_speech_threshold=0.5,  # Higher threshold = skip more silence
                temperature=0.0,           # Deterministic for speed
                condition_on_previous_text=False,  # Faster processing
            )
        except MemoryError:
            return jsonify({"error": "Out of memory. Try a smaller file or use a faster model."}), 503
        except Exception as transcribe_err:
            err_msg = str(transcribe_err)[:100]
            return jsonify({"error": f"Transcription error: {err_msg}"}), 500

        # Extract and validate results
        transcript = (result.get("text", "") or "").strip()
        detected_language = result.get("language", "unknown")
        language_probability = result.get("language_probability", None)

        # Optimize segments - remove duplicates and empty ones
        segments = []
        seen_texts = set()
        
        for seg in result.get("segments", []):
            text = (seg.get("text", "") or "").strip()
            if not text or text in seen_texts:
                continue
            
            seen_texts.add(text)
            segments.append({
                "start": format_time(seg.get("start", 0)),
                "end": format_time(seg.get("end", 0)),
                "start_raw": float(seg.get("start", 0)),
                "end_raw": float(seg.get("end", 0)),
                "text": text,
            })

        word_count = len(transcript.split()) if transcript else 0
        char_count = len(transcript)

        print(f"[OK] Done! Language: {detected_language} | Words: {word_count} | Segments: {len(segments)}")

        # Minimal response payload for speed
        return jsonify({
            "success": True,
            "transcript": transcript,
            "language": detected_language,
            "language_probability": round(language_probability, 3) if language_probability else None,
            "segments": segments,
            "word_count": word_count,
            "char_count": char_count,
        })

    except Exception as e:
        print(f"[ERR] Unhandled error: {e}", file=sys.stderr)
        return jsonify({"error": f"Server error: {str(e)[:80]}"}), 500

    finally:
        # Aggressive cleanup
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
                print(f"[CLEANUP] Removed temp file")
            except Exception as cleanup_err:
                print(f"[WARN] Cleanup failed: {cleanup_err}")
        
        gc.collect()


@app.route("/health", methods=["GET"])
def health():
    """Simple health check endpoint."""
    return jsonify({"status": "ok", "model": WHISPER_MODEL}), 200


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
    """Generate SRT subtitle content from segments with robust error handling."""
    if not segments:
        return ""
    
    srt_content = ""
    try:
        idx = 1
        for seg in segments:
            # Handle both raw floats and formatted timestamps
            if "start_raw" in seg and isinstance(seg["start_raw"], (int, float)):
                start_val = float(seg.get("start_raw", 0))
            else:
                # Try to parse from formatted timestamp (MM:SS)
                start_str = seg.get("start", "00:00")
                try:
                    parts = start_str.split(":")
                    start_val = int(parts[0]) * 60 + int(parts[1]) if len(parts) >= 2 else 0
                except:
                    start_val = 0
            
            if "end_raw" in seg and isinstance(seg["end_raw"], (int, float)):
                end_val = float(seg.get("end_raw", 0))
            else:
                # Try to parse from formatted timestamp (MM:SS)
                end_str = seg.get("end", "00:00")
                try:
                    parts = end_str.split(":")
                    end_val = int(parts[0]) * 60 + int(parts[1]) if len(parts) >= 2 else 0
                except:
                    end_val = 0
            
            start_srt = format_time_srt(start_val)
            end_srt = format_time_srt(end_val)
            text = seg.get("text", "").strip()
            
            # Skip empty segments
            if not text:
                continue
            
            srt_content += f"{idx}\n{start_srt} --> {end_srt}\n{text}\n\n"
            idx += 1
        
        return srt_content.strip()
    except Exception as e:
        print(f"[ERR] SRT generation error: {e}")
        raise


@app.route("/download-srt", methods=["POST"])
def download_srt():
    """Generate and return SRT file for download."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        segments = data.get("segments", [])
        filename = data.get("filename", "transcript").replace(".json", "").replace(".srt", "")
        
        if not segments:
            return jsonify({"error": "No segments provided"}), 400
        
        srt_content = generate_srt(segments)
        
        if not srt_content:
            return jsonify({"error": "No subtitle content generated"}), 400
        
        return jsonify({
            "success": True,
            "srt_content": srt_content,
            "filename": f"{filename}.srt"
        })
    except Exception as e:
        print(f"[ERR] SRT download error: {e}")
        return jsonify({"error": f"SRT generation failed: {str(e)}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n[*] Starting Transcription Server on port {port}...")
    app.run(debug=False, host="0.0.0.0", port=port)
