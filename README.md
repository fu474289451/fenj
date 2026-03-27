# Video Scene Storyboard Tool

视频镜头分析与分镜表生成工具。输入在线视频链接，自动下载、检测镜头切换、截取代表帧，并生成带嵌入截图的 Excel 分镜表。

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
  shots/           # 所有镜头截图（shot_001.jpg, shot_002.jpg ...）
  storyboard.xlsx  # 分镜表格
```

### Excel 分镜表列说明

| 列 | 说明 |
|----|------|
| 镜头编号 | 自动编号 |
| 时间码（起始） | 镜头起始时间 HH:MM:SS.fff |
| 时长 | 镜头持续秒数 |
| 截图预览 | 嵌入的缩略图（160px 宽） |
| 景别 | 留空，手动填写（如：全景、中景、特写等） |
| 运镜 | 留空，手动填写（如：固定、推、拉、摇等） |
| 备注 | 留空，手动填写 |
