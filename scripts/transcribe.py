# -*- coding: utf-8 -*-
"""whisper.cpp 转录模块 — smart-summarize v0.6.0 模块化拆分

唯一转录引擎：查找本机 whisper.cpp 构建（含 GPU 后端检测/上报）、ffmpeg 转码、
音频/视频转录入口。是否使用 GPU 取决于用户安装的构建（Vulkan/Metal/CUDA）。
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from deps import MissingDependencyError, _missing_dep_kinds, MANAGED_BIN, MANAGED_MODELS
from extractors import extract_text_file  # noqa: F401 (re-export for compat)
from slicing import make_tmpdir


def _default_whispercpp_dir():
    return MANAGED_BIN


def _default_whispercpp_models_dir():
    return MANAGED_MODELS


WHISPERCPP_DIR = Path(os.environ.get(
    "SMART_SUMMARIZE_WHISPERCPP_DIR",
    str(_default_whispercpp_dir()),
)).expanduser()
WHISPERCPP_MODELS_DIR = Path(os.environ.get(
    "SMART_SUMMARIZE_WHISPERCPP_MODELS_DIR",
    str(_default_whispercpp_models_dir()),
)).expanduser()
# faster-whisper 风格模型名 -> whisper.cpp ggml 模型文件
WHISPERCPP_GGML_MAP = {
    "large-v3-turbo": "ggml-large-v3-turbo.bin",          # fp16，参考精度（默认）
    "large-v3-turbo-q5_0": "ggml-large-v3-turbo-q5_0.bin",  # 量化快速档
}

NO_GPU = False


def _find_ffmpeg():
    configured = os.environ.get("SMART_SUMMARIZE_FFMPEG")
    if configured:
        p = Path(configured).expanduser()
        if p.exists() and p.is_file():
            return str(p)
    p = shutil.which("ffmpeg")
    if p:
        return p
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    managed = MANAGED_BIN / exe
    if managed.exists() and managed.is_file():
        return str(managed)
    return None


def _find_whispercpp_cli():
    configured = os.environ.get("SMART_SUMMARIZE_WHISPERCPP_CLI")
    if configured:
        p = Path(configured).expanduser()
        if p.exists() and p.is_file():
            return p

    # 先查 PATH，便于 macOS/Linux 通过包管理器或自行安装后直接使用。
    path_cli = shutil.which("whisper-cli")
    if path_cli:
        return Path(path_cli)

    names = ("whisper-cli.exe", "whisper-cli") if os.name == "nt" else ("whisper-cli", "whisper-cli.exe")
    for name in names:
        for base in (WHISPERCPP_DIR, MANAGED_BIN):
            candidate = base / name
            if candidate.exists() and candidate.is_file():
                return candidate
    return None


def _model_candidates(model_name):
    """该模型所有可能的位置（按查找/下载优先级）"""
    fname = WHISPERCPP_GGML_MAP.get(model_name, f"ggml-{model_name}.bin")
    dirs = []
    for d in (WHISPERCPP_MODELS_DIR, MANAGED_MODELS):
        d = Path(d).expanduser()
        if d not in dirs:
            dirs.append(d)
    return [d / fname for d in dirs]


def _find_model_file(model_name):
    for p in _model_candidates(model_name):
        if p.exists() and p.is_file():
            return p
    return None


def _whispercpp_available(model_name):
    cli = _find_whispercpp_cli()
    return cli is not None and _find_model_file(model_name) is not None


def _whispercpp_transcribe(file_path, model_name, want_srt):
    """使用本机 whisper.cpp 构建转录；成功返回 SRT 或纯文本，失败返回 None。"""
    cli = _find_whispercpp_cli()
    ggml = _find_model_file(model_name)
    ffmpeg = _find_ffmpeg()
    if not (cli and ggml and ffmpeg):
        return None
    tmpdir = make_tmpdir(f"ss_wcpp_{Path(file_path).stem}_")
    try:
        wav = tmpdir / "audio16k.wav"
        cmd = [ffmpeg, "-i", str(file_path), "-vn", "-acodec", "pcm_s16le",
               "-ar", "16000", "-ac", "1", str(wav), "-y"]
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=600)
        if r.returncode != 0 or not wav.exists():
            return None
        out_base = tmpdir / "out"
        # 不用 -np：它会把 ggml 后端日志一起吞掉，导致 GPU 后端上报失效；
        # 日志走 stderr，不影响 stdout 的 SRT/JSON 输出
        cmd = [str(cli), "-m", str(ggml), "-f", str(wav), "-l", "auto",
               "-osrt", "-of", str(out_base)]
        if NO_GPU:
            cmd += ["-ng"]
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=3600)
        srt_file = tmpdir / "out.srt"
        if not srt_file.exists():
            return None
        content = srt_file.read_text(encoding="utf-8", errors="replace")
        backend = _detect_backend_from_log(r.stderr or "")
        if NO_GPU:
            print(f"  🖥 转录完成（已强制 CPU）: {model_name}", file=sys.stderr)
        elif backend:
            print(f"  🎮 转录完成（GPU 加速: {backend}）: {model_name}", file=sys.stderr)
        else:
            print(f"  ✅ 转录完成（CPU）: {model_name}", file=sys.stderr)
        if want_srt:
            return content.strip() or None
        lines = [l.strip() for l in content.splitlines()
                 if l.strip() and "-->" not in l and not re.fullmatch(r"\d+", l.strip())]
        text = " ".join(lines).strip()
        return text if len(text) > 10 else None
    except Exception as e:
        print(f"  ⚠️ whisper.cpp 转录错误: {e}", file=sys.stderr)
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _detect_backend_from_log(stderr_text):
    """从 whisper-cli 的 stderr 判断实际使用的计算后端，返回可读描述。"""
    text = stderr_text or ""
    # ggml_vulkan 设备行两种格式：新版 "0 | AMD Radeon..."，旧版 "0 = AMD Radeon..."
    m = re.search(r"ggml_vulkan:.*?\d+\s*[\|=]\s*([A-Za-z][^\r\n]+)", text)
    if m:
        return f"Vulkan: {m.group(1).strip()[:80]}"
    m = re.search(r"ggml_cuda[^\n]*?device\s*\d*\s*\|?\s*([^\r\n]*)", text, re.I)
    if m:
        return f"CUDA: {m.group(1).strip()[:80]}"
    if "ggml_metal" in text or "Metal" in text:
        return "Metal"
    if re.search(r"ggml_hip|ROCm", text, re.I):
        return "ROCm/HIP"
    return None


def extract_audio_text(file_path, model="large-v3-turbo"):
    """提取音频文件内容（语音转文字）- 返回纯文本"""
    kinds = _missing_dep_kinds(model)
    if kinds:
        raise MissingDependencyError(kinds)
    return _whispercpp_transcribe(file_path, model, want_srt=False)


def extract_audio_srt(file_path, model="large-v3-turbo"):
    """提取音频文件内容（语音转文字）- 返回 SRT 格式"""
    kinds = _missing_dep_kinds(model)
    if kinds:
        raise MissingDependencyError(kinds)
    return _whispercpp_transcribe(file_path, model, want_srt=True)


def extract_video_text(file_path):
    """提取视频文件内容"""
    tmpdir = make_tmpdir(f"ss_vid_{Path(file_path).stem}_")

    try:
        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            raise MissingDependencyError(["ffmpeg"])
        # 先尝试提取内置字幕
        try:
            output_file = tmpdir / 'subtitle.srt'
            cmd = [ffmpeg, '-i', file_path, '-map', '0:s:0', str(output_file), '-y']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and output_file.exists():
                content = output_file.read_text(encoding='utf-8', errors='ignore')
                text = re.sub(r'\d+\n\d{2}:\d{2}:\d{2}.*?\n\n', '', content, flags=re.DOTALL)
                if text.strip():
                    return text.strip()
        except Exception:
            pass

        # 提取音频并转录
        audio_file = tmpdir / 'audio.wav'
        cmd = [ffmpeg, '-i', file_path, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', str(audio_file), '-y']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and audio_file.exists():
            return extract_audio_text(str(audio_file))
    except MissingDependencyError:
        raise
    except Exception as e:
        print(f"  ⚠️ 视频处理错误: {e}", file=sys.stderr)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return None