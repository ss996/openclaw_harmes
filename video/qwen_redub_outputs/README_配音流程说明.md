# Qwen 视频配音流程说明

## 一、整体处理步骤概览

```
原视频/原音频
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 步骤 1：ASR 语音转写（带时间轴）                                    │
│  - 将原音频转为 16kHz 单声道，供 ASR 使用                           │
│  - 调用阿里 paraformer-realtime-v2 识别，得到每句话的 begin_time/end_time │
│  - 输出：transcript_with_timestamps.json、transcript.srt、transcript.txt │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 步骤 2：TTS 按时间轴合成配音                                        │
│  - 可选：将相邻短句合并成较大段落（减少切换，音色更连贯）               │
│  - 对每段文字：调用 Qwen TTS 实时接口生成 PCM → 转 WAV               │
│  - 按原时间轴做时长对齐（加速/减速），并在句间插入静音                 │
│  - 将所有片段按时间顺序拼接成一条 dubbed_speech.wav                   │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 步骤 3：背景音处理（二选一）                                        │
│  - 纯人声模式（--speech-only）：不混背景，直接用配音轨                 │
│  - 保留背景模式：优先用 demucs 分离出“无人生”轨，否则对人声段做 ducking │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 步骤 4：混音（仅保留背景时）                                        │
│  - 背景轨 + 新配音轨 做 amix，得到 mixed_new_audio.wav               │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 步骤 5：封装最终视频                                                │
│  - 原视频画面 + 新音频轨 → final_dubbed_video.mp4 / final_dubbed_video_speech_only.mp4 │
└─────────────────────────────────────────────────────────────────┘
```

## 二、核心代码逻辑说明

| 模块 | 函数/类 | 作用 |
|------|---------|------|
| **ASR 准备** | `prepare_asr_audio()` | 用 ffmpeg 把任意音频转成 16kHz 单声道 PCM，满足实时 ASR 输入要求 |
| **ASR 转写** | `transcribe_with_timestamps()` | 调用 Recognition(paraformer-realtime-v2)，读整段 WAV，返回带毫秒时间戳的句子列表 |
| **字幕输出** | `ms_to_srt()` / `write_srt()` | 把毫秒时间转为 SRT 时间轴格式并写入 transcript.srt |
| **TTS 回调** | `TTSCallback` | 接收 Qwen TTS WebSocket 返回的 base64 音频流，写入 PCM 文件，并在 session.finished 时结束等待 |
| **TTS 单句** | `tts_segment()` | 对一段文字发起一次 TTS 会话，输出 PCM，可选 voice（如 Serena） |
| **时长对齐** | `retime_audio()` / `atempo_chain()` | 用 ffmpeg atempo 把 TTS 生成的音频拉长或缩短到目标时长，保证和原时间轴一致 |
| **静音** | `create_silence()` | 用 ffmpeg anullsrc 生成指定秒数的静音 WAV，填在句与句之间 |
| **拼接** | `concat_wavs()` | 用 ffmpeg concat 协议按顺序拼接多段 WAV，得到整条配音轨 |
| **配音轨构建** | `build_dub_track()` | 按时间轴循环：句前补静音 → TTS 合成 → 时长对齐 → 加入 pieces，最后 concat 成 dubbed_speech.wav |
| **段落合并** | `merge_sentences_to_chunks()` | 把间隔小于 max_gap_ms、总长不超过 max_chars 的相邻句合并成一大段，减少 TTS 切换、提高音色连贯性 |
| **背景-人声分离** | `try_demucs_background()` | 若已安装 demucs，则用 htdemucs 分离出 no_vocals.wav 作为背景轨 |
| **背景 ducking** | `duck_background()` | 无 demucs 时：在人声时间段内把原音轨音量压到 0.12，其余保持，作为背景轨 |
| **混音** | `mix_background_and_dub()` | 背景轨 + 配音轨 amix，输出 mixed_new_audio.wav |
| **封装视频** | `mux_video()` | 原视频流 + 新音频轨，-c:v copy -c:a aac，-shortest 按音频长度截断 |

## 三、命令行参数说明

| 参数 | 含义 |
|------|------|
| `--video` | 原始视频文件路径（必填） |
| `--audio` | 原始音频文件路径（必填，建议 WAV） |
| `--out-dir` | 所有中间文件与最终视频的输出目录 |
| `--voice` | TTS 音色，默认 Serena（女声） |
| `--sleep-seconds` | 每句 TTS 调用之间的休眠秒数，用于规避 API 限流 |
| `--asr-only` | 只跑 ASR，输出 json/srt/txt 后退出 |
| `--speech-only` | 不保留背景音，只用人声轨合成视频 |
| `--transcript-json` | 指定已有转写 JSON，跳过 ASR，直接做 TTS 与后续步骤 |
| `--merge-gap-ms` | 合并句子时的最大间隔（毫秒） |
| `--merge-max-chars` | 合并后的段落最大字数 |

## 四、环境要求

- **DASHSCOPE_API_KEY**：环境变量，阿里云百炼 API Key
- **Python**：需能 `import dashscope`（含 audio.asr、qwen_tts_realtime）
- **ffmpeg**：用于音频重采样、静音、拼接、时长调整、混音、封装视频
- **可选 demucs**：保留背景音且做人声分离时需安装 `demucs`

## 五、输出文件一览

| 文件 | 说明 |
|------|------|
| `transcript_with_timestamps.json` | 带 begin_time/end_time 的完整转写 |
| `transcript.srt` | 字幕格式时间轴 |
| `transcript.txt` | 纯文本全文 |
| `asr_input_16k.wav` | 供 ASR 使用的 16k 单声道音频（未指定 --transcript-json 时生成） |
| `tts_segments/` | 每段 TTS 的 PCM/WAV 及静音片段 |
| `dubbed_speech.wav` | 按时间轴拼接后的完整配音轨 |
| `mixed_new_audio.wav` / `mixed_new_audio_speech_only.wav` | 最终用于封装的音频（含或不含背景） |
| `final_dubbed_video.mp4` / `final_dubbed_video_speech_only.mp4` | 最终配音视频 |
