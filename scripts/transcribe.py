# -*- coding: utf-8 -*-
"""whisper.cpp 转录模块

唯一转录引擎：ffmpeg 转码、音频/视频转录入口。是否使用 GPU 取决于用户安装的
构建（Vulkan/Metal/CUDA）；组件定位与缺失检测见 deps.py。
"""
import re
import shutil
import subprocess
from pathlib import Path

from deps import (MissingDependencyError, _missing_dep_kinds, _find_ffmpeg,  # noqa: F401 (re-export)
                  _find_whispercpp_cli, _find_model_file)
from slicing import make_tmpdir
import messages

NO_GPU = False


def _whispercpp_transcribe(file_path, model_name, want_srt):
    """使用本机 whisper.cpp 构建转录；成功返回 SRT 或纯文本，失败返回 None。"""
    cli = _find_whispercpp_cli()
    ggml = _find_model_file(model_name)
    ffmpeg = _find_ffmpeg()
    if not (cli and ggml and ffmpeg):
        return None
    tmpdir = make_tmpdir(f"myag_wcpp_{Path(file_path).stem}_")
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
            messages.warn("trans_done_cpu_forced", model=model_name)
        elif backend:
            messages.warn("trans_done_gpu", backend=backend, model=model_name)
        else:
            messages.warn("trans_done_cpu", model=model_name)
        if want_srt:
            return content.strip() or None
        lines = [l.strip() for l in content.splitlines()
                 if l.strip() and "-->" not in l and not re.fullmatch(r"\d+", l.strip())]
        text = " ".join(lines).strip()
        return text if len(text) > 10 else None
    except Exception as e:
        messages.warn("transcribe_error", err=e)
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
    tmpdir = make_tmpdir(f"myag_vid_{Path(file_path).stem}_")

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
        messages.warn("video_error", err=e)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return None
