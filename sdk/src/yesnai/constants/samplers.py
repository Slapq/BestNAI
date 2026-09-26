"""Sampler, noise schedule, and undesired-content constants."""

from __future__ import annotations

from typing import Literal

Sampler = Literal[
    "k_euler",
    "k_euler_ancestral",
    "k_dpm_2",
    "k_dpm_2_ancestral",
    "k_dpmpp_2m",
    "k_dpmpp_2s_ancestral",
    "k_dpmpp_sde",
    "ddim",
]

K_EULER: Sampler = "k_euler"
K_EULER_ANCESTRAL: Sampler = "k_euler_ancestral"
K_DPM_2: Sampler = "k_dpm_2"
K_DPM_2_ANCESTRAL: Sampler = "k_dpm_2_ancestral"
K_DPM_FAST: Sampler = "k_dpmpp_2m"
K_DPM_ADAPTIVE: Sampler = "k_dpmpp_2s_ancestral"
K_DPM_SDE: Sampler = "k_dpmpp_sde"
DDIM: Sampler = "ddim"

NoiseSchedule = Literal["karras", "exponential", "polyexponential"]

NOISE_KARRAS: NoiseSchedule = "karras"
NOISE_EXPONENTIAL: NoiseSchedule = "exponential"
NOISE_POLYEXPONENTIAL: NoiseSchedule = "polyexponential"

UCPreset = Literal["strong", "light", "furry_focus", "human_focus", "none"]

UC_STRONG: UCPreset = "strong"
UC_LIGHT: UCPreset = "light"
UC_FURRY_FOCUS: UCPreset = "furry_focus"
UC_HUMAN_FOCUS: UCPreset = "human_focus"
UC_NONE: UCPreset = "none"

#: Integer `ucPreset` values used by the low-level NovelAI protocol.
UC_PRESET_TO_INT: dict[str, int] = {
    "strong": 0,
    "light": 1,
    "furry_focus": 2,
    "human_focus": 3,
    "none": 4,
}
UC_INT_TO_PRESET: dict[int, str] = {value: key for key, value in UC_PRESET_TO_INT.items()}

QUALITY_TAGS = "best quality, amazing quality, very aesthetic, absurdres"

UNDESIRED_CONTENT_PRESETS: dict[str, str] = {
    "heavy": (
        "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
        "fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, "
        "signature, watermark, username, blurry"
    ),
    "light": "lowres, bad anatomy, bad hands, text, error, extra digit, fewer digits, cropped, worst quality, low quality",
    "furry_focus": (
        "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
        "fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, "
        "signature, watermark, username, blurry, bad feet"
    ),
    "human_focus": (
        "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
        "fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, "
        "signature, watermark, username, blurry, bad feet, bad proportions"
    ),
    "none": "",
}

#: Streaming frame encodings the gateway can return.
StreamingType = Literal["msgpack", "sse"]

#: MessagePack is used by the raw `/ai/generate-image-stream` relay.
STREAM_MSGPACK: StreamingType = "msgpack"
