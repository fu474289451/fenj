# Video Scene Storyboard Tool

视频镜头分析与分镜表生成工具。输入在线视频链接，自动下载、检测镜头切换、为每个镜头生成动画 GIF，并生成 HTML 分镜板（3列网格排版，可直接打印）。

## 环境要求

- Python 3.9+
- **ffmpeg** 已安装并在系统 PATH 中（yt-dlp 合并音视频需要）
  - Windows: 从 https://www.gyan.dev/ffmpeg/builds/ 下载，解压后将 `bin` 目录加入 PATH
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`

## 安装

```bash
pip install -r requirements.txt
```

> 注意：tkinter 随 Python 标准库一起安装（Windows/macOS 默认包含）。
> Linux 用户如缺少 tkinter，请运行：`sudo apt-get install python3-tk`

## 运行

```bash
python scene_storyboard.py
```

## 使用方法

1. 启动程序后，在输入框中粘贴视频链接（支持 YouTube 及通用 MP4 链接）
2. 可调整「检测阈值」（默认 27.0，值越小检测越灵敏、镜头越多；值越大越不灵敏）
3. 点击「开始分析」
4. 等待进度条完成
5. 完成后会显示输出文件夹路径，点击可直接打开

## 输出内容

在桌面生成以视频标题命名的文件夹，包含：

```
{视频标题}/
  shots/              # 每个镜头的动画 GIF（shot_001.gif, shot_002.gif ...）
  storyboard.html     # 分镜板（浏览器打开，3列网格排版）
```

### 分镜板说明

用浏览器打开 `storyboard.html`，每个镜头卡片包含：
- 动画 GIF 预览（可看到镜头完整运动）
- 镜头编号、起始时间码、时长
- 景别 / 运镜 / 备注（留空，打印后手写填写）

支持 `Ctrl+P` 打印，自动适配打印布局。
