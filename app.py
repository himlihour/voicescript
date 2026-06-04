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
# Options (speed vs accuracy):
#   - "tiny"    : Fastest  (≈1min for 10min video)  - Lower accuracy
#   - "small"   : Fast     (≈2min for 10min video)  - Medium accuracy
#   - "base"    : Balanced (≈4min for 10min video)  - Good accuracy
#   - "small"   : Slower   (≈10min for 10min video) - High accuracy
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
        # Validate file
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No file selected"}), 400

        if not allowed_file(file.filename):
            return jsonify({"error": f"File type not supported. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"}), 400

        # Clean memory before transcription
        gc.collect()

        # Save to temp file
        ext = file.filename.rsplit(".", 1)[1].lower()
        temp_path = os.path.join(tempfile.gettempdir(), f"whisper_{uuid.uuid4().hex}.{ext}")
        file.save(temp_path)
        print(f"[FILE] Saved to: {temp_path}")

        if not os.path.exists(temp_path):
            return jsonify({"error": "File upload failed"}), 400

        # Transcribe with Whisper
        print("[*] Starting transcription...")
        try:
            result = model.transcribe(
                temp_path,
                task="transcribe",
                verbose=False,
                word_timestamps=False,
                language=None,           # Auto-detect language
                beam_size=1,             # Fastest greedy decoding
                best_of=1,               # No sampling
                no_speech_threshold=0.4, # Skip silent sections
            )
        except Exception as transcribe_err:
            print(f"[ERR] Transcription failed: {transcribe_err}")
            return jsonify({"error": f"Transcription error: {str(transcribe_err)}"}), 500

        # Extract results
        transcript = result.get("text", "").strip()
        detected_language = result.get("language", "unknown")
        language_probability = result.get("language_probability", None)

        # Build segments with timestamps
        segments = []
        try:
            for seg in result.get("segments", []):
                start = seg.get("start", 0)
                end = seg.get("end", 0)
                text = seg.get("text", "").strip()
                if text:  # Only add non-empty segments
                    segments.append({
                        "start": format_time(start),
                        "end": format_time(end),
                        "start_raw": float(start),
                        "end_raw": float(end),
                        "text": text,
                    })
        except Exception as seg_err:
            print(f"[WARN] Segment processing error: {seg_err}")

        word_count = len(transcript.split()) if transcript else 0
        char_count = len(transcript)

        print(f"[OK] Transcription complete. Language: {detected_language}, Words: {word_count}, Segments: {len(segments)}")

        response_data = {
            "success": True,
            "transcript": transcript,
            "language": detected_language,
            "language_probability": language_probability,
            "segments": segments,
            "word_count": word_count,
            "char_count": char_count,
        }

        return jsonify(response_data)

    except Exception as e:
        print(f"[ERR] Unhandled error in transcribe: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Server error: {str(e)}"}), 500

    finally:
        # Always cleanup
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as cleanup_err:
                print(f"[WARN] Could not remove temp file: {cleanup_err}")
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
