"""以 Web Audio 风格的振荡器和增益包络离线合成提示音。"""

from dataclasses import dataclass
from functools import cache
from io import BytesIO
import math
import struct
import wave

SAMPLE_RATE = 44100


@dataclass(frozen=True)
class Tone:
    frequency: float
    start: float = 0
    peak: float = .16
    attack: float = .006
    decay: float = .45
    waveform: str = "sine"
    glide_to: float | None = None


PRESETS = {
    "chime": ("清脆双响", (Tone(880, peak=.20, decay=.42), Tone(1318.5, start=.18, peak=.18, decay=.55))),
    "wood": ("木鱼", (Tone(220, peak=.34, decay=.16, waveform="triangle", glide_to=120), Tone(330, peak=.10, decay=.10))),
    "up3": ("三音上行", tuple(Tone(f, start=i * .13, peak=.16, decay=.45) for i, f in enumerate((523.25, 659.25, 783.99)))),
    "chord": ("和弦收尾", tuple(Tone(f, peak=.13, decay=1.05) for f in (392, 493.88, 587.33))),
    "soft": ("柔和低吟", (Tone(440, peak=.12, attack=.06, decay=.9), Tone(554.37, peak=.09, attack=.08, decay=1.0))),
}
DEFAULT_PRESETS = {"break_start": "chime", "long_start": "chime", "break_end": "wood",
                   "auto_start": "up3", "complete": "chord", "reminder": "soft"}


@cache
def synthesize(preset: str) -> bytes:
    """44.1 kHz、16 bit 单声道 WAV；所有音符先混合，再统一量化。"""
    tones = PRESETS[preset][1]
    samples = [0.0] * math.ceil((max(t.start + t.attack + t.decay for t in tones) + .012) * SAMPLE_RATE)
    for tone in tones:
        offset = round(tone.start * SAMPLE_RATE)
        duration = tone.attack + tone.decay
        phase = 0.0
        for index in range(math.ceil(duration * SAMPLE_RATE)):
            elapsed = index / SAMPLE_RATE
            gain = tone.peak * elapsed / tone.attack if elapsed < tone.attack else tone.peak * math.exp(
                math.log(.0001 / tone.peak) * (elapsed - tone.attack) / tone.decay)
            # 末尾淡出到零，避免停止振荡器时出现点击声。
            gain *= min(1, max(0, (duration - elapsed) / .008))
            frequency = tone.frequency
            if tone.glide_to is not None:
                frequency *= (tone.glide_to / tone.frequency) ** min(1, elapsed / tone.decay)
            if tone.waveform == "triangle":
                # 限制三角波谐波，减少高频毛刺和混叠。
                harmonics = min(15, int((SAMPLE_RATE / 2 - 1) / frequency))
                value = 8 / math.pi ** 2 * sum((-1) ** ((n - 1) // 2) * math.sin(n * phase) / n ** 2
                                               for n in range(1, harmonics + 1, 2))
            else:
                value = math.sin(phase)
            samples[offset + index] += gain * value
            phase = (phase + 2 * math.pi * frequency / SAMPLE_RATE) % (2 * math.pi)
    pcm = struct.pack(f"<{len(samples)}h", *(round(max(-1, min(1, value)) * 32767) for value in samples))
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    return output.getvalue()
