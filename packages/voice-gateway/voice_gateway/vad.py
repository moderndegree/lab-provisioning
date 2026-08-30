"""Silero VAD v5 over bare onnxruntime.

Two jobs, and they are the same computation used for opposite purposes:

  endpointing  in vad mode, decide when the user stopped talking
  barge-in     while we are speaking, decide that the user started again

Why the raw ONNX graph and not the `silero-vad` pip package: that package pulls
torch and torchaudio, roughly 2 GB of wheels, onto a box whose entire role here
is shuttling small buffers between sockets. The graph is 2.3 MB and
`ser5/ansible/roles/voice` fetches it with a checksum.

Graph signature, read off the model rather than assumed (v5.1.2):

    input   float32 [1, 512]      exactly 512 samples at 16 kHz — not negotiable
    state   float32 [2, 1, 128]   carried between calls; this is what makes it
                                  a sequence model rather than a per-frame one
    sr      int64   scalar
    ->
    output  float32 [1, 1]        speech probability
    stateN  float32 [2, 1, 128]

The 512-sample window is why this class buffers: the wire delivers 20 ms frames
(320 samples) and Silero wants 32 ms (512). Feeding it 320 does not error, it
just produces garbage probabilities, which is a much worse failure.
"""

from __future__ import annotations

import numpy as np

WINDOW_SAMPLES = 512
SAMPLE_RATE = 16_000
_STATE_SHAPE = (2, 1, 128)


class SileroVad:
    """Stateful speech detector. One instance per connection — the LSTM state is
    per-stream and sharing it across sessions would leak one speaker's context
    into another's endpointing."""

    def __init__(self, model_path: str, threshold: float = 0.5) -> None:
        # Imported here rather than at module import so that the protocol and
        # config modules stay importable (for tests and for the bench harness)
        # on a machine with no onnxruntime.
        import onnxruntime as ort

        opts = ort.SessionOptions()
        # One thread. This runs per 32 ms window on a box that is also running
        # Prometheus, Grafana, Open WebUI, SearXNG and Hermes; letting ORT spin
        # up a pool for a 2 MB graph costs more in contention than it saves.
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._session = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.threshold = threshold
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self._pending = np.zeros(0, dtype=np.float32)
        self.reset()

    def reset(self) -> None:
        """Drop the sequence state and any partial window.

        Called between utterances. Without it the model carries the tail of the
        previous turn into the next one and the first window or two of a new
        utterance are judged against stale context.
        """
        self._state = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)

    def probabilities(self, frame: bytes) -> list[float]:
        """Feed one wire frame of int16 PCM; get a probability per complete
        512-sample window it completed. Usually 0 or 1 values for a 20 ms frame.
        """
        if not frame:
            return []
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        self._pending = np.concatenate((self._pending, samples))

        out: list[float] = []
        while self._pending.shape[0] >= WINDOW_SAMPLES:
            window = self._pending[:WINDOW_SAMPLES]
            self._pending = self._pending[WINDOW_SAMPLES:]
            prob, self._state = self._session.run(
                ["output", "stateN"],
                {
                    "input": window.reshape(1, WINDOW_SAMPLES),
                    "state": self._state,
                    "sr": self._sr,
                },
            )
            out.append(float(prob[0][0]))
        return out

    def is_speech(self, frame: bytes) -> bool:
        """True if ANY window completed by this frame looked like speech.

        Deliberately optimistic: for barge-in a missed window is a truncated
        interruption the user has to repeat, which is more annoying than the
        occasional over-trigger that the min-duration gates upstream absorb.
        """
        return any(p >= self.threshold for p in self.probabilities(frame))


class Endpointer:
    """Turns a stream of per-frame speech decisions into utterance boundaries.

    The state machine is deliberately tiny and lives here rather than in
    session.py so the bench can drive it directly against a WAV file and get the
    same boundaries the live path would get.
    """

    def __init__(
        self,
        vad: SileroVad,
        *,
        frame_ms: int,
        min_speech_ms: int,
        silence_ms: int,
    ) -> None:
        self._vad = vad
        self._frame_ms = frame_ms
        self._min_speech_ms = min_speech_ms
        self._silence_ms = silence_ms
        self.reset()

    def reset(self) -> None:
        self._vad.reset()
        self._speech_ms = 0
        self._silence_run_ms = 0
        self.started = False

    def feed(self, frame: bytes) -> bool:
        """Feed one frame. Returns True exactly once, on the frame that ends the
        utterance. Returns False forever if speech never started — a silent
        stream must not produce an empty turn."""
        speech = self._vad.is_speech(frame)

        if speech:
            self._speech_ms += self._frame_ms
            self._silence_run_ms = 0
            if self._speech_ms >= self._min_speech_ms:
                self.started = True
            return False

        if not self.started:
            # Leading silence. Do not let it accumulate into an endpoint.
            return False

        self._silence_run_ms += self._frame_ms
        return self._silence_run_ms >= self._silence_ms
