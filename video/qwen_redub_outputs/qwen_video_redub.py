#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qwen 视频配音流水线（带详细中文注释）

整体流程：
  1) ASR 语音转写（带时间轴）：原音频 → 文本 + 每句起止时间（毫秒）
  2) 导出转写结果：JSON（含时间戳）、SRT 字幕、纯文本
  3) TTS 按时间轴合成：每段文字 → 女声 PCM → 时长对齐 → 与静音拼接成整轨
  4) 背景音处理：纯人声模式则跳过；否则优先 demucs 分离，否则对人声段 ducking
  5) 混音并封装：新音频 + 原视频 → 最终 MP4
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterable, List, Tuple

import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult
from dashscope.audio.qwen_tts_realtime import (
    AudioFormat,
    QwenTtsRealtime,
    QwenTtsRealtimeCallback,
)


# =============================================================================
# TTS 回调：接收 WebSocket 流式返回的音频数据，写入本地 PCM 文件
# =============================================================================

class TTSCallback(QwenTtsRealtimeCallback):
    """Qwen 实时 TTS 的回调类，用于接收服务端推送的 base64 音频块并写入文件。"""

    def __init__(self, output_pcm: Path) -> None:
        self.output_pcm = output_pcm   # 输出 PCM 文件路径
        self._done = threading.Event() # 用于在 session.finished 时通知主线程
        self._fh = None                 # 文件句柄
        self.error = None              # 若发生 error 事件则记录在此

    def on_open(self):
        """WebSocket 连接建立后打开文件，准备写入 PCM 数据。"""
        self._fh = self.output_pcm.open("wb")

    def on_close(self, code, msg):
        """连接关闭时关闭文件句柄。"""
        if self._fh:
            self._fh.close()

    def on_event(self, response):
        """
        处理服务端推送的事件：
        - response.audio.delta：解码 base64 后写入 PCM
        - session.finished：标记合成结束，唤醒 wait()
        - error：记录错误并结束等待
        """
        try:
            event_type = response.get("type", "")
            if event_type == "response.audio.delta":
                audio_b64 = response.get("delta", "")
                if audio_b64 and self._fh:
                    self._fh.write(base64.b64decode(audio_b64))
            elif event_type == "session.finished":
                self._done.set()
            elif event_type == "error":
                self.error = response.get("error")
                self._done.set()
        except Exception as exc:  # pragma: no cover
            self.error = {"message": str(exc)}
            self._done.set()

    def wait(self, timeout=60) -> bool:
        """阻塞直到 session.finished 或 error 或超时，返回是否在超时前完成。"""
        return self._done.wait(timeout)


# =============================================================================
# 通用工具：命令行执行、音视频信息、ASR 前处理与字幕输出
# =============================================================================

def run(cmd: List[str], *, check=True, capture=False) -> subprocess.CompletedProcess:
    """
    执行外部命令。check=True 时非零退出会抛 RuntimeError 并附带 stdout/stderr。
    capture=True 时捕获标准输出，用于 ffprobe 等读结果。
    """
    kwargs = {"text": True}
    if capture:
        kwargs["capture_output"] = True
    proc = subprocess.run(cmd, **kwargs)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(shlex.quote(x) for x in cmd)}\n"
            f"stdout:\n{proc.stdout if hasattr(proc, 'stdout') else ''}\n"
            f"stderr:\n{proc.stderr if hasattr(proc, 'stderr') else ''}"
        )
    return proc


def ffprobe_duration(path: Path) -> float:
    """用 ffprobe 读取音/视频时长（秒）。"""
    proc = run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture=True,
    )
    return float((proc.stdout or "0").strip())


def prepare_asr_audio(input_audio: Path, out_wav: Path) -> Path:
    """
    将任意音频转为 16kHz 单声道 PCM S16LE WAV，供 paraformer 实时 ASR 使用。
    ASR 模型对采样率/声道有要求，统一格式可避免识别异常。
    """
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_audio),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )
    return out_wav


def ms_to_srt(ms: int) -> str:
    """将毫秒数转为 SRT 时间轴格式：HH:MM:SS,mmm。"""
    if ms < 0:
        ms = 0
    sec, milli = divmod(ms, 1000)
    hour, rem = divmod(sec, 3600)
    minute, second = divmod(rem, 60)
    return f"{hour:02d}:{minute:02d}:{second:02d},{milli:03d}"


def write_srt(sentences: List[dict], srt_path: Path) -> None:
    """
    根据带 begin_time/end_time 的句子列表生成 SRT 字幕文件。
    每条格式：序号、时间轴行、文本、空行。
    """
    lines = []
    for idx, seg in enumerate(sentences, start=1):
        start = int(seg["begin_time"])
        end = int(seg["end_time"])
        text = seg["text"].strip()
        if not text:
            continue
        lines.append(str(idx))
        lines.append(f"{ms_to_srt(start)} --> {ms_to_srt(end)}")
        lines.append(text)
        lines.append("")
    srt_path.write_text("\n".join(lines), encoding="utf-8")


def normalize_asr_output(result) -> Tuple[str, List[dict]]:
    """
    将 Transcription 类 API 的返回结果统一成 (全文, 句子列表)。
    句子列表每项为 {"begin_time", "end_time", "text"}，时间单位为毫秒。
    本脚本实际使用 Recognition 实时接口，此函数保留以兼容其他 ASR 返回格式。
    """
    output = getattr(result, "output", None)
    if output is None:
        raise RuntimeError(f"ASR returned empty output: {result}")

    text = getattr(output, "text", "") or ""
    raw_sentences = getattr(output, "sentences", None)
    if raw_sentences is None and isinstance(output, dict):
        text = output.get("text", text)
        raw_sentences = output.get("sentences", [])
    if raw_sentences is None:
        raw_sentences = []

    sentences = []
    for item in raw_sentences:
        if isinstance(item, dict):
            start = int(item.get("begin_time", 0))
            end = int(item.get("end_time", 0))
            seg_text = (item.get("text") or "").strip()
        else:
            start = int(getattr(item, "begin_time", 0))
            end = int(getattr(item, "end_time", 0))
            seg_text = (getattr(item, "text", "") or "").strip()
        if end <= start or not seg_text:
            continue
        sentences.append({"begin_time": start, "end_time": end, "text": seg_text})

    return text, sentences


# =============================================================================
# ASR：阿里 paraformer 实时识别，得到带时间戳的句子列表
# =============================================================================

class _RecognitionCollector(RecognitionCallback):
    """在 Recognition 流式回调中收集每一句识别结果（含 begin_time/end_time/text）。"""

    def __init__(self):
        self.events: List[dict] = []

    def on_event(self, result: RecognitionResult) -> None:
        sentence = result.get_sentence()
        if isinstance(sentence, dict):
            self.events.append(sentence)


def transcribe_with_timestamps(audio_path: Path) -> Tuple[str, List[dict]]:
    """
    对整段音频做一次带时间戳的语音识别。
    - 使用 paraformer-realtime-v2，要求输入为 16kHz 单声道 WAV。
    - 返回：(拼接后的全文, 句子列表)，每句含 begin_time/end_time（毫秒）和 text。
    """
    collector = _RecognitionCollector()
    recognizer = Recognition(
        model="paraformer-realtime-v2",
        callback=collector,
        format="wav",
        sample_rate=16000,
        timestamp_alignment_enabled=True,  # 开启时间戳对齐，便于后续按句配音
    )
    result = recognizer.call(str(audio_path))
    status = getattr(result, "status_code", None)
    if status != 200:
        code = getattr(result, "code", "")
        message = getattr(result, "message", "")
        req_id = getattr(result, "request_id", "")
        raise RuntimeError(
            f"ASR failed: status={status}, code={code}, message={message}, request_id={req_id}"
        )

    raw = result.get_sentence()
    items: List[dict] = raw if isinstance(raw, list) else collector.events
    sentences: List[dict] = []
    for item in items:
        begin = int(item.get("begin_time", 0))
        end = int(item.get("end_time", 0) or 0)
        text = (item.get("text") or "").strip()
        if end <= begin or not text:
            continue
        sentences.append({"begin_time": begin, "end_time": end, "text": text})

    full_text = "".join(seg["text"] for seg in sentences)
    return full_text, sentences


# =============================================================================
# TTS：Qwen 实时语音合成与音频后处理
# =============================================================================

def tts_segment(text: str, output_pcm: Path, *, voice="Serena", timeout=60) -> None:
    """
    对一段文字调用 Qwen 实时 TTS，将 PCM 写入 output_pcm。
    使用 WebSocket，音色 voice（如 Serena 女声），输出 24kHz 单声道 16bit PCM。
    """
    callback = TTSCallback(output_pcm)
    tts = QwenTtsRealtime(
        model="qwen3-tts-flash-realtime",
        callback=callback,
        url="wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
    )
    tts.connect()
    tts.update_session(
        voice=voice,
        response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
        mode="server_commit",
    )
    tts.append_text(text)
    tts.finish()
    if not callback.wait(timeout):
        raise RuntimeError(f"TTS timeout: {text[:30]}...")
    if callback.error:
        raise RuntimeError(f"TTS error: {callback.error}")


def pcm_to_wav(pcm_path: Path, wav_path: Path) -> None:
    """将 24kHz 单声道 S16LE 的 PCM 转为 WAV，便于 ffmpeg 后续处理。"""
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "s16le",
            "-ar",
            "24000",
            "-ac",
            "1",
            "-i",
            str(pcm_path),
            "-ar",
            "24000",
            "-ac",
            "1",
            str(wav_path),
        ]
    )


def atempo_chain(tempo: float) -> str:
    """
    构造 ffmpeg atempo 滤镜链。单次 atempo 仅支持 (0.5, 2.0]，超出范围需多次串联。
    例如 tempo=4 → atempo=2,atempo=2；tempo=0.25 → atempo=0.5,atempo=0.5。
    """
    factors: List[float] = []
    if tempo <= 0:
        return "atempo=1.0"
    while tempo > 2.0:
        factors.append(2.0)
        tempo /= 2.0
    while tempo < 0.5:
        factors.append(0.5)
        tempo /= 0.5
    factors.append(tempo)
    return ",".join(f"atempo={f:.6f}" for f in factors)


def retime_audio(input_wav: Path, target_sec: float, output_wav: Path) -> None:
    """
    将 input_wav 通过变速（atempo）拉长或缩短到 target_sec 秒，写入 output_wav。
    用于把 TTS 生成的句子对齐到原视频该句的时间长度，保证口型/节奏一致。
    """
    src_sec = ffprobe_duration(input_wav)
    if src_sec <= 0 or target_sec <= 0:
        run(["cp", str(input_wav), str(output_wav)])
        return
    tempo = src_sec / target_sec
    chain = atempo_chain(tempo)
    run(["ffmpeg", "-y", "-i", str(input_wav), "-af", chain, str(output_wav)])


def create_silence(duration_sec: float, out_wav: Path) -> None:
    """生成指定时长（秒）的静音 WAV，24kHz 单声道，用于句与句之间的间隔。"""
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            f"{duration_sec:.3f}",
            str(out_wav),
        ]
    )


def concat_wavs(wavs: Iterable[Path], output_wav: Path, tmp_list: Path) -> None:
    """按顺序将多段 WAV 拼接成一条，使用 ffmpeg concat 协议，无重编码。"""
    lines = [f"file '{w.resolve()}'" for w in wavs]
    tmp_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp_list), "-c", "copy", str(output_wav)])


def build_dub_track(sentences: List[dict], workdir: Path, voice: str, rpm_sleep: float) -> Path:
    """
    根据带时间戳的句子列表，逐段调用 TTS、对齐时长、插入静音，最后拼接成一条配音轨。
    - 若当前句开始时间晚于上一句结束时间，中间补静音。
    - 每句：TTS 生成 PCM → 转 WAV → 按 (end_ms - start_ms) 做 retime → 加入 pieces。
    - rpm_sleep：每句合成后休眠秒数，避免 API 限流。
    返回拼接后的 WAV 路径：workdir/dubbed_speech.wav。
    """
    seg_dir = workdir / "tts_segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    pieces: List[Path] = []
    cursor_ms = 0  # 已合成到的时间位置（毫秒）

    for idx, seg in enumerate(sentences):
        start_ms = int(seg["begin_time"])
        end_ms = int(seg["end_time"])
        text = seg["text"].strip()
        if not text:
            continue

        # 与上一句之间有间隔时，插入静音
        if start_ms > cursor_ms:
            silence = seg_dir / f"silence_{idx:04d}.wav"
            create_silence((start_ms - cursor_ms) / 1000.0, silence)
            pieces.append(silence)

        raw_pcm = seg_dir / f"seg_{idx:04d}.pcm"
        raw_wav = seg_dir / f"seg_{idx:04d}_raw.wav"
        fit_wav = seg_dir / f"seg_{idx:04d}.wav"

        tts_segment(text, raw_pcm, voice=voice, timeout=80)
        pcm_to_wav(raw_pcm, raw_wav)
        target_sec = max((end_ms - start_ms) / 1000.0, 0.2)  # 至少 0.2 秒，避免过度压缩
        retime_audio(raw_wav, target_sec, fit_wav)
        pieces.append(fit_wav)

        cursor_ms = end_ms
        time.sleep(rpm_sleep)

    dubbed = workdir / "dubbed_speech.wav"
    concat_wavs(pieces, dubbed, workdir / "tts_concat.txt")
    return dubbed


def merge_sentences_to_chunks(
    sentences: List[dict], max_gap_ms: int = 1200, max_chars: int = 140
) -> List[dict]:
    """
    将相邻的 ASR 短句合并成较大的段落。
    合并条件：与上一句间隔 ≤ max_gap_ms 且合并后总字数 ≤ max_chars。
    这样可减少 TTS 调用次数，降低句间音色抖动，使整体更连贯。
    """
    chunks: List[dict] = []
    current = None

    for seg in sentences:
        start = int(seg["begin_time"])
        end = int(seg["end_time"])
        text = (seg["text"] or "").strip()
        if not text or end <= start:
            continue

        if current is None:
            current = {"begin_time": start, "end_time": end, "text": text}
            continue

        gap = start - int(current["end_time"])
        merged_len = len(current["text"]) + len(text)
        if gap <= max_gap_ms and merged_len <= max_chars:
            current["end_time"] = end
            current["text"] = current["text"] + text
        else:
            chunks.append(current)
            current = {"begin_time": start, "end_time": end, "text": text}

    if current is not None:
        chunks.append(current)

    return chunks


# =============================================================================
# 背景音处理与混音、视频封装
# =============================================================================

def try_demucs_background(audio_path: Path, workdir: Path) -> Path | None:
    """
    若当前环境已安装 demucs，则用人声分离得到“无人生”背景轨（no_vocals.wav）。
    未安装或执行失败则返回 None，主流程会改用 duck_background 作为降级方案。
    """
    check = run(
        [sys.executable, "-c", "import demucs; print('ok')"],
        check=False,
        capture=True,
    )
    if check.returncode != 0:
        return None

    out_dir = workdir / "demucs_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            "-m",
            "demucs",
            "--two-stems=vocals",
            "-n",
            "htdemucs",
            "--out",
            str(out_dir),
            str(audio_path),
        ]
    )
    candidate = out_dir / "htdemucs" / audio_path.stem / "no_vocals.wav"
    return candidate if candidate.exists() else None


def duck_background(audio_path: Path, sentences: List[dict], output_wav: Path) -> None:
    """
    在无人声分离时的降级方案：在原音频上对人声时间段做 ducking（压低音量）。
    仅在每句的 [begin_time-0.05s, end_time+0.05s] 内将音量设为 0.12，其余保持 1.0，
    得到一条“背景感”轨，再与 TTS 配音轨混音。
    """
    filters = []
    for seg in sentences:
        start = max(seg["begin_time"] / 1000.0 - 0.05, 0.0)
        end = seg["end_time"] / 1000.0 + 0.05
        filters.append(f"volume=0.12:enable='between(t,{start:.3f},{end:.3f})'")
    af = ",".join(filters) if filters else "volume=1.0"
    run(["ffmpeg", "-y", "-i", str(audio_path), "-af", af, str(output_wav)])


def mix_background_and_dub(bg_wav: Path, dub_wav: Path, mixed_wav: Path) -> None:
    """将背景轨与配音轨混合：背景 1.0 倍、配音 1.35 倍，amix 取较长者为时长，不归一化。"""
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(bg_wav),
            "-i",
            str(dub_wav),
            "-filter_complex",
            "[0:a]volume=1.0[bg];[1:a]volume=1.35[dub];[bg][dub]amix=inputs=2:duration=longest:normalize=0[a]",
            "-map",
            "[a]",
            str(mixed_wav),
        ]
    )


def mux_video(video_path: Path, audio_path: Path, output_video: Path) -> None:
    """
    用原视频的画面流 + 新音频轨封装成 MP4。
    视频流直接 copy，音频编码为 aac；-shortest 表示以较短的一路为准截断。
    """
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(output_video),
        ]
    )


# =============================================================================
# 命令行参数与主流程
# =============================================================================

def parse_args() -> argparse.Namespace:
    """解析命令行：输入输出路径、TTS 音色与限流、ASR 仅转写 / 纯人声 / 复用转写等选项。"""
    p = argparse.ArgumentParser(description="Qwen ASR+TTS 视频配音流水线")
    p.add_argument("--video", required=True, help="原始视频路径（必填）")
    p.add_argument("--audio", required=True, help="原始音频路径，建议 WAV（必填）")
    p.add_argument("--out-dir", default="video/qwen_redub_outputs", help="输出目录，存放转写、分段音频与最终视频")
    p.add_argument("--voice", default="Serena", help="TTS 音色，如 Serena 女声")
    p.add_argument("--sleep-seconds", type=float, default=10.0, help="每句 TTS 调用后休眠秒数，用于 API 限流")
    p.add_argument("--asr-only", action="store_true", help="仅执行 ASR，导出 json/srt/txt 后退出")
    p.add_argument("--speech-only", action="store_true", help="不保留背景音，仅用配音轨合成视频")
    p.add_argument(
        "--transcript-json",
        default="",
        help="已有转写 JSON 路径，指定则跳过 ASR 直接做 TTS 与后续步骤",
    )
    p.add_argument("--merge-gap-ms", type=int, default=1200, help="合并句子时的最大间隔（毫秒）")
    p.add_argument("--merge-max-chars", type=int, default=140, help="合并后段落的最大字数")
    return p.parse_args()


def main() -> None:
    """
    主流程：ASR（或复用转写）→ 导出字幕 → 可选合并段落 → TTS 按时间轴合成 → 背景处理 → 混音 → 封装视频。
    需设置环境变量 DASHSCOPE_API_KEY。
    """
    args = parse_args()
    video_path = Path(args.video).resolve()
    audio_path = Path(args.audio).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("missing DASHSCOPE_API_KEY environment variable")
    dashscope.api_key = api_key

    print("== Qwen video redub pipeline ==")
    print(f"video: {video_path}")
    print(f"audio: {audio_path}")
    print(f"audio duration: {ffprobe_duration(audio_path):.2f}s")

    transcript_json = out_dir / "transcript_with_timestamps.json"
    transcript_srt = out_dir / "transcript.srt"
    transcript_txt = out_dir / "transcript.txt"

    # ---------- 步骤 1：获取带时间戳的转写 ----------
    if args.transcript_json:
        print("\n[1/5] Reusing existing transcript ...")
        loaded = json.loads(Path(args.transcript_json).read_text(encoding="utf-8"))
        full_text = loaded.get("text", "")
        sentences = loaded.get("sentences", [])
    else:
        print("\n[1/5] ASR transcription with timestamps ...")
        asr_ready_audio = prepare_asr_audio(audio_path, out_dir / "asr_input_16k.wav")
        full_text, sentences = transcribe_with_timestamps(asr_ready_audio)

    if not sentences:
        raise RuntimeError("No timestamped sentences found")

    transcript_json.write_text(
        json.dumps({"text": full_text, "sentences": sentences}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    transcript_txt.write_text(full_text, encoding="utf-8")
    write_srt(sentences, transcript_srt)
    print(f"saved: {transcript_json}")
    print(f"saved: {transcript_srt}")

    if args.asr_only:
        print("\nASR only mode done.")
        return

    # ---------- 步骤 2：按时间轴 TTS 合成并拼接成配音轨 ----------
    print("\n[2/5] TTS by timestamps ...")
    tts_sentences = merge_sentences_to_chunks(
        sentences, max_gap_ms=args.merge_gap_ms, max_chars=args.merge_max_chars
    )
    print(f"tts chunks: {len(tts_sentences)} (from {len(sentences)} segments)")
    dub_speech = build_dub_track(tts_sentences, out_dir, args.voice, args.sleep_seconds)
    print(f"saved: {dub_speech}")

    # ---------- 步骤 3 / 4：背景音处理与混音 ----------
    if args.speech_only:
        print("\n[3/5] Speech-only mode: skip background track")
        mixed_audio = out_dir / "mixed_new_audio_speech_only.wav"
        run(["cp", str(dub_speech), str(mixed_audio)])
        print(f"saved: {mixed_audio}")
    else:
        print("\n[3/5] Build background track ...")
        bg_track = try_demucs_background(audio_path, out_dir)
        if bg_track is None:
            bg_track = out_dir / "background_ducked.wav"
            duck_background(audio_path, sentences, bg_track)
            print("demucs unavailable, used fallback ducking")
        else:
            print("demucs success, using no_vocals track")
        print(f"background: {bg_track}")

        print("\n[4/5] Mix background + new speech ...")
        mixed_audio = out_dir / "mixed_new_audio.wav"
        mix_background_and_dub(bg_track, dub_speech, mixed_audio)
        print(f"saved: {mixed_audio}")

    # ---------- 步骤 5：封装最终视频 ----------
    print("\n[5/5] Mux with original video ...")
    output_video = out_dir / (
        "final_dubbed_video_speech_only.mp4" if args.speech_only else "final_dubbed_video.mp4"
    )
    mux_video(video_path, mixed_audio, output_video)
    print(f"saved: {output_video}")
    print("\nDone.")


if __name__ == "__main__":
    main()
