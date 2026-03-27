"""
Video Scene Storyboard Tool
视频镜头分析与分镜表生成工具
"""

import os
import re
import shutil
import tempfile
import threading
import platform
import subprocess
import math
from pathlib import Path
from collections import namedtuple, Counter

import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np
import yt_dlp
import cv2
from scenedetect import open_video, SceneManager
from scenedetect.detectors import ContentDetector
from PIL import Image as PILImage

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GIF_WIDTH = 320
GIF_MAX_FRAMES = 24
GIF_FPS = 8
DEFAULT_THRESHOLD = 27.0
SHOT_DIR_NAME = "shots"
STORYBOARD_FILENAME = "storyboard.html"
COLS_PER_ROW = 3

SceneCut = namedtuple("SceneCut", ["index", "start_time", "end_time", "start_frame", "end_frame"])

# Load face cascade once (ships with OpenCV)
_face_cascade_path = os.path.join(
    os.path.dirname(cv2.__file__), "data", "haarcascade_frontalface_default.xml"
)
_face_cascade = None


def _get_face_cascade():
    global _face_cascade
    if _face_cascade is None:
        if os.path.exists(_face_cascade_path):
            _face_cascade = cv2.CascadeClassifier(_face_cascade_path)
        else:
            _face_cascade = cv2.CascadeClassifier()
    return _face_cascade


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = name.strip('. ')
    if len(name) > 100:
        name = name[:100]
    return name or "untitled"


def make_phase_cb(progress_cb, phase_start: float, phase_end: float):
    def cb(msg: str, local_pct: float):
        global_pct = phase_start + (local_pct / 100.0) * (phase_end - phase_start)
        progress_cb(msg, global_pct)
    return cb


def format_timecode(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def get_output_dir(title: str) -> Path:
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        desktop = Path.home()
    base_name = sanitize_filename(title)
    output_dir = desktop / base_name
    counter = 1
    while output_dir.exists():
        output_dir = desktop / f"{base_name}_{counter}"
        counter += 1
    output_dir.mkdir(parents=True)
    return output_dir


def open_folder(path: str):
    if platform.system() == "Windows":
        os.startfile(path)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ---------------------------------------------------------------------------
# Shot analysis functions
# ---------------------------------------------------------------------------

def analyze_shot_type(frame) -> tuple:
    """Analyze shot type and detect faces. Returns (shot_type_str, num_faces)."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = frame.shape[:2]
    frame_area = h * w

    cascade = _get_face_cascade()
    faces = []
    if not cascade.empty():
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
        if isinstance(faces, np.ndarray) and len(faces) > 0:
            faces = faces.tolist()
        elif not isinstance(faces, list):
            faces = []

    num_faces = len(faces)

    if num_faces > 0:
        max_face_area = max(fw * fh for (_, _, fw, fh) in faces)
        ratio = max_face_area / frame_area
        if ratio > 0.15:
            return "特写", num_faces
        elif ratio > 0.05:
            return "近景", num_faces
        elif ratio > 0.01:
            return "中景", num_faces
        else:
            return "全景", num_faces
    else:
        edges = cv2.Canny(gray, 50, 150)
        edge_density = np.count_nonzero(edges) / frame_area
        if edge_density > 0.15:
            return "近景", 0
        elif edge_density > 0.06:
            return "中景", 0
        else:
            return "远景", 0


def analyze_camera_movement(cap, start_frame: int, end_frame: int, fps: float) -> str:
    """Analyze camera movement using optical flow between sampled frame pairs."""
    scene_frames = end_frame - start_frame
    if scene_frames < 2:
        return "固定"

    # Sample ~6 frame pairs across the scene
    num_pairs = min(6, scene_frames // max(1, int(fps * 0.2)))
    if num_pairs < 1:
        num_pairs = 1
    step = max(1, scene_frames // (num_pairs + 1))
    gap = max(1, int(fps * 0.15))

    dx_list = []
    dy_list = []
    divergence_list = []

    for p in range(num_pairs):
        f1_idx = start_frame + step * (p + 1)
        f2_idx = f1_idx + gap
        if f2_idx >= end_frame:
            break

        cap.set(cv2.CAP_PROP_POS_FRAMES, f1_idx)
        ret1, frame1 = cap.read()
        cap.set(cv2.CAP_PROP_POS_FRAMES, f2_idx)
        ret2, frame2 = cap.read()
        if not ret1 or not ret2 or frame1 is None or frame2 is None:
            continue

        gray1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)

        # Downscale for speed
        small_h, small_w = 120, 160
        gray1_s = cv2.resize(gray1, (small_w, small_h))
        gray2_s = cv2.resize(gray2, (small_w, small_h))

        flow = cv2.calcOpticalFlowFarneback(
            gray1_s, gray2_s, None,
            pyr_scale=0.5, levels=3, winsize=15,
            iterations=3, poly_n=5, poly_sigma=1.2, flags=0
        )

        dx_mean = np.mean(flow[:, :, 0])
        dy_mean = np.mean(flow[:, :, 1])
        dx_list.append(dx_mean)
        dy_list.append(dy_mean)

        # Compute divergence for zoom detection
        cy, cx = small_h // 2, small_w // 2
        flow_center = flow[cy - 20:cy + 20, cx - 20:cx + 20]
        flow_edge_top = flow[:20, :, :]
        flow_edge_bot = flow[-20:, :, :]
        flow_edge_left = flow[:, :20, :]
        flow_edge_right = flow[:, -20:, :]

        # Radial magnitude: positive = expanding (zoom out/pull), negative = contracting (zoom in/push)
        edge_mag = (
            np.mean(np.sqrt(flow_edge_top[:, :, 0]**2 + flow_edge_top[:, :, 1]**2)) +
            np.mean(np.sqrt(flow_edge_bot[:, :, 0]**2 + flow_edge_bot[:, :, 1]**2)) +
            np.mean(np.sqrt(flow_edge_left[:, :, 0]**2 + flow_edge_left[:, :, 1]**2)) +
            np.mean(np.sqrt(flow_edge_right[:, :, 0]**2 + flow_edge_right[:, :, 1]**2))
        ) / 4.0
        center_mag = np.mean(np.sqrt(flow_center[:, :, 0]**2 + flow_center[:, :, 1]**2))
        divergence_list.append(edge_mag - center_mag)

    if not dx_list:
        return "固定"

    avg_dx = np.mean(dx_list)
    avg_dy = np.mean(dy_list)
    avg_div = np.mean(divergence_list)
    magnitude = math.sqrt(avg_dx**2 + avg_dy**2)

    # Thresholds
    if magnitude < 0.8 and abs(avg_div) < 0.5:
        return "固定"

    # Check zoom (push/pull) first
    if abs(avg_div) > 1.0 and abs(avg_div) > magnitude * 0.6:
        if avg_div > 0:
            return "拉"
        else:
            return "推"

    # Determine pan/tilt
    if abs(avg_dx) > abs(avg_dy) * 1.3:
        return "右摇" if avg_dx > 0 else "左摇"
    elif abs(avg_dy) > abs(avg_dx) * 1.3:
        return "下摇" if avg_dy > 0 else "上摇"
    else:
        return "跟移"


def describe_scene(frame, num_faces: int, shot_type: str, movement: str) -> str:
    """Generate a brief textual description of the scene."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    brightness = np.mean(hsv[:, :, 2])

    b_mean = np.mean(frame[:, :, 0].astype(float))
    r_mean = np.mean(frame[:, :, 2].astype(float))

    parts = []

    # Brightness
    if brightness > 170:
        parts.append("明亮")
    elif brightness > 85:
        parts.append("中等亮度")
    else:
        parts.append("暗调")

    # Color temperature
    diff = r_mean - b_mean
    if diff > 15:
        parts.append("暖色调")
    elif diff < -15:
        parts.append("冷色调")

    # People
    if num_faces == 0:
        parts.append("无人物")
    elif num_faces == 1:
        parts.append("1人入画")
    else:
        parts.append(f"{num_faces}人入画")

    # Shot type + movement context
    parts.append(f"{shot_type}景别")
    if movement != "固定":
        parts.append(f"镜头{movement}")
    else:
        parts.append("固定镜头")

    return "，".join(parts)


def generate_director_summary(scenes: list, analyses: list, title: str) -> str:
    """Generate a director's summary based on scene statistics."""
    total_shots = len(scenes)
    if total_shots == 0:
        return "无镜头数据。"

    total_duration = scenes[-1].end_time - scenes[0].start_time
    durations = [s.end_time - s.start_time for s in scenes]
    avg_dur = sum(durations) / len(durations)
    min_dur = min(durations)
    max_dur = max(durations)

    # Pace
    if avg_dur < 2.0:
        pace = "快节奏"
    elif avg_dur < 5.0:
        pace = "中等节奏"
    else:
        pace = "慢节奏"

    # Shot type distribution
    shot_types = [a.get("shot_type", "未知") for a in analyses]
    st_counter = Counter(shot_types)
    st_parts = []
    for st, cnt in st_counter.most_common():
        pct = cnt / total_shots * 100
        st_parts.append(f"{st} {pct:.0f}%")

    # Movement distribution
    movements = [a.get("movement", "未知") for a in analyses]
    mv_counter = Counter(movements)
    mv_parts = []
    for mv, cnt in mv_counter.most_common():
        pct = cnt / total_shots * 100
        mv_parts.append(f"{mv} {pct:.0f}%")

    # Format duration
    dur_min = int(total_duration // 60)
    dur_sec = int(total_duration % 60)
    dur_str = f"{dur_min}分{dur_sec}秒" if dur_min > 0 else f"{dur_sec}秒"

    lines = [
        f"影片《{title}》共 {total_shots} 个镜头，总时长 {dur_str}。",
        f"平均镜头时长 {avg_dur:.1f}秒，整体呈{pace}剪辑风格"
        f"（最短 {min_dur:.1f}秒，最长 {max_dur:.1f}秒）。",
        f"景别分布：{' / '.join(st_parts)}。",
        f"运镜方式：{' / '.join(mv_parts)}。",
    ]
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Pipeline functions
# ---------------------------------------------------------------------------

def download_video(url: str, temp_dir: str, progress_cb) -> tuple:
    def ydl_hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0:
                progress_cb("正在下载视频...", (downloaded / total) * 100)
        elif d["status"] == "finished":
            progress_cb("下载完成，正在处理...", 100)

    # Prefer single-file formats that don't require ffmpeg to merge.
    ydl_opts = {
        "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "progress_hooks": [ydl_hook],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "untitled")
            filename = ydl.prepare_filename(info)
            video_path = os.path.splitext(filename)[0] + ".mp4"
            if not os.path.exists(video_path):
                video_path = filename
            if not os.path.exists(video_path):
                for f in os.listdir(temp_dir):
                    if f.endswith(('.mp4', '.mkv', '.webm', '.avi')):
                        video_path = os.path.join(temp_dir, f)
                        break
    except yt_dlp.utils.DownloadError as e:
        raise RuntimeError(f"下载失败，请检查链接是否有效。\n{e}")

    if not os.path.exists(video_path):
        raise RuntimeError("下载失败：未找到视频文件。")

    return video_path, title


def detect_scenes(video_path: str, threshold: float, progress_cb) -> list:
    progress_cb("正在分析镜头...", 0)

    video = open_video(video_path)
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=threshold))

    total_frames = video.duration.get_frames()

    def frame_callback(frame_img, frame_num):
        if total_frames > 0 and frame_num % 30 == 0:
            pct = min((frame_num / total_frames) * 100, 100)
            progress_cb("正在分析镜头...", pct)

    scene_manager.detect_scenes(video, callback=frame_callback)
    scene_list = scene_manager.get_scene_list()

    if not scene_list:
        video_cv = cv2.VideoCapture(video_path)
        fps = video_cv.get(cv2.CAP_PROP_FPS)
        frame_count = int(video_cv.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps if fps > 0 else 0
        video_cv.release()
        return [SceneCut(
            index=1, start_time=0.0, end_time=duration,
            start_frame=0, end_frame=frame_count - 1,
        )]

    scenes = []
    for i, (start_tc, end_tc) in enumerate(scene_list):
        scenes.append(SceneCut(
            index=i + 1,
            start_time=start_tc.get_seconds(),
            end_time=end_tc.get_seconds(),
            start_frame=start_tc.get_frames(),
            end_frame=end_tc.get_frames(),
        ))

    progress_cb("镜头分析完成", 100)
    return scenes


def capture_gifs_and_analyze(video_path: str, scenes: list, output_dir: str,
                             progress_cb) -> tuple:
    """Generate GIFs and analyze each scene. Returns (gif_paths, analyses)."""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 24.0

    shots_dir = os.path.join(output_dir, SHOT_DIR_NAME)
    os.makedirs(shots_dir, exist_ok=True)

    paths = []
    analyses = []
    total = len(scenes)

    for i, scene in enumerate(scenes):
        scene_frames = scene.end_frame - scene.start_frame
        if scene_frames <= 0:
            scene_frames = 1

        num_samples = min(GIF_MAX_FRAMES, max(1, scene_frames))
        if num_samples == 1:
            sample_indices = [scene.start_frame]
        else:
            step = scene_frames / num_samples
            sample_indices = [int(scene.start_frame + step * j) for j in range(num_samples)]

        pil_frames = []
        representative_frame = None  # BGR frame for analysis

        for idx_j, frame_idx in enumerate(sample_indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            # Keep a representative frame (~ 1/3 into the scene) for shot type analysis
            if idx_j == len(sample_indices) // 3:
                representative_frame = frame.copy()

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = PILImage.fromarray(rgb)
            orig_w, orig_h = pil_img.size
            if orig_w > 0:
                scale = GIF_WIDTH / orig_w
                new_h = int(orig_h * scale)
                pil_img = pil_img.resize((GIF_WIDTH, new_h), PILImage.LANCZOS)
            pil_frames.append(pil_img)

        # Fallback representative frame
        if representative_frame is None and pil_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, sample_indices[0])
            ret, representative_frame = cap.read()
            if not ret:
                representative_frame = None

        # Save GIF
        filename = f"shot_{i + 1:03d}.gif"
        filepath = os.path.join(shots_dir, filename)

        if pil_frames:
            frame_duration = 1000 // GIF_FPS
            if len(pil_frames) == 1:
                pil_frames[0].save(filepath, format="GIF")
            else:
                pil_frames[0].save(
                    filepath, format="GIF", save_all=True,
                    append_images=pil_frames[1:],
                    duration=frame_duration, loop=0,
                )
            paths.append(filepath)
        else:
            paths.append(None)

        # Analyze shot type
        shot_type = "中景"
        num_faces = 0
        if representative_frame is not None:
            try:
                shot_type, num_faces = analyze_shot_type(representative_frame)
            except Exception:
                pass

        # Analyze camera movement
        movement = "固定"
        try:
            movement = analyze_camera_movement(cap, scene.start_frame, scene.end_frame, fps)
        except Exception:
            pass

        # Generate description
        description = ""
        if representative_frame is not None:
            try:
                description = describe_scene(representative_frame, num_faces, shot_type, movement)
            except Exception:
                description = f"{shot_type}，镜头{movement}"

        analyses.append({
            "shot_type": shot_type,
            "movement": movement,
            "description": description,
            "num_faces": num_faces,
        })

        pct = ((i + 1) / total) * 100
        progress_cb(f"正在生成 GIF 并分析 {i + 1}/{total}...", pct)

    cap.release()
    return paths, analyses


def create_storyboard_html(scenes: list, gif_paths: list, analyses: list,
                           title: str, director_summary: str,
                           output_path: str, progress_cb):
    """Generate an HTML storyboard with title, director summary, and 3-column grid."""
    progress_cb("正在生成分镜表...", 0)

    # Escape HTML
    def esc(text):
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    cards_html = []
    total = len(scenes)

    for i, scene in enumerate(scenes):
        duration = scene.end_time - scene.start_time
        timecode = format_timecode(scene.start_time)
        gif_rel = ""
        if i < len(gif_paths) and gif_paths[i] and os.path.exists(gif_paths[i]):
            gif_rel = f"{SHOT_DIR_NAME}/shot_{i + 1:03d}.gif"

        analysis = analyses[i] if i < len(analyses) else {}
        shot_type = esc(analysis.get("shot_type", ""))
        movement = esc(analysis.get("movement", ""))
        description = esc(analysis.get("description", ""))

        if gif_rel:
            img_tag = f'<img src="{gif_rel}" alt="Shot {i+1}">'
        else:
            img_tag = '<div class="no-img">无画面</div>'

        card = f"""    <div class="card">
      <div class="img-wrap">{img_tag}</div>
      <div class="info">
        <div class="row-main">
          <span class="shot-num">#{i + 1:03d}</span>
          <span class="timecode">{timecode}</span>
          <span class="duration">{duration:.1f}s</span>
        </div>
        <div class="row-desc">{description}</div>
        <div class="row-fields">
          <span class="field">景别：<b>{shot_type}</b></span>
          <span class="field">运镜：<b>{movement}</b></span>
        </div>
        <div class="row-note">备注：</div>
      </div>
    </div>"""
        cards_html.append(card)

        pct = ((i + 1) / total) * 100
        progress_cb(f"正在生成分镜表 {i + 1}/{total}...", pct)

    all_cards = "\n".join(cards_html)
    title_esc = esc(title)
    summary_esc = esc(director_summary)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title_esc} - 分镜表</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", Arial, sans-serif;
    background: #f5f5f5;
    padding: 24px;
    color: #333;
  }}
  .header {{
    max-width: 1200px;
    margin: 0 auto 20px auto;
    text-align: center;
  }}
  h1 {{
    font-size: 24px;
    color: #222;
    margin-bottom: 16px;
  }}
  .director-summary {{
    background: #e8f0fe;
    border: 1px solid #c5d7f2;
    border-radius: 8px;
    padding: 14px 20px;
    text-align: left;
    font-size: 14px;
    line-height: 1.8;
    color: #333;
  }}
  .director-summary .label {{
    font-weight: bold;
    color: #1a56db;
    margin-right: 6px;
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat({COLS_PER_ROW}, 1fr);
    gap: 16px;
    max-width: 1200px;
    margin: 0 auto;
  }}
  .card {{
    background: #fff;
    border: 2px solid #ccc;
    border-radius: 6px;
    overflow: hidden;
  }}
  .img-wrap {{
    background: #000;
    text-align: center;
    line-height: 0;
  }}
  .img-wrap img {{
    width: 100%;
    height: auto;
    display: block;
  }}
  .no-img {{
    color: #999;
    padding: 60px 0;
    font-size: 14px;
    line-height: 1;
  }}
  .info {{
    padding: 8px 10px;
    font-size: 12px;
    border-top: 1px solid #e0e0e0;
  }}
  .row-main {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 4px;
    font-weight: bold;
    font-size: 13px;
  }}
  .shot-num {{
    color: #1a73e8;
    font-size: 15px;
  }}
  .timecode {{
    font-family: "Consolas", "Courier New", monospace;
    color: #555;
  }}
  .duration {{
    color: #888;
  }}
  .row-desc {{
    color: #444;
    margin-bottom: 4px;
    line-height: 1.4;
    font-size: 12px;
  }}
  .row-fields {{
    display: flex;
    gap: 12px;
    margin-bottom: 4px;
    color: #555;
  }}
  .field b {{
    color: #1a56db;
  }}
  .row-note {{
    color: #999;
    min-height: 18px;
    border-top: 1px dashed #e0e0e0;
    padding-top: 3px;
    margin-top: 2px;
  }}
  @media print {{
    body {{ padding: 8px; background: #fff; }}
    .grid {{ gap: 8px; }}
    .card {{ break-inside: avoid; border-width: 1px; }}
    .director-summary {{ background: #f0f4fa; }}
  }}
</style>
</head>
<body>
<div class="header">
  <h1>{title_esc}</h1>
  <div class="director-summary">
    <span class="label">导演阐述：</span>{summary_esc}
  </div>
</div>
<div class="grid">
{all_cards}
</div>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    progress_cb("分镜表生成完成", 100)


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

def run_pipeline(url: str, threshold: float, progress_cb, done_cb, error_cb):
    temp_dir = tempfile.mkdtemp(prefix="scene_storyboard_")

    try:
        # Phase 1: Download (0% - 35%)
        dl_cb = make_phase_cb(progress_cb, 0, 35)
        dl_cb("准备下载...", 0)
        video_path, title = download_video(url, temp_dir, dl_cb)

        # Phase 2: Scene detection (35% - 55%)
        detect_cb = make_phase_cb(progress_cb, 35, 55)
        scenes = detect_scenes(video_path, threshold, detect_cb)

        # Phase 3: Generate GIFs + analyze (55% - 90%)
        output_dir = get_output_dir(title)
        gif_cb = make_phase_cb(progress_cb, 55, 90)
        gif_paths, analyses = capture_gifs_and_analyze(
            video_path, scenes, str(output_dir), gif_cb
        )

        # Phase 4: Generate HTML storyboard (90% - 100%)
        html_cb = make_phase_cb(progress_cb, 90, 100)
        director_summary = generate_director_summary(scenes, analyses, title)
        html_path = str(output_dir / STORYBOARD_FILENAME)
        create_storyboard_html(
            scenes, gif_paths, analyses, title, director_summary,
            html_path, html_cb
        )

        progress_cb("完成！", 100)
        done_cb(str(output_dir))

    except Exception as e:
        error_cb(str(e))
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tkinter UI
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("视频镜头分镜工具")
        self.root.geometry("560x300")
        self.root.resizable(False, False)
        self._running = False
        self._build_widgets()

    def _build_widgets(self):
        pad = {"padx": 12, "pady": 4}

        tk.Label(self.root, text="视频链接：").grid(row=0, column=0, sticky="w", **pad)
        self.url_entry = tk.Entry(self.root, width=52)
        self.url_entry.grid(row=0, column=1, columnspan=2, sticky="we", **pad)

        tk.Label(self.root, text="检测阈值：").grid(row=1, column=0, sticky="w", **pad)
        self.threshold_entry = tk.Entry(self.root, width=10)
        self.threshold_entry.insert(0, str(DEFAULT_THRESHOLD))
        self.threshold_entry.grid(row=1, column=1, sticky="w", **pad)
        tk.Label(self.root, text="（值越小越灵敏）", fg="gray").grid(
            row=1, column=2, sticky="w")

        self.start_btn = tk.Button(self.root, text="开始分析", width=16,
                                   command=self._on_start)
        self.start_btn.grid(row=2, column=0, columnspan=3, pady=10)

        self.progress = ttk.Progressbar(self.root, length=500, mode="determinate")
        self.progress.grid(row=3, column=0, columnspan=3, **pad)

        self.status_label = tk.Label(self.root, text="就绪", fg="gray", anchor="w")
        self.status_label.grid(row=4, column=0, columnspan=3, sticky="we", **pad)

        self.output_label = tk.Label(self.root, text="", fg="blue", cursor="hand2",
                                     anchor="w", wraplength=520)
        self.output_label.grid(row=5, column=0, columnspan=3, sticky="we", **pad)
        self.output_label.bind("<Button-1>", self._on_open_folder)
        self._output_path = None

        self.root.columnconfigure(1, weight=1)

    def _on_start(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("提示", "请输入视频链接")
            return

        try:
            threshold = float(self.threshold_entry.get().strip())
            if threshold <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("提示", "阈值请输入一个正数")
            return

        if self._running:
            return

        self._running = True
        self.start_btn.config(state="disabled")
        self.progress["value"] = 0
        self.status_label.config(text="正在启动...", fg="gray")
        self.output_label.config(text="")
        self._output_path = None

        thread = threading.Thread(
            target=run_pipeline,
            args=(url, threshold, self._on_progress, self._on_done, self._on_error),
            daemon=True,
        )
        thread.start()

    def _on_progress(self, message: str, percent: float):
        self.root.after(0, lambda: self._apply_progress(message, percent))

    def _apply_progress(self, message: str, percent: float):
        self.progress["value"] = min(percent, 100)
        self.status_label.config(text=message, fg="black")

    def _on_done(self, output_dir: str):
        self.root.after(0, lambda: self._apply_done(output_dir))

    def _apply_done(self, output_dir: str):
        self._running = False
        self.start_btn.config(state="normal")
        self.progress["value"] = 100
        self.status_label.config(text="分析完成！", fg="green")
        self._output_path = output_dir
        self.output_label.config(text=f"输出目录：{output_dir}（点击打开）")

    def _on_error(self, error_msg: str):
        self.root.after(0, lambda: self._apply_error(error_msg))

    def _apply_error(self, error_msg: str):
        self._running = False
        self.start_btn.config(state="normal")
        self.progress["value"] = 0
        self.status_label.config(text="出错了", fg="red")
        messagebox.showerror("错误", error_msg)

    def _on_open_folder(self, event=None):
        if self._output_path and os.path.isdir(self._output_path):
            try:
                open_folder(self._output_path)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
