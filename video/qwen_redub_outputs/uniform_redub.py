#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
均匀语速配音脚本 v7
核心策略：
  1. 声音复刻：先生成参考音频，克隆为专属 voice_id，所有段复用同一音色
  2. 紧跟时间轴：保守合并(gap≤1.5s, chars≤100)，28段精确对齐
  3. 限速保护：atempo [0.70, 1.30]
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List

import dashscope
import requests
from dashscope.audio.qwen_tts_realtime import (
    AudioFormat,
    QwenTtsRealtime,
    QwenTtsRealtimeCallback,
)

TEMPO_MIN = 0.70
TEMPO_MAX = 1.30

VC_ENROLLMENT_MODEL = "qwen-voice-enrollment"
VC_TTS_MODEL = "qwen3-tts-vc-2026-01-22"
VC_TTS_REALTIME_MODEL = "qwen3-tts-vc-realtime-2026-01-15"

REFERENCE_MODEL = "qwen3-tts-instruct-flash"
REFERENCE_VOICE = "Serena"
REFERENCE_INSTRUCTIONS = (
    "你是一位专业的产品操作演示讲解员。"
    "请用平稳一致的语调朗读，语速中等偏慢，保持温柔清晰的音色，"
    "不要有过多的情绪起伏和语调变化，像在做软件功能介绍。"
)
REFERENCE_TEXT = (
    "供应商朋友们大家好，接下来由我为大家讲解供应商系统首页和系统管理模块的操作流程。"
    "首页包括代办事项、售后代办事项、商品代办事项、销售额统计。"
    "首先待办事项包括待认领订单、待发货订单、待签收订单、待确认空品单数量。"
    "系统管理包括公司信息、司机管理、角色管理和用户管理。"
)


def run(cmd: List[str], *, check=True, capture=False):
    kwargs = {"text": True}
    if capture:
        kwargs["capture_output"] = True
    proc = subprocess.run(cmd, **kwargs)
    if check and proc.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(shlex.quote(x) for x in cmd)}")
    return proc


def ffprobe_dur(path: Path) -> float:
    p = run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture=True)
    return float((p.stdout or "0").strip())


# ─── Phase 1: 生成参考音频 ───

def generate_reference_audio(out_wav: Path, api_key: str) -> None:
    """用 instruct 模型生成一段稳定的参考音频，作为声音复刻的输入。"""
    print("Phase 1: 生成参考音频...")
    dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'

    response = dashscope.MultiModalConversation.call(
        model=REFERENCE_MODEL,
        api_key=api_key,
        text=REFERENCE_TEXT,
        voice=REFERENCE_VOICE,
        language_type="Chinese",
        instructions=REFERENCE_INSTRUCTIONS,
        optimize_instructions=True,
        stream=True,
    )

    audio_chunks = []
    for chunk in response:
        if chunk.status_code != 200:
            raise RuntimeError(f"参考音频生成失败: {chunk.status_code} {chunk.message}")
        audio_obj = getattr(chunk.output, "audio", None)
        if audio_obj:
            b64 = audio_obj.get("data", "")
            if b64:
                audio_chunks.append(base64.b64decode(b64))

    if not audio_chunks:
        raise RuntimeError("参考音频无数据")

    pcm_tmp = out_wav.with_suffix(".ref_pcm")
    pcm_tmp.write_bytes(b"".join(audio_chunks))
    run(["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1",
         "-i", str(pcm_tmp), "-ar", "24000", "-ac", "1", str(out_wav)])
    pcm_tmp.unlink(missing_ok=True)
    print(f"  参考音频: {out_wav} ({ffprobe_dur(out_wav):.1f}s)")


# ─── Phase 2: 声音复刻 ───

def create_cloned_voice(ref_wav: Path, api_key: str,
                        target_model: str = VC_TTS_MODEL,
                        preferred_name: str = "narrator") -> str:
    """调用 qwen-voice-enrollment 创建专属音色，返回 voice_id。"""
    print("Phase 2: 声音复刻...")
    b64_audio = base64.b64encode(ref_wav.read_bytes()).decode()
    data_uri = f"data:audio/wav;base64,{b64_audio}"

    url = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
    payload = {
        "model": VC_ENROLLMENT_MODEL,
        "input": {
            "action": "create",
            "target_model": target_model,
            "preferred_name": preferred_name,
            "audio": {"data": data_uri},
        }
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"声音复刻失败: {resp.status_code} {resp.text}")

    voice_id = resp.json()["output"]["voice"]
    print(f"  voice_id: {voice_id}")
    return voice_id


# ─── Phase 3: TTS 合成（使用克隆音色） ───

def tts_one_vc(text: str, out_wav: Path, voice_id: str, api_key: str) -> None:
    """用克隆音色调用 qwen3-tts-vc HTTP API 合成语音。"""
    dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'

    response = dashscope.MultiModalConversation.call(
        model=VC_TTS_MODEL,
        api_key=api_key,
        text=text,
        voice=voice_id,
        language_type="Chinese",
        stream=True,
    )

    audio_chunks = []
    for chunk in response:
        if chunk.status_code != 200:
            raise RuntimeError(f"TTS error {chunk.status_code}: {chunk.message}")
        audio_obj = getattr(chunk.output, "audio", None)
        if audio_obj:
            b64 = audio_obj.get("data", "")
            if b64:
                audio_chunks.append(base64.b64decode(b64))

    if not audio_chunks:
        raise RuntimeError(f"TTS 无数据: {text[:30]}...")

    pcm_tmp = out_wav.with_suffix(".vc_pcm")
    pcm_tmp.write_bytes(b"".join(audio_chunks))
    run(["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1",
         "-i", str(pcm_tmp), "-ar", "24000", "-ac", "1", str(out_wav)])
    pcm_tmp.unlink(missing_ok=True)


# ─── 音频处理工具 ───

def atempo_chain(tempo: float) -> str:
    factors = []
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


def retime_clamped(in_wav: Path, target_sec: float, out_wav: Path) -> float:
    src = ffprobe_dur(in_wav)
    if src <= 0 or target_sec <= 0:
        run(["cp", str(in_wav), str(out_wav)])
        return src
    raw_tempo = src / target_sec
    clamped = max(TEMPO_MIN, min(TEMPO_MAX, raw_tempo))
    chain = atempo_chain(clamped)
    run(["ffmpeg", "-y", "-i", str(in_wav), "-af", chain, str(out_wav)])
    return ffprobe_dur(out_wav)


def silence_wav(dur: float, out: Path):
    if dur < 0.01:
        dur = 0.01
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", f"{dur:.3f}", str(out)])


def concat_wavs(wavs: List[Path], out: Path, listf: Path):
    listf.write_text("\n".join(f"file '{w.resolve()}'" for w in wavs) + "\n", encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listf), "-c", "copy", str(out)])


# ─── 合并逻辑 ───

def merge_sentences(sentences: List[dict], max_gap_ms=1500, max_chars=100) -> List[dict]:
    if not sentences:
        return []
    filtered = [s for s in sentences if (s.get("text") or "").strip()]
    if not filtered:
        return []
    chunks: List[dict] = []
    cur = None
    for seg in filtered:
        start = int(seg["begin_time"])
        end = int(seg["end_time"])
        text = seg["text"].strip()
        if cur is None:
            cur = {"begin_time": start, "end_time": end, "text": text}
            continue
        gap = start - int(cur["end_time"])
        merged_len = len(cur["text"]) + len(text)
        force_merge = len(text) <= 2
        if force_merge or (gap <= max_gap_ms and merged_len <= max_chars):
            cur["end_time"] = end
            cur["text"] = cur["text"] + "，" + text if len(text) <= 3 else cur["text"] + text
        else:
            chunks.append(cur)
            cur = {"begin_time": start, "end_time": end, "text": text}
    if cur:
        chunks.append(cur)
    return chunks


# ─── 组装配音轨 ───

def build_track(chunks: List[dict], workdir: Path, voice_id: str,
                api_key: str, sleep_s: float) -> Path:
    seg_dir = workdir / "v7_segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    pieces: List[Path] = []
    cursor_ms = 0

    for idx, ch in enumerate(chunks):
        start_ms = int(ch["begin_time"])
        end_ms = int(ch["end_time"])
        text = ch["text"].strip()
        target_sec = max((end_ms - start_ms) / 1000.0, 0.3)

        print(f"  [{idx+1}/{len(chunks)}] {start_ms/1000:.1f}s-{end_ms/1000:.1f}s "
              f"({target_sec:.1f}s, {len(text)}字) {text[:50]}...")

        if start_ms > cursor_ms:
            sil = seg_dir / f"sil_{idx:03d}.wav"
            silence_wav((start_ms - cursor_ms) / 1000.0, sil)
            pieces.append(sil)

        raw = seg_dir / f"seg_{idx:03d}_raw.wav"
        fit = seg_dir / f"seg_{idx:03d}.wav"

        tts_one_vc(text, raw, voice_id, api_key)

        raw_dur = ffprobe_dur(raw)
        raw_tempo = raw_dur / target_sec if target_sec > 0 else 1.0
        actual_dur = retime_clamped(raw, target_sec, fit)
        clamped_tempo = max(TEMPO_MIN, min(TEMPO_MAX, raw_tempo))
        print(f"    TTS={raw_dur:.1f}s 目标={target_sec:.1f}s "
              f"tempo={raw_tempo:.2f}→{clamped_tempo:.2f} 实际={actual_dur:.1f}s")
        pieces.append(fit)

        if actual_dur < target_sec - 0.05:
            pad = seg_dir / f"pad_{idx:03d}.wav"
            silence_wav(target_sec - actual_dur, pad)
            pieces.append(pad)

        cursor_ms = end_ms
        time.sleep(sleep_s)

    out = workdir / "v7_dubbed.wav"
    concat_wavs(pieces, out, workdir / "v7_concat.txt")
    return out


def main():
    transcript_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "/Users/shaoshuai/Desktop/yzh/openclaw/video/qwen_redub_outputs/transcript_with_timestamps.json")
    video_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(
        "/Users/shaoshuai/Desktop/yzh/openclaw/video/4daca3b27461a9f2f4e87e5a1c667a72.mov")
    out_dir = transcript_path.parent

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("set DASHSCOPE_API_KEY")
    dashscope.api_key = api_key

    # Phase 1: 生成参考音频
    ref_wav = out_dir / "reference_voice.wav"
    if not ref_wav.exists():
        generate_reference_audio(ref_wav, api_key)
    else:
        print(f"Phase 1: 复用已有参考音频 {ref_wav} ({ffprobe_dur(ref_wav):.1f}s)")

    # Phase 2: 声音复刻
    voice_id = create_cloned_voice(ref_wav, api_key, target_model=VC_TTS_MODEL)

    # Phase 3: 加载时间轴并合并
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    sentences = data.get("sentences", [])
    chunks = merge_sentences(sentences, max_gap_ms=1500, max_chars=100)

    print(f"\nPhase 3: TTS 合成")
    print(f"  原始句子: {len(sentences)}")
    print(f"  合并后: {len(chunks)} 段")
    print(f"  voice_id: {voice_id}")
    print(f"  TTS model: {VC_TTS_MODEL}\n")

    for i, ch in enumerate(chunks):
        dur = (ch["end_time"] - ch["begin_time"]) / 1000.0
        print(f"  段{i+1}: {ch['begin_time']/1000:.1f}s-{ch['end_time']/1000:.1f}s "
              f"({dur:.1f}s) {len(ch['text'])}字")

    print("\n开始生成...")
    dubbed = build_track(chunks, out_dir, voice_id, api_key, sleep_s=1.0)
    print(f"\n配音轨: {dubbed} ({ffprobe_dur(dubbed):.1f}s)")

    output_video = out_dir / "speech_video_v7.mp4"
    print("封装视频...")
    run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(dubbed),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac",
        "-shortest",
        str(output_video),
    ])
    print(f"完成: {output_video} ({os.path.getsize(output_video)/1024/1024:.1f}MB)")


if __name__ == "__main__":
    main()
