"""
face_swap_worker.py - InsightFace face swap in isolated subprocess.
Called by server.py via subprocess.run() to prevent OOM from killing Flask.
Usage: python face_swap_worker.py <source_img> <gesture_video> <output_video> <swap_model>
"""
import sys, os, shutil, json, subprocess, tempfile

def main():
    if len(sys.argv) < 5:
        print(json.dumps({"error": "Usage: face_swap_worker.py <src_img> <gesture_video> <output> <model>"}))
        sys.exit(1)

    source_img   = sys.argv[1]
    gesture_video = sys.argv[2]
    output_path  = sys.argv[3]
    swap_model   = sys.argv[4]

    # Import InsightFace
    try:
        import insightface
        from insightface.app import FaceAnalysis
    except ImportError:
        print(json.dumps({"error": "InsightFace not installed", "skipped": True}))
        shutil.copy2(gesture_video, output_path)
        sys.exit(0)

    import cv2, numpy as np

    _onnx_providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    # Heartbeat inicial — mantém o watchdog feliz durante init pesado (~minutos)
    print(json.dumps({"progress": 0, "total": 1, "stage": "init"}), flush=True)
    try:
        face_app = FaceAnalysis(name="buffalo_l", providers=_onnx_providers)
        face_app.prepare(ctx_id=0, det_size=(640, 640))
    except Exception as e:
        print(json.dumps({"error": f"FaceAnalysis init failed: {e}", "skipped": True}))
        shutil.copy2(gesture_video, output_path)
        sys.exit(0)
    print(json.dumps({"progress": 0, "total": 1, "stage": "face_analysis_ready"}), flush=True)

    # Load swapper model
    try:
        swapper = insightface.model_zoo.get_model(swap_model, download=False)
    except Exception as e:
        print(json.dumps({"error": f"inswapper load failed: {e}", "skipped": True}))
        shutil.copy2(gesture_video, output_path)
        sys.exit(0)
    print(json.dumps({"progress": 0, "total": 1, "stage": "swapper_ready"}), flush=True)

    # Detect source face
    src_img_bgr = cv2.imread(source_img)
    if src_img_bgr is None:
        # Try reading via bytes for Unicode paths
        with open(source_img, 'rb') as f:
            src_img_bgr = cv2.imdecode(np.frombuffer(f.read(), np.uint8), cv2.IMREAD_COLOR)

    src_faces = face_app.get(src_img_bgr)
    if not src_faces:
        print(json.dumps({"error": "No face detected in source image", "skipped": True}))
        shutil.copy2(gesture_video, output_path)
        sys.exit(0)
    src_face = max(src_faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]))
    print(json.dumps({"progress": 0, "total": 1, "stage": "source_face_ok"}), flush=True)

    tmp = tempfile.mkdtemp(prefix="fswap_worker_")
    try:
        cap = cv2.VideoCapture(gesture_video)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        frames_dir = os.path.join(tmp, "frames")
        os.makedirs(frames_dir)

        idx = 0
        while True:
            ret, frame = cap.read()
            if not ret: break
            dst_faces = face_app.get(frame)
            if dst_faces:
                for dst_face in dst_faces:
                    frame = swapper.get(frame, dst_face, src_face, paste_back=True)
            out_f = os.path.join(frames_dir, f"{idx:06d}.png")
            cv2.imwrite(out_f, frame)
            idx += 1
            # heartbeat a cada 5 frames (~0.2s de vídeo) — antes era 25 (~1s)
            if idx % 5 == 0 or idx == 1:
                print(json.dumps({"progress": idx, "total": total_frames}), flush=True)
        cap.release()

        # Re-encode
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        tmp_out = os.path.join(tmp, "output.mp4")
        ret = subprocess.run([
            ffmpeg, "-y", "-framerate", str(fps),
            "-i", os.path.join(frames_dir, "%06d.png"),
            "-c:v", "libx264", "-crf", "16", "-preset", "medium",
            "-pix_fmt", "yuv420p", tmp_out
        ], capture_output=True, timeout=600)
        if ret.returncode != 0:
            raise Exception(f"ffmpeg re-encode failed: {ret.stderr.decode()[:200]}")

        # Mux original audio
        tmp_mux = os.path.join(tmp, "muxed.mp4")
        ret2 = subprocess.run([
            ffmpeg, "-y", "-i", tmp_out, "-i", gesture_video,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy", "-c:a", "aac", "-shortest", tmp_mux
        ], capture_output=True, timeout=120)
        final = tmp_mux if ret2.returncode == 0 else tmp_out

        shutil.copy2(final, output_path)
        print(json.dumps({"success": True, "frames": idx}))
        sys.exit(0)

    except Exception as e:
        print(json.dumps({"error": str(e)}))
        if not os.path.exists(output_path):
            shutil.copy2(gesture_video, output_path)
        sys.exit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
