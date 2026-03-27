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
from pathlib import Path
from collections import namedtuple

import tkinter as tk
from tkinter import ttk, messagebox

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

    ydl_opts = {
        "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
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


def capture_scene_gifs(video_path: str, scenes: list, output_dir: str,
                       progress_cb) -> list:
    """For each scene, sample frames evenly and save as animated GIF."""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 24.0

    shots_dir = os.path.join(output_dir, SHOT_DIR_NAME)
    os.makedirs(shots_dir, exist_ok=True)

    paths = []
    total = len(scenes)

    for i, scene in enumerate(scenes):
        scene_frames = scene.end_frame - scene.start_frame
        if scene_frames <= 0:
            scene_frames = 1

        # Determine how many frames to sample (up to GIF_MAX_FRAMES)
        num_samples = min(GIF_MAX_FRAMES, max(1, scene_frames))
        # Calculate step to sample evenly across the scene
        if num_samples == 1:
            sample_indices = [scene.start_frame]
        else:
            step = scene_frames / num_samples
            sample_indices = [int(scene.start_frame + step * j) for j in range(num_samples)]

        pil_frames = []
        for frame_idx in sample_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            # Convert BGR -> RGB
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = PILImage.fromarray(rgb)
            # Resize to GIF_WIDTH, keep aspect ratio
            orig_w, orig_h = pil_img.size
            if orig_w > 0:
                scale = GIF_WIDTH / orig_w
                new_h = int(orig_h * scale)
                pil_img = pil_img.resize((GIF_WIDTH, new_h), PILImage.LANCZOS)
            pil_frames.append(pil_img)

        filename = f"shot_{i + 1:03d}.gif"
        filepath = os.path.join(shots_dir, filename)

        if pil_frames:
            frame_duration = 1000 // GIF_FPS  # ms per frame
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

        pct = ((i + 1) / total) * 100
        progress_cb(f"正在生成 GIF {i + 1}/{total}...", pct)

    cap.release()
    return paths


def create_storyboard_html(scenes: list, gif_paths: list,
                           output_path: str, progress_cb):
    """Generate an HTML storyboard with 3-column grid layout."""
    progress_cb("正在生成分镜表...", 0)

    cards_html = []
    total = len(scenes)

    for i, scene in enumerate(scenes):
        duration = scene.end_time - scene.start_time
        timecode = format_timecode(scene.start_time)
        gif_rel = ""
        if i < len(gif_paths) and gif_paths[i] and os.path.exists(gif_paths[i]):
            gif_rel = f"{SHOT_DIR_NAME}/shot_{i + 1:03d}.gif"

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
        <div class="row-fields">
          <span>景别：________</span>
          <span>运镜：________</span>
        </div>
        <div class="row-note">备注：</div>
      </div>
    </div>"""
        cards_html.append(card)

        pct = ((i + 1) / total) * 100
        progress_cb(f"正在生成分镜表 {i + 1}/{total}...", pct)

    all_cards = "\n".join(cards_html)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>分镜表 Storyboard</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", Arial, sans-serif;
    background: #f5f5f5;
    padding: 24px;
    color: #333;
  }}
  h1 {{
    text-align: center;
    margin-bottom: 24px;
    font-size: 22px;
    color: #222;
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
    font-size: 13px;
    border-top: 1px solid #e0e0e0;
  }}
  .row-main {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 4px;
    font-weight: bold;
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
  .row-fields {{
    display: flex;
    gap: 16px;
    margin-bottom: 4px;
    color: #666;
  }}
  .row-note {{
    color: #666;
    min-height: 20px;
  }}
  @media print {{
    body {{ padding: 8px; background: #fff; }}
    .grid {{ gap: 8px; }}
    .card {{ break-inside: avoid; border-width: 1px; }}
  }}
</style>
</head>
<body>
<h1>分镜表 Storyboard</h1>
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
        # Phase 1: Download (0% - 40%)
        dl_cb = make_phase_cb(progress_cb, 0, 40)
        dl_cb("准备下载...", 0)
        video_path, title = download_video(url, temp_dir, dl_cb)

        # Phase 2: Scene detection (40% - 65%)
        detect_cb = make_phase_cb(progress_cb, 40, 65)
        scenes = detect_scenes(video_path, threshold, detect_cb)

        # Phase 3: Generate GIFs (65% - 90%)
        output_dir = get_output_dir(title)
        gif_cb = make_phase_cb(progress_cb, 65, 90)
        gif_paths = capture_scene_gifs(video_path, scenes, str(output_dir), gif_cb)

        # Phase 4: Generate HTML storyboard (90% - 100%)
        html_cb = make_phase_cb(progress_cb, 90, 100)
        html_path = str(output_dir / STORYBOARD_FILENAME)
        create_storyboard_html(scenes, gif_paths, html_path, html_cb)

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
