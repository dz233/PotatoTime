"""验证提示音是可播放、无削波且有平滑首尾的 PCM WAV。"""

from io import BytesIO
import math
from pathlib import Path
import struct
import sys
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tomatotime.synthesis import PRESETS, SAMPLE_RATE, synthesize


class SynthesisTests(unittest.TestCase):
    def test_presets_are_short_valid_sounds_without_clipping_or_end_clicks(self):
        for name in PRESETS:
            with self.subTest(name=name), wave.open(BytesIO(synthesize(name)), "rb") as wav:
                self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, SAMPLE_RATE))
                self.assertGreater(wav.getnframes() / SAMPLE_RATE, .16)
                self.assertLess(wav.getnframes() / SAMPLE_RATE, 1.2)
                pcm = struct.unpack(f"<{wav.getnframes()}h", wav.readframes(wav.getnframes()))
                self.assertEqual(pcm[0], 0)
                self.assertTrue(all(value == 0 for value in pcm[-200:]))
                self.assertLess(max(abs(value) for value in pcm), 32767)
                self.assertGreater(math.sqrt(sum(value * value for value in pcm) / len(pcm)), 200)

    def test_chime_contains_requested_two_pitches_in_order(self):
        with wave.open(BytesIO(synthesize("chime")), "rb") as wav:
            pcm = struct.unpack(f"<{wav.getnframes()}h", wav.readframes(wav.getnframes()))
        def strength(frequency, start, duration=.08):
            part = pcm[round(start * SAMPLE_RATE):round((start + duration) * SAMPLE_RATE)]
            return abs(sum(value * complex(math.cos(2 * math.pi * frequency * i / SAMPLE_RATE),
                                           math.sin(2 * math.pi * frequency * i / SAMPLE_RATE)) for i, value in enumerate(part)))
        self.assertGreater(strength(880, .015), 20 * strength(1318.5, .015))
        self.assertGreater(strength(1318.5, .2), 3 * strength(880, .2))


if __name__ == "__main__":
    unittest.main()
