#!/usr/bin/env python3
"""Neosapien mic test-jig runner (Raspberry Pi host).

Drives the DUT over BLE with AT commands, captures the PDM audio stream,
and applies deterministic pass/fail checks. Exit code is 0 on PASS, 1 on
FAIL and 2 on an infrastructure error, so a jig harness can branch on it.

Dependencies on 64-bit Raspberry Pi OS:
    sudo apt install python3-numpy
    pip install bleak
Optional speech check (slow on a Pi 3, only used with --speech):
    pip install faster-whisper
"""

import argparse
import asyncio
import os
import sys
import time
import wave

import numpy as np
from bleak import BleakClient, BleakScanner

DEVICE_NAME = "SPH0141_Audio"
AUDIO_CHARACTERISTIC_UUID = "12345678-1234-5678-1234-56789abcdef1"
CMD_CHARACTERISTIC_UUID = "12345678-1234-5678-1234-56789abcdef2"
RESPONSE_CHARACTERISTIC_UUID = "12345678-1234-5678-1234-56789abcdef3"

# Must match the AT command / response strings in src/main.c
CMD_ACTIVE = "AT+ACTIVE"
CMD_SLEEP = "AT+SLEEP"
CMD_CAPTURE = "AT+CAPTURE"
RESP_ACTIVE_DONE = "ACTIVE DONE"
RESP_SLEEP_DONE = "SLEEP DONE"
RESP_CAPTURE_DONE = "CAPTURE DONE"

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
FULL_SCALE = 32768.0

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_ERROR = 2


class Check:
    def __init__(self, name, passed, detail):
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        return f"[{'PASS' if self.passed else 'FAIL'}] {self.name:<10} {self.detail}"


def dbfs(value):
    return float("-inf") if value <= 0 else 20.0 * np.log10(value / FULL_SCALE)


def dominant_tone(ac):
    """Returns (dominant frequency in Hz, tone-to-noise ratio in dB)."""
    spectrum = np.abs(np.fft.rfft(ac * np.hanning(len(ac))))
    freqs = np.fft.rfftfreq(len(ac), 1.0 / SAMPLE_RATE)

    # Ignore rumble below 50 Hz so DC drift can't win the argmax.
    lo = int(np.searchsorted(freqs, 50.0))
    peak_idx = lo + int(np.argmax(spectrum[lo:]))

    power = spectrum ** 2
    band = slice(max(peak_idx - 3, lo), peak_idx + 4)
    tone_power = float(np.sum(power[band]))
    noise_power = max(float(np.sum(power[lo:])) - tone_power, 1e-12)

    return float(freqs[peak_idx]), 10.0 * np.log10(tone_power / noise_power)


def analyse(raw, expected_bytes, args, detail=""):
    """Turns a captured byte stream into pass/fail checks. Pure function.

    expected_bytes is how much audio should have arrived: real-time rate x
    duration when live streaming, or the size the DUT reported when using
    buffered capture.
    """
    checks = []

    received = len(raw)
    ratio = received / expected_bytes if expected_bytes > 0 else 0.0
    checks.append(Check(
        "stream", ratio >= args.min_stream,
        f"{received}/{int(expected_bytes)} B ({ratio * 100:.0f}%, "
        f"need {args.min_stream * 100:.0f}%){detail}"))

    if received < SAMPLE_RATE:  # under ~0.5 s of audio, nothing worth measuring
        checks.append(Check("signal", False,
                            "too little audio captured to analyse"))
        return checks

    samples = np.frombuffer(raw[:received // 2 * 2], dtype="<i2").astype(np.float64)
    dc = float(np.mean(samples))
    ac = samples - dc
    rms = float(np.sqrt(np.mean(ac ** 2)))
    peak = float(np.max(np.abs(ac)))
    clipped = int(np.count_nonzero(np.abs(samples) >= 32700))
    distinct = int(len(np.unique(samples)))

    # A dead or stuck PDM line reads as a constant, so variety is the tell.
    checks.append(Check(
        "alive", rms > 0 and distinct > 16,
        f"{distinct} distinct sample values, RMS {rms:.0f} counts"))

    checks.append(Check(
        "level", dbfs(rms) >= args.min_rms_dbfs,
        f"RMS {dbfs(rms):.1f} dBFS (need >= {args.min_rms_dbfs:.1f}), "
        f"peak {dbfs(peak):.1f} dBFS"))

    checks.append(Check(
        "clipping", clipped <= len(samples) * args.max_clip,
        f"{clipped} clipped samples ({clipped / len(samples) * 100:.2f}%, "
        f"limit {args.max_clip * 100:.2f}%)"))

    checks.append(Check(
        "dc-offset", abs(dc) <= args.max_dc,
        f"DC offset {dc:.0f} counts (limit {args.max_dc})"))

    if args.tone:
        freq, snr = dominant_tone(ac)
        on_freq = abs(freq - args.tone) <= args.tone_tol
        checks.append(Check(
            "tone", on_freq and snr >= args.min_tone_snr,
            f"dominant {freq:.0f} Hz (expected {args.tone}+/-{args.tone_tol}), "
            f"tone/noise {snr:.1f} dB (need >= {args.min_tone_snr})"))

    return checks


def save_wav(path, raw):
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(BYTES_PER_SAMPLE)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(raw)


async def transcribe_whisper_cpp(raw, cli_path, model_path, threads):
    """Runs whisper.cpp on the capture. Much faster than faster-whisper on ARM,
    and it needs a GGML model (models/ggml-tiny.en.bin), not a CTranslate2 one.
    """
    import tempfile

    handle, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(handle)
    try:
        save_wav(wav_path, raw)
        print(f"  running {os.path.basename(cli_path)} with {os.path.basename(model_path)}...")
        proc = await asyncio.create_subprocess_exec(
            cli_path, "--model", model_path, "--file", wav_path,
            "--threads", str(threads), "--no-timestamps",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            print(f"  whisper.cpp failed ({proc.returncode}): "
                  f"{stderr.decode(errors='replace').strip().splitlines()[-1:]}")
            return

        said = " ".join(stdout.decode(errors="replace").split())
        print(f"  transcript: {said or '(no speech detected)'}")
    finally:
        os.unlink(wav_path)


async def transcribe(raw, model_name):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("  faster-whisper not installed; skipping transcription")
        return

    loop = asyncio.get_running_loop()
    print(f"  loading Whisper model '{model_name}' (slow on a Pi 3)...")
    model = await loop.run_in_executor(
        None, lambda: WhisperModel(model_name, device="cpu", compute_type="int8"))

    audio = np.frombuffer(raw[:len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / FULL_SCALE
    # beam_size=1 keeps this tolerable on ARM; quality matters less than speed here.
    segments, _ = await loop.run_in_executor(
        None, lambda: model.transcribe(audio, beam_size=1, vad_filter=True))

    said = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    print(f"  transcript: {said or '(no speech detected)'}")


class TonePlayer:
    """Plays a sine tone out of the Pi's audio output for the duration of the
    recording, so the mic has something deterministic to hear. Without this
    the --tone check only passes if you trigger a tone by hand.
    """

    def __init__(self, freq, seconds, amplitude=0.35):
        self.freq = freq
        self.seconds = seconds
        self.amplitude = amplitude
        self.proc = None
        self.path = None

    async def __aenter__(self):
        import tempfile

        t = np.arange(int(SAMPLE_RATE * self.seconds)) / SAMPLE_RATE
        tone = (np.sin(2 * np.pi * self.freq * t) * self.amplitude * FULL_SCALE)
        handle, self.path = tempfile.mkstemp(suffix=".wav")
        os.close(handle)
        save_wav(self.path, tone.astype("<i2").tobytes())

        try:
            self.proc = await asyncio.create_subprocess_exec(
                "aplay", "-q", self.path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError:
            print("  WARNING: 'aplay' not found, cannot play the reference tone")
            return self

        print(f"  playing {self.freq:.0f} Hz reference tone")
        await asyncio.sleep(0.3)  # let playback actually start before recording
        return self

    async def __aexit__(self, *_exc):
        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self.proc.kill()
        if self.path:
            os.unlink(self.path)
        return False


async def start_audio_notify(client, handler):
    """Subscribes to the audio stream, preferring BlueZ's AcquireNotify.

    bleak defaults to StartNotify, which routes every notification through a
    D-Bus signal -- that is the throughput bottleneck on a Raspberry Pi.
    AcquireNotify hands back a file descriptor instead. bleak falls back to
    StartNotify on its own if the characteristic doesn't support it.
    """
    try:
        await client.start_notify(AUDIO_CHARACTERISTIC_UUID, handler,
                                  bluez={"use_start_notify": False})
    except Exception as exc:
        print(f"  AcquireNotify unavailable ({exc!r}); using StartNotify")
        await client.start_notify(AUDIO_CHARACTERISTIC_UUID, handler)


class DutSession:
    def __init__(self, client):
        self.client = client
        self.audio = bytearray()
        self.responses = asyncio.Queue()
        self.capturing = False
        self.packets = 0
        self.max_packet = 0

    def _on_response(self, _sender, data):
        self.responses.put_nowait(bytes(data).decode("ascii", errors="replace").strip())

    def _on_audio(self, _sender, data):
        if self.capturing:
            self.audio.extend(data)
            # Actual packet size is the honest read on the negotiated MTU.
            self.packets += 1
            self.max_packet = max(self.max_packet, len(data))

    async def start(self):
        await self.client.start_notify(RESPONSE_CHARACTERISTIC_UUID, self._on_response)
        await start_audio_notify(self.client, self._on_audio)

    async def _send(self, cmd):
        while not self.responses.empty():
            self.responses.get_nowait()
        await self.client.write_gatt_char(
            CMD_CHARACTERISTIC_UUID, cmd.encode("ascii"), response=True)

    async def _wait_for(self, matches, timeout, what):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no '{what}' from DUT")
            response = await asyncio.wait_for(self.responses.get(), timeout=remaining)
            if response == "ERROR":
                raise RuntimeError(f"DUT reported ERROR while waiting for '{what}'")
            if matches(response):
                return response

    async def command(self, cmd, expect, timeout=5.0):
        await self._send(cmd)
        await self._wait_for(lambda r: r == expect, timeout, expect)

    async def capture_buffered(self, duration_ms, timeout):
        """Records into DUT RAM, then receives the whole thing with no
        dropping. Slow links only make the transfer take longer.
        """
        self.audio = bytearray()
        self.packets = 0
        self.max_packet = 0
        self.capturing = True
        start = time.monotonic()
        try:
            await self._send(f"{CMD_CAPTURE}={int(duration_ms)}")
            header = await self._wait_for(
                lambda r: r.startswith("CAPTURE ") and r != RESP_CAPTURE_DONE,
                timeout, "CAPTURE <bytes>")
            expected = int(header.split()[1])
            print(f"  DUT recorded {expected} B, transferring...")

            deadline = time.monotonic() + timeout
            previous = 0
            while True:
                try:
                    response = await asyncio.wait_for(self.responses.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    response = None

                if response == RESP_CAPTURE_DONE:
                    break
                if response == "ERROR":
                    raise RuntimeError("DUT reported ERROR during transfer")
                if time.monotonic() > deadline:
                    raise TimeoutError("capture transfer did not finish")

                total = len(self.audio)
                print(f"  transferring... {total}/{expected} B "
                      f"({(total - previous) / 1000:.1f} kB/s)")
                previous = total
        finally:
            self.capturing = False

        return bytes(self.audio), expected, time.monotonic() - start

    async def capture(self, seconds):
        self.audio = bytearray()
        self.capturing = True
        start = time.monotonic()
        previous = 0
        try:
            while True:
                elapsed = time.monotonic() - start
                if elapsed >= seconds:
                    break
                await asyncio.sleep(min(1.0, seconds - elapsed))
                total = len(self.audio)
                print(f"  capturing... {total / 1000:6.1f} kB "
                      f"({(total - previous) / 1000:5.1f} kB/s)")
                previous = total
        finally:
            self.capturing = False
        return bytes(self.audio), time.monotonic() - start


async def run(args):
    address = args.address
    if not address:
        print(f"Scanning for '{args.name}'...")
        device = await BleakScanner.find_device_by_name(args.name, timeout=args.scan_timeout)
        if device is None:
            print(f"ERROR: no device advertising as '{args.name}' found")
            return EXIT_ERROR
        address = device.address

    print(f"Connecting to {address}...")
    async with BleakClient(address, timeout=args.connect_timeout) as client:
        if not client.is_connected:
            print("ERROR: failed to connect")
            return EXIT_ERROR

        # On the BlueZ backend mtu_size is a placeholder until the MTU is
        # actually acquired, so ask for it rather than reporting a fake 23.
        acquire = getattr(client, "_acquire_mtu", None)
        if acquire is not None:
            try:
                await acquire()
            except Exception:
                pass
        try:
            mtu = client.mtu_size
            print(f"Connected. ATT MTU {mtu} ({mtu - 3} B per notification)")
            if mtu < 100:
                print("  NOTE: small MTU caps throughput; the stream check may fail")
        except Exception as exc:  # backend-dependent, never fatal
            print(f"Connected. ATT MTU unavailable ({exc!r})")

        session = DutSession(client)
        await session.start()

        raw, expected_bytes, detail = b"", 0.0, ""
        try:
            if args.capture_ms:
                # Buffered: the DUT records first, so a slow link costs time
                # rather than samples, and the audio comes back contiguous.
                for remaining in range(args.countdown, 0, -1):
                    print(f"  recording in {remaining}...")
                    await asyncio.sleep(1.0)
                print(f"Recording {args.capture_ms} ms into DUT RAM...")
                # If you're checking for a tone, play one by default.
                tone_hz = None if args.no_play_tone else (args.play_tone or args.tone)
                if tone_hz:
                    async with TonePlayer(tone_hz, args.capture_ms / 1000.0 + 1.0):
                        raw, expected_bytes, elapsed = await session.capture_buffered(
                            args.capture_ms, args.transfer_timeout)
                else:
                    raw, expected_bytes, elapsed = await session.capture_buffered(
                        args.capture_ms, args.transfer_timeout)
                detail = f" -- {elapsed:.0f}s transfer"
            else:
                await session.command(CMD_ACTIVE, RESP_ACTIVE_DONE, timeout=args.ack_timeout)
                print(f"DUT active; capturing {args.seconds:.0f}s...")
                raw, elapsed = await session.capture(args.seconds)
                expected_bytes = SAMPLE_RATE * BYTES_PER_SAMPLE * elapsed
                detail = f" -- {len(raw) / elapsed / 1000:.1f} kB/s over {elapsed:.1f}s"
            if session.packets:
                print(f"  link: {session.packets / max(elapsed, 1e-6):.0f} pkt/s, "
                      f"max {session.max_packet} B/pkt "
                      f"(20 B/pkt means the MTU is stuck at 23)")
        finally:
            # Always leave the DUT in the low-power state, even on failure.
            # Stop the audio stream first, or the ack for AT+SLEEP queues up
            # behind a backlog of audio notifications and times out.
            try:
                await client.stop_notify(AUDIO_CHARACTERISTIC_UUID)
            except Exception:
                pass
            try:
                await session.command(CMD_SLEEP, RESP_SLEEP_DONE, timeout=args.ack_timeout)
            except Exception as exc:
                print(f"WARNING: could not return DUT to sleep ({exc!r})")

    if args.wav:
        save_wav(args.wav, raw)
        print(f"Saved {args.wav}")

    print()
    checks = analyse(raw, expected_bytes, args, detail)
    for check in checks:
        print(check)

    if args.speech:
        print("\nSpeech check (informational, not part of pass/fail):")
        if args.whisper_cli:
            await transcribe_whisper_cpp(raw, args.whisper_cli, args.model, args.threads)
        else:
            await transcribe(raw, args.model)

    failed = [c.name for c in checks if not c.passed]
    print()
    if failed:
        print(f"RESULT: FAIL ({len(failed)} of {len(checks)} checks failed: {', '.join(failed)})")
        return EXIT_FAIL
    print(f"RESULT: PASS (all {len(checks)} checks)")
    return EXIT_PASS


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Neosapien mic test-jig runner")
    parser.add_argument("--address", help="DUT MAC address (skips scanning)")
    parser.add_argument("--name", default=DEVICE_NAME, help="advertised name to scan for")
    parser.add_argument("--seconds", type=float, default=5.0,
                        help="live-stream capture duration")
    parser.add_argument("--capture-ms", type=int, default=0,
                        help="use buffered capture instead of live streaming: the DUT "
                             "records this many ms to RAM (max 4000), then transfers it "
                             "losslessly (recommended on slow links; needed for --tone)")
    parser.add_argument("--countdown", type=int, default=0,
                        help="seconds to count down before recording starts, so you have "
                             "time to start speaking")
    parser.add_argument("--transfer-timeout", type=float, default=180.0,
                        help="how long a buffered transfer may take")
    parser.add_argument("--wav", help="also write the captured audio to this .wav")
    parser.add_argument("--speech", action="store_true",
                        help="also run Whisper transcription (slow on a Pi 3)")
    parser.add_argument("--model", default=os.environ.get("WHISPER_MODEL", "tiny.en"),
                        help="faster-whisper model name/directory, or the path to a "
                             "GGML .bin when --whisper-cli is used")
    parser.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI"),
                        help="path to whisper.cpp's whisper-cli; much faster than "
                             "faster-whisper on a Pi (needs a GGML model via --model)")
    parser.add_argument("--threads", type=int, default=4,
                        help="threads for whisper.cpp")
    parser.add_argument("--scan-timeout", type=float, default=15.0)
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    parser.add_argument("--ack-timeout", type=float, default=10.0,
                        help="how long to wait for an AT command ack")

    limits = parser.add_argument_group("pass/fail limits")
    limits.add_argument("--min-stream", type=float, default=0.80,
                        help="minimum fraction of real-time audio that must arrive")
    limits.add_argument("--min-rms-dbfs", type=float, default=-60.0,
                        help="minimum RMS level in dBFS")
    limits.add_argument("--max-clip", type=float, default=0.01,
                        help="maximum fraction of clipped samples")
    limits.add_argument("--max-dc", type=float, default=2000.0,
                        help="maximum DC offset in counts")
    limits.add_argument("--tone", type=float,
                        help="expected test-tone frequency in Hz (enables the tone check)")
    limits.add_argument("--play-tone", type=float, default=None,
                        help="frequency to play out of the Pi during a buffered capture "
                             "(defaults to --tone)")
    limits.add_argument("--no-play-tone", action="store_true",
                        help="never play a tone; use an external tone source instead")
    limits.add_argument("--tone-tol", type=float, default=50.0,
                        help="tone frequency tolerance in Hz")
    limits.add_argument("--min-tone-snr", type=float, default=10.0,
                        help="minimum tone-to-noise ratio in dB")
    return parser.parse_args(argv)


def main():
    args = parse_args(sys.argv[1:])
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nAborted.")
        return EXIT_ERROR
    except Exception as exc:
        print(f"ERROR: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
