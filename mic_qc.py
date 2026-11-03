#!/usr/bin/env python3
"""Production mic QC for the Neosapien board: golden-unit calibration, then
data-derived pass/fail limits.

Nothing here modifies mic_test.py -- the BLE session and capture logic are
imported from it.

Two modes:
    calibrate  measure N known-good boards, write limits.json
    test       measure one board, compare against limits.json (default)

It plays a single multi-tone stimulus (several sines at once), so one capture
measures the whole frequency response instead of one capture per tone. A
second, silent capture gives the noise floor. Two captures per board.

Speaker hardware (MAX98357A I2S amp on a Raspberry Pi):
    # /boot/firmware/config.txt
    dtparam=audio=off
    dtoverlay=hifiberry-dac
    # reboot, then find the card:
    aplay -l
    # and pass it here, e.g.:
    ./mic_qc.py --alsa-device plughw:CARD=sndrpihifiberry,DEV=0 ...

Wiring: BCLK->GPIO18, LRCLK/WS->GPIO19, DIN->GPIO21, plus VIN/GND.

Usage:
    ./mic_qc.py --calibrate --runs 10 --limits limits.json
    ./mic_qc.py --limits limits.json
"""

import argparse
import asyncio
import inspect
import json
import os
import statistics
import sys
import tempfile
import time

import numpy as np
from bleak import BleakClient, BleakScanner

from mic_test import (
    AUDIO_CHARACTERISTIC_UUID,
    BYTES_PER_SAMPLE,
    DEVICE_NAME,
    EXIT_ERROR,
    EXIT_FAIL,
    EXIT_PASS,
    FULL_SCALE,
    SAMPLE_RATE,
    DutSession,
    dbfs,
    save_wav,
)

DEFAULT_TONES = [300.0, 1000.0, 3000.0]
HARMONICS = (2, 3, 4, 5)
TONE_BANDWIDTH_HZ = 40.0

# Metrics where only an upper bound matters (lower is unambiguously better).
UPPER_BOUND_ONLY = ("thd_pct", "noise_dbfs", "clipped_pct")


class WavePlayer:
    """Plays an arbitrary waveform out of an ALSA device (the MAX98357A)."""

    def __init__(self, samples, device=None, lead=0.5):
        self.samples = samples
        self.device = device
        self.lead = lead
        self.proc = None
        self.path = None

    async def __aenter__(self):
        handle, self.path = tempfile.mkstemp(suffix=".wav")
        os.close(handle)
        save_wav(self.path, self.samples.astype("<i2").tobytes())

        cmd = ["aplay", "-q"]
        if self.device:
            cmd += ["-D", self.device]
        cmd.append(self.path)

        try:
            self.proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError:
            print("  WARNING: 'aplay' not found; no stimulus will be played")
            return self

        # ALSA needs a moment to actually start pushing samples, so give the
        # speaker a head start before the DUT begins recording.
        await asyncio.sleep(self.lead)

        # aplay fails fast when the device is missing, busy or misnamed. Left
        # unchecked the capture proceeds in silence, every tone reads at the
        # noise floor, and the advice that follows is "raise --amplitude" --
        # exactly backwards. A fixture fault must never be reportable as a
        # verdict on the board.
        if self.proc.returncode not in (None, 0):
            message = (await self.proc.stderr.read()).decode(errors="replace").strip()
            raise RuntimeError(
                "aplay exited {0} before the capture began, so no stimulus was "
                "played: {1}".format(self.proc.returncode, message or "no message"))
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


TONE_MS = 150
GAP_MS = 50
# Raised-cosine edge length. 5 ms is a hard beep; 25 ms is a chime. Only the
# flat top between the edges is measured, so this is free until it starts
# eating into FRAME_MS -- make_sequence() clamps it there.
TONE_RAMP_MS = 25.0
FRAME_MS = 120
HOP_MS = 15


class AmpSelect:
    """Enables one MAX98357A through its SD (shutdown) pin.

    Several amps can share one I2S bus; SD decides which of them actually
    makes sound, so only the position under test is audible. SD low = muted.
    Stays enabled across both captures so the amp's own hiss is included in
    the noise floor, exactly as it is during the real measurement.
    """

    def __init__(self, gpio, active_low=False):
        self.gpio = gpio
        self.active_low = active_low
        self.device = None

    def __enter__(self):
        if self.gpio is None:
            return self
        try:
            from gpiozero import OutputDevice
        except ImportError:
            print("  WARNING: gpiozero not installed, cannot drive SD "
                  "(sudo apt install python3-gpiozero)")
            return self

        self.device = OutputDevice(self.gpio, active_high=not self.active_low,
                                   initial_value=True)
        return self

    def __exit__(self, *_exc):
        if self.device is not None:
            self.device.off()
            self.device.close()
        return False


def bluez_kwargs(adapter):
    """Adapter selection in whichever form this bleak accepts.

    bleak moved from `adapter="hci1"` to `bluez={"adapter": "hci1"}` and warns
    on the old spelling. Probing the signature rather than the version keeps
    both working, which matters because the jig and the laptop rarely have the
    same bleak installed.
    """
    if not adapter:
        return {}
    try:
        if "bluez" in inspect.signature(BleakClient.__init__).parameters:
            return {"bluez": {"adapter": adapter}}
    except (TypeError, ValueError):
        pass
    return {"adapter": adapter}


async def read_mtu(client):
    """Negotiated ATT MTU, or None.

    client.mtu_size is a placeholder on BlueZ until the MTU is acquired, and
    the private method that does it has moved between bleak versions. When
    none of them work the honest number is the largest notification actually
    seen, so callers fall back to that rather than reporting a fictional 23.
    """
    for owner in (client, getattr(client, "_backend", None)):
        acquire = getattr(owner, "_acquire_mtu", None)
        if acquire is None:
            continue
        try:
            await acquire()
            break
        except Exception:                               # noqa: BLE001
            continue
    try:
        size = client.mtu_size
    except Exception:                                   # noqa: BLE001
        return None
    return size if size and size > 23 else None


def parse_tones(value):
    """Frequencies from either "1000,3000,6000" or ["1000", "3000", "6000"].

    Both spellings are accepted everywhere, because the two scripts used to
    disagree about which one they wanted and a silently mis-parsed tone list
    produces limits for the wrong stimulus.
    """
    if isinstance(value, str):
        value = [value]
    tones = []
    for item in value:
        tones.extend(float(part) for part in str(item).replace(",", " ").split())
    if not tones:
        raise ValueError("no tone frequencies given")
    return tones


def gains_for(freqs, gains):
    """One multiplier per tone, defaulting to no shaping at all."""
    if gains is None:
        return [1.0] * len(freqs)
    if len(gains) != len(freqs):
        raise ValueError("--tone-gains needs one value per tone: "
                         "{0} tone(s), {1} gain(s)".format(len(freqs), len(gains)))
    return [float(g) for g in gains]


def make_multitone(freqs, seconds, amplitude, gains=None):
    """Sines summed together, scaled to stay clear of clipping.

    A small speaker's output can vary by 20 dB or more across the band, and a
    single amplitude then has to choose between clipping the loud tone and
    burying the quiet one. `gains` pre-shapes the stimulus so every tone lands
    at a usable level; see suggest_gains(), which works them out from a
    measurement.
    """
    gains = gains_for(freqs, gains)
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    wave = sum(g * np.sin(2 * np.pi * f * t) for f, g in zip(freqs, gains))
    return wave / len(freqs) * amplitude * FULL_SCALE


def sequence_timing(capture_ms, n_tones):
    """Beep/gap length that guarantees the recording window holds a complete
    instance of every tone, whenever it happens to start.

    The window must fit a whole cycle plus one more tone: with gap = tone/3
    that is tone * (4n/3 + 1) <= capture.
    """
    tone_ms = capture_ms / (4.0 * n_tones / 3.0 + 1.0)
    return tone_ms, tone_ms / 3.0


def make_sequence(freqs, seconds, amplitude, tone_ms=TONE_MS, gap_ms=GAP_MS,
                  gains=None, ramp_ms=TONE_RAMP_MS, descending=True, grouped=True):
    """Tones one at a time, repeating: an audible arpeggio.

    Each tone gets the full amplitude (better SNR than summing them), and the
    pattern repeats for the whole stimulus so the DUT's recording window is
    guaranteed to contain every tone regardless of when it starts recording.

    `gains` scales each tone individually, to compensate a speaker that is far
    louder at one frequency than another.

    Three things soften how it sounds to somebody sitting next to it all day,
    none of which move a single measured number:

    `ramp_ms`   longer raised-cosine edges. A 5 ms edge is a hard "beep"; 25 ms
                is closer to a chime. Clamped below so the flat top stays at
                least FRAME_MS long -- the analysis measures level on that flat
                part, and a tone that is still rising under the frame would
                read quieter and vary run to run.
    `descending` play high to low. A falling phrase reads as a chime, a rising
                one as an alarm. The analysis finds each tone independently, so
                the order is free.
    `grouped`   short gaps between tones and a longer one before the phrase
                repeats, using the same total gap time.

    What it cannot have is silence. The DUT starts recording whenever it likes,
    and the timing guarantee is that any capture window holds a complete
    instance of every tone -- so a rest long enough to be restful is also long
    enough to hide a tone and fail a good board.
    """
    gains = gains_for(freqs, gains)
    order = sorted(range(len(freqs)), key=lambda i: freqs[i], reverse=descending)

    tone_len = int(SAMPLE_RATE * tone_ms / 1000)
    t = np.arange(tone_len) / SAMPLE_RATE

    # Never let the edges eat into the flat region the analysis needs, however
    # long the caller asked for or however short the capture made the tones.
    frame_len = int(SAMPLE_RATE * FRAME_MS / 1000)
    max_ramp = max((tone_len - frame_len) // 2, 1)
    ramp_len = int(np.clip(int(SAMPLE_RATE * ramp_ms / 1000), 1, max_ramp))

    envelope = np.ones(tone_len)
    ramp = 0.5 * (1 - np.cos(np.pi * np.arange(ramp_len) / ramp_len))
    envelope[:ramp_len] = ramp
    envelope[-ramp_len:] = ramp[::-1]

    # Same total gap time either way, because the timing guarantee depends on
    # the cycle length and nothing else.
    total_gap = len(freqs) * int(SAMPLE_RATE * gap_ms / 1000)
    if grouped and len(freqs) > 1:
        short = int(total_gap * 0.25 / (len(freqs) - 1))
        gaps = [short] * (len(freqs) - 1) + [total_gap - short * (len(freqs) - 1)]
    else:
        gaps = [total_gap // len(freqs)] * len(freqs)

    cycle = np.concatenate([
        np.concatenate([gains[i] * np.sin(2 * np.pi * freqs[i] * t) * envelope,
                        np.zeros(gaps[slot])])
        for slot, i in enumerate(order)
    ])

    repeats = int(np.ceil(SAMPLE_RATE * seconds / len(cycle)))
    return np.tile(cycle, repeats)[:int(SAMPLE_RATE * seconds)] * amplitude * FULL_SCALE


def best_frame_for(samples, tone, frame_len, hop):
    """Loudest short frame for a tone, so sequential tones need no time
    alignment between playback and the DUT's recording window.
    """
    window = np.hanning(frame_len)
    scale = 2.0 / np.sum(window)
    freqs = np.fft.rfftfreq(frame_len, 1.0 / SAMPLE_RATE)

    best_level, best_amps = 0.0, None
    for start in range(0, max(len(samples) - frame_len, 0) + 1, hop):
        segment = samples[start:start + frame_len]
        segment = segment - segment.mean()
        amps = np.abs(np.fft.rfft(segment * window)) * scale
        level = level_at(freqs, amps, tone)
        if level > best_level:
            best_level, best_amps = level, amps

    return best_level, freqs, best_amps


def build_stimulus(kind, freqs, seconds, amplitude, capture_ms=1000, gains=None,
                   ramp_ms=TONE_RAMP_MS, descending=True, grouped=True):
    if kind == "sequence":
        tone_ms, gap_ms = sequence_timing(capture_ms, len(freqs))
        return make_sequence(freqs, seconds, amplitude, tone_ms, gap_ms, gains,
                             ramp_ms, descending, grouped)
    return make_multitone(freqs, seconds, amplitude, gains)


# What a well-placed tone should land at: loud enough for a wide SNR, with
# plenty of room left before clipping.
FLATTEN_TARGET_DBFS = -12.0
# Never ask the DAC for the last 10%; the amplifier distorts near full scale.
DAC_HEADROOM = 0.9


def suggest_gains(tones, metrics, amplitude, gains=None,
                  target=FLATTEN_TARGET_DBFS, headroom=DAC_HEADROOM,
                  max_amplitude=None):
    """Per-tone gains that would bring every tone to the same captured level.

    Returns (amplitude, gains, achieved_dbfs), or None if a tone was not
    measurable. Flattening only ever attenuates: the loud tones come down to
    meet the quiet one.

    It never raises the drive above `max_amplitude`, which defaults to the
    amplitude the measurement was taken at, because nothing in that
    measurement says the speaker stays linear above it. On this fixture it
    emphatically does not -- pushing 1 kHz from 0.5 to 0.9 amplitude made the
    captured level fall 11 dB while THD went from 6% to 81%, the fundamental's
    energy having gone into harmonics. Raise the ceiling explicitly, and
    re-measure, if you want to explore above the known-good level.
    """
    gains = gains_for(tones, gains)
    ceiling = min(headroom, amplitude if max_amplitude is None else max_amplitude)

    peaks = []
    for tone, gain in zip(tones, gains):
        level = metrics.get("level_{0}hz_dbfs".format(int(tone)))
        if level is None or not np.isfinite(level):
            return None
        peaks.append(amplitude * gain * 10.0 ** ((target - level) / 20.0))

    loudest = max(peaks)
    if loudest <= 0:
        return None

    scale = min(1.0, ceiling / loudest)
    return loudest * scale, [p / loudest for p in peaks], target + 20.0 * np.log10(scale)


def spectrum(raw):
    """Amplitude spectrum of a capture, corrected for the analysis window."""
    samples = np.frombuffer(raw[:len(raw) // 2 * 2], dtype="<i2").astype(np.float64)
    ac = samples - samples.mean()
    window = np.hanning(len(ac))
    amps = np.abs(np.fft.rfft(ac * window)) * 2.0 / np.sum(window)
    freqs = np.fft.rfftfreq(len(ac), 1.0 / SAMPLE_RATE)
    return samples, freqs, amps


def level_at(freqs, amps, target, bandwidth=TONE_BANDWIDTH_HZ):
    """RMS amplitude in a narrow band around a frequency, in counts."""
    if target >= SAMPLE_RATE / 2:
        return 0.0
    band = (freqs >= target - bandwidth) & (freqs <= target + bandwidth)
    if not band.any():
        return 0.0
    return float(np.sqrt(np.sum(amps[band] ** 2) / 2.0))


def inband_noise(samples, tone, frame_len, hop, bandwidth=TONE_BANDWIDTH_HZ):
    """Average noise amplitude inside a tone's measurement band.

    This is the noise that actually competes with the tone. Wideband room
    noise is spread over the whole spectrum, so only the sliver inside the
    band matters -- which is why a loud room is far less damaging to a
    narrowband measurement than the overall noise floor suggests.
    """
    window = np.hanning(frame_len)
    scale = 2.0 / np.sum(window)
    freqs = np.fft.rfftfreq(frame_len, 1.0 / SAMPLE_RATE)

    powers = []
    for start in range(0, max(len(samples) - frame_len, 0) + 1, hop):
        segment = samples[start:start + frame_len]
        segment = segment - segment.mean()
        amps = np.abs(np.fft.rfft(segment * window)) * scale
        powers.append(level_at(freqs, amps, tone, bandwidth) ** 2)

    return float(np.sqrt(np.mean(powers))) if powers else 0.0


def measure(stimulus_raw, silence_raw, tones, stimulus="multitone",
            bandwidth=TONE_BANDWIDTH_HZ):
    """Reduces the two captures to the numbers a limits file is built from."""
    metrics = {}
    samples, freqs, amps = spectrum(stimulus_raw)
    silence, _, _ = spectrum(silence_raw)
    primary = min(tones)

    if stimulus == "sequence":
        # Tones arrive one at a time, so measure each in its own loudest frame.
        # Derive the analysis frame from the same timing the stimulus used,
        # so a frame always fits inside one beep.
        capture_ms = 1000.0 * len(samples) / SAMPLE_RATE
        tone_ms, _ = sequence_timing(capture_ms, len(tones))
        frame_len = min(int(SAMPLE_RATE * min(FRAME_MS, tone_ms * 0.7) / 1000),
                        len(samples))
        frame_len = max(frame_len, 64)
        hop = max(int(SAMPLE_RATE * HOP_MS / 1000), 1)
        raw_levels = {t: best_frame_for(samples, t, frame_len, hop)[0] for t in tones}
        fundamental, freqs, amps = best_frame_for(samples, primary, frame_len, hop)
    else:
        frame_len = len(samples)
        hop = max(frame_len, 1)
        raw_levels = {t: level_at(freqs, amps, t, bandwidth) for t in tones}
        fundamental = raw_levels[primary]

    for tone in tones:
        noise = inband_noise(silence, tone, min(frame_len, len(silence)), hop, bandwidth)
        # Ambient inside the band biases the tone level upward, so take it out.
        corrected = np.sqrt(max(raw_levels[tone] ** 2 - noise ** 2, 0.0))
        metrics[f"level_{int(tone)}hz_dbfs"] = dbfs(corrected)
        # The SNR that actually governs whether this tone is measurable.
        metrics[f"snr_{int(tone)}hz_db"] = dbfs(raw_levels[tone]) - dbfs(noise)

    # THD against the primary (lowest) tone. Skip any harmonic that lands on
    # another stimulus tone, or that tone's own energy would be counted as
    # distortion -- 3 kHz is the 3rd harmonic of 1 kHz, for instance.
    usable = [n for n in HARMONICS
              if primary * n < SAMPLE_RATE / 2
              and not any(abs(primary * n - other) < bandwidth
                          for other in tones if other != primary)]
    harmonic_power = sum(level_at(freqs, amps, primary * n, bandwidth) ** 2
                         for n in usable)
    metrics["thd_harmonics_used"] = float(len(usable))
    metrics["thd_pct"] = (100.0 * np.sqrt(harmonic_power) / fundamental
                          if fundamental > 0 and amps is not None else float("inf"))

    metrics["dc_counts"] = float(np.mean(samples))
    # Direct evidence rather than inference. A tone level near 0 dBFS only
    # suggests clipping, and a capture can equally clip on ambient low-frequency
    # energy while every tone still reads low -- which a high noise_dbfs with
    # healthy per-tone SNR is exactly the signature of.
    metrics["clipped_pct"] = 100.0 * float(np.mean(np.abs(samples) >= 32700))

    silence, _, _ = spectrum(silence_raw)
    silence_rms = float(np.sqrt(np.mean((silence - silence.mean()) ** 2)))
    metrics["noise_dbfs"] = dbfs(silence_rms)

    # How much louder the stimulus capture is than the silence capture,
    # broadband. Near zero means nothing reached the microphone at all -- a
    # dead amplifier, a muted SD pin, the wrong ALSA device, aplay not running
    # -- which is a fault in the fixture, not in the board under test. Separate
    # metric because per-tone SNR cannot tell the two apart: a deaf microphone
    # and a silent speaker look identical.
    stimulus_rms = float(np.sqrt(np.mean((samples - samples.mean()) ** 2)))
    metrics["stimulus_delta_db"] = dbfs(stimulus_rms) - dbfs(silence_rms)

    return metrics


async def capture_once(session, capture_ms, transfer_timeout, stimulus, device, lead=0.5):
    if stimulus is None:
        raw, expected, _ = await session.capture_buffered(capture_ms, transfer_timeout)
    else:
        async with WavePlayer(stimulus, device, lead):
            raw, expected, _ = await session.capture_buffered(capture_ms, transfer_timeout)

    if len(raw) < expected:
        raise RuntimeError(f"incomplete capture: {len(raw)}/{expected} B")
    return raw


async def measure_board(args, tones):
    """Connects, takes the stimulus + silence captures, returns the metrics."""
    address = args.address
    if not address:
        scan_kwargs = bluez_kwargs(args.adapter)
        device = await BleakScanner.find_device_by_name(
            args.name, timeout=args.scan_timeout, **scan_kwargs)
        if device is None:
            raise RuntimeError(f"no device advertising as '{args.name}'")
        address = device.address

    stimulus = build_stimulus(args.stimulus, tones, args.capture_ms / 1000.0
                              + args.stimulus_lead + 1.0, args.amplitude,
                              args.capture_ms, args.tone_gains,
                              args.tone_ramp_ms, args.descending, args.grouped)

    client_kwargs = bluez_kwargs(args.adapter)
    async with BleakClient(address, timeout=args.connect_timeout,
                           **client_kwargs) as client:
        if not client.is_connected:
            raise RuntimeError("failed to connect")

        session = DutSession(client)
        await session.start()
        try:
            with AmpSelect(args.sd_gpio, args.sd_active_low):
                print("  capturing with stimulus...")
                stimulus_raw = await capture_once(
                    session, args.capture_ms, args.transfer_timeout, stimulus,
                    args.alsa_device, args.stimulus_lead)

                print("  capturing silence (noise floor)...")
                silence_raw = await capture_once(
                    session, args.capture_ms, args.transfer_timeout, None,
                    args.alsa_device)
        finally:
            try:
                await client.stop_notify(AUDIO_CHARACTERISTIC_UUID)
            except Exception:
                pass

    if args.wav:
        save_wav(args.wav, stimulus_raw)
        print(f"  saved {args.wav}")

    return measure(stimulus_raw, silence_raw, tones, args.stimulus,
                   args.tone_bandwidth)


def stimulus_context(args, tones):
    """Everything about the stimulus that a limits file is only valid for.

    A limits file is a statement about one stimulus, played through one
    speaker, into one fixture. Applied to a different stimulus it is not
    merely less accurate -- it is meaningless, and it fails silently, which is
    the worst way for a production test to be wrong.
    """
    return {
        "tones_hz": list(tones),
        "capture_ms": args.capture_ms,
        "stimulus": args.stimulus,
        "amplitude": args.amplitude,
        "tone_gains": list(args.tone_gains) if args.tone_gains else None,
    }


def context_mismatch(limits, context):
    """Stimulus settings that differ from the ones the limits were built with.

    Settings a limits file predates are skipped rather than reported, so an
    older file still works; only a recorded value that actually disagrees is
    an error.
    """
    differences = []
    for key, now in context.items():
        if key not in limits:
            continue
        was = limits[key]
        if key == "tones_hz":
            same = [float(f) for f in was] == [float(f) for f in now]
        elif isinstance(now, float) and isinstance(was, (int, float)):
            same = abs(float(now) - float(was)) < 1e-9
        else:
            same = now == was
        if not same:
            differences.append("{0}: calibrated with {1!r}, this run uses {2!r}".format(
                key, was, now))
    return differences


def build_limits(runs, tones, sigma_k, min_sigma, capture_ms, context=None):
    limits = {}
    for key in runs[0]:
        values = [float(run[key]) for run in runs]
        mean = statistics.fmean(values)
        
        # Calculate standard deviation manually to prevent Python fractions module crash
        if len(values) > 1:
            variance = sum((x - mean) ** 2 for x in values) / len(values)
            raw_sigma = variance ** 0.5
        else:
            raw_sigma = 0.0
            
        sigma = max(raw_sigma, min_sigma)
        entry = {"mean": mean, "sigma": sigma, "hi": mean + sigma_k * sigma}
        if key not in UPPER_BOUND_ONLY:
            entry["lo"] = mean - sigma_k * sigma
        limits[key] = entry

    built = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tones_hz": tones,
        "capture_ms": capture_ms,
        "runs": len(runs),
        "sigma_k": sigma_k,
        "metrics": limits,
    }
    built.update(context or {})
    return built


def evaluate(metrics, limits):
    """Returns (rows, passed). Each row: name, value, lo, hi, ok, margin_sigma."""
    rows = []
    passed = True
    for key, value in metrics.items():
        limit = limits["metrics"].get(key)
        if limit is None:
            rows.append((key, value, None, None, None, None))
            continue

        lo = limit.get("lo")
        hi = limit.get("hi")
        ok = bool((lo is None or value >= lo) and (hi is None or value <= hi))
        sigma = limit["sigma"] or 1e-9
        margin = abs(value - limit["mean"]) / sigma
        rows.append((key, value, lo, hi, ok, margin))
        passed = passed and ok

    return rows, passed


def print_rows(rows):
    print(f"\n{'metric':<22}{'value':>10}{'low':>10}{'high':>10}{'sigma':>8}  result")
    for key, value, lo, hi, ok, margin in rows:
        lo_s = "-" if lo is None else f"{lo:.2f}"
        hi_s = "-" if hi is None else f"{hi:.2f}"
        if ok is None:
            result, margin_s = "no limit", "-"
        else:
            result = "PASS" if ok else "FAIL"
            margin_s = f"{margin:.1f}"
        print(f"{key:<22}{value:>10.2f}{lo_s:>10}{hi_s:>10}{margin_s:>8}  {result}")


async def run(args):
    tones = parse_tones(args.tones)

    # Harmonics 2f-5f of the lowest tone are where THD is measured, so another
    # stimulus tone sitting on one of them would be counted as distortion.
    primary = min(tones)
    for n in HARMONICS if args.stimulus == "multitone" else ():
        for other in tones:
            if other != primary and abs(other - primary * n) < TONE_BANDWIDTH_HZ:
                print(f"WARNING: {other:.0f} Hz sits on harmonic {n} of {primary:.0f} Hz; "
                      f"THD will read high. Pick tones that are not harmonically related.")

    if args.dump_stimulus:
        seconds = args.capture_ms / 1000.0 + 1.0
        save_wav(args.dump_stimulus,
                 build_stimulus(args.stimulus, tones, seconds, args.amplitude,
                                args.capture_ms, args.tone_gains,
                                args.tone_ramp_ms, args.descending,
                                args.grouped).astype("<i2").tobytes())
        joiner = " then " if args.stimulus == "sequence" else "+"
        print(f"Wrote {args.dump_stimulus}: {args.stimulus}, "
              f"{joiner.join(f'{f:.0f}Hz' for f in tones)}, "
              f"{seconds:.1f}s, amplitude {args.amplitude} of full scale")
        return EXIT_PASS

    if args.stimulus == "sequence":
        tone_ms, _ = sequence_timing(args.capture_ms, len(tones))
        if tone_ms < 50:
            print(f"WARNING: {args.capture_ms} ms capture leaves only {tone_ms:.0f} ms "
                  f"per tone for {len(tones)} tones. Frequency resolution and SNR will "
                  f"suffer; use --capture-ms {int(50 * (4 * len(tones) / 3 + 1))} or more, "
                  "or fewer tones.")

    if args.sd_check:
        seconds = args.capture_ms / 1000.0 + 1.0
        stimulus = build_stimulus(args.stimulus, tones, seconds, args.amplitude,
                                  args.capture_ms, args.tone_gains,
                                  args.tone_ramp_ms, args.descending, args.grouped)
        print(f"Enabling SD on GPIO{args.sd_gpio} and playing the stimulus -- "
              "only that position's speaker should sound.")
        with AmpSelect(args.sd_gpio, args.sd_active_low):
            async with WavePlayer(stimulus, args.alsa_device, args.stimulus_lead):
                await asyncio.sleep(seconds)
        print("Done.")
        return EXIT_PASS

    if args.measure:
        # Used to set speaker level/distance before there is anything to
        # compare against, so it deliberately needs no limits file.
        metrics = await measure_board(args, tones)
        print()
        for key, value in metrics.items():
            print(f"  {key:<22}{value:>10.2f}")

        # Per-tone headroom: shows which frequency the speaker/mic pair is
        # weakest at, which is usually the lowest one.
        print("\n  per-tone in-band SNR (what actually limits the measurement):")
        for tone in tones:
            tone_snr = metrics[f"snr_{int(tone)}hz_db"]
            verdict = "good" if tone_snr >= 25 else ("marginal" if tone_snr >= 15 else "TOO WEAK")
            print(f"    {int(tone):>5} Hz{tone_snr:>9.1f} dB   {verdict}")

        # SNR, not absolute level, is what decides whether a tone is
        # measurable: a quiet tone with plenty of headroom above the in-band
        # noise is perfectly usable.
        weak = [t for t in tones if metrics[f"snr_{int(t)}hz_db"] < 25]
        hot = [t for t in tones if metrics[f"level_{int(t)}hz_dbfs"] > -6]

        # Before any verdict: did the speaker actually make a sound?
        delta = metrics.get("stimulus_delta_db", 99.0)
        if delta < 3.0:
            print(f"\n  STOP: the stimulus capture is only {delta:.1f} dB louder than "
                  "the silence capture.")
            print("  Nothing measurable reached the microphone, so none of the numbers "
                  "above say anything")
            print("  about the board. Check, in this order:")
            print("    - is the speaker actually making a sound? "
                  "aplay -D <device> <any wav>")
            print("    - is --alsa-device right? aplay -l")
            print("    - is another process holding the sound card? fuser -v /dev/snd/*")
            print("    - if using --sd-gpio, is the amplifier's SD line being driven?")
            return EXIT_ERROR

        print("\nReady to calibrate when every tone has SNR above 25 dB and no "
              "tone is above -6 dBFS.")
        if weak:
            print(f"  -> too weak at {', '.join(f'{int(t)} Hz' for t in weak)}: "
                  "raise --amplitude, move the speaker closer, or quieten the fixture")
        elif hot:
            print(f"  -> close to clipping at {', '.join(f'{int(t)} Hz' for t in hot)}: "
                  "lower --amplitude or move the speaker away")
        else:
            print("  -> levels look good; safe to calibrate")

        clipped = metrics.get("clipped_pct", 0.0)
        if clipped > 0.01:
            print(f"\n  STOP: {clipped:.2f}% of samples are at full scale -- the "
                  "capture is clipping.")
            print("  A clipped tone reads at full scale however much louder the "
                  "source really is, so the")
            print("  levels above are floors, not measurements, and thd_pct is "
                  "meaningless. No gains are")
            print("  suggested from a saturated capture: they would be computed from "
                  "a number that is wrong")
            print("  in an unknown direction, and would under-correct.")
            print("\n  Get an unclipped measurement first, then flatten from that:")
            print(f"    --amplitude {max(args.amplitude / 5.0, 0.02):.2f}   "
                  "(re-run until clipped_pct is 0.00)")
            print("  Moving the speaker further from the module does the same job "
                  "and keeps more headroom.")
            return EXIT_ERROR

        # High THD at the lowest tone means the playback chain, not the mic, is
        # distorting -- small speakers give up at low frequencies long before
        # they do at high ones. Every level below is then measuring a broken
        # stimulus, so say so before suggesting anything based on them.
        if metrics.get("thd_pct", 0.0) > 10.0:
            print(f"\n  STOP: thd_pct is {metrics['thd_pct']:.0f}%. At that level the "
                  f"speaker is collapsing at {int(min(tones))} Hz and its energy is "
                  "going into harmonics --")
            print("  the tone levels above are not a measurement of the microphone. "
                  "Lower --amplitude until")
            print(f"  thd_pct is back under ~10%, or drop {int(min(tones))} Hz and use "
                  "tones the speaker can actually deliver.")

        # A speaker that is 20 dB louder at one frequency forces --amplitude to
        # choose between clipping the loud tone and burying the quiet one.
        # Per-tone gains lift the weak tone without touching the loud one,
        # which no single amplitude can do.
        levels = [metrics[f"level_{int(t)}hz_dbfs"] for t in tones]
        spread = max(levels) - min(levels)
        if spread > 6:
            print(f"\n  {spread:.0f} dB spread across tones -- the speaker, not the mic.")
            suggestion = suggest_gains(tones, metrics, args.amplitude, args.tone_gains)
            if suggestion is None:
                print("     (a tone was not measurable, so no gains can be suggested)")
            else:
                new_amplitude, new_gains, achieved = suggestion
                print("     flatten it with:")
                print("       --amplitude {0:.2f} --tone-gains {1}".format(
                    new_amplitude, " ".join(f"{g:.3f}" for g in new_gains)))
                print(f"     every tone should then land near {achieved:.0f} dBFS.")
                if achieved < FLATTEN_TARGET_DBFS - 1.0:
                    print(f"     ({int(tones[int(np.argmax(new_gains))])} Hz is already "
                          "at full output, so the rest come down to meet it rather "
                          "than it coming up.)")
                print("     Re-run --measure afterwards: driving a small speaker harder "
                      "at its weak frequency can raise thd_pct, and if it does, back "
                      "--amplitude off.")
        if metrics.get("thd_harmonics_used", 0) == 0:
            print("  -> THD is meaningless here: every harmonic of "
                  f"{int(min(tones))} Hz collides with another tone. Use tones that "
                  "are not integer multiples, e.g. --tones 1000,2700,5300")
        return EXIT_PASS

    if args.calibrate:
        print(f"Calibrating from {args.runs} run(s) of a known-good board.")
        print("Keep the fixture undisturbed; this defines the limits for every "
              "board afterwards.\n")
        runs = []
        for index in range(1, args.runs + 1):
            print(f"Run {index}/{args.runs}")
            runs.append(await measure_board(args, tones))

        limits = build_limits(runs, tones, args.sigma, args.min_sigma,
                              args.capture_ms, stimulus_context(args, tones))
        with open(args.limits, "w") as handle:
            json.dump(limits, handle, indent=2)

        print(f"\nWrote {args.limits} from {len(runs)} run(s):")
        for key, entry in limits["metrics"].items():
            lo = entry.get("lo")
            span = f"{lo:.2f} .. {entry['hi']:.2f}" if lo is not None else f"<= {entry['hi']:.2f}"
            print(f"  {key:<22} mean {entry['mean']:>8.2f}  sigma {entry['sigma']:>5.2f}  {span}")
        if len(runs) < 5:
            print("\nNOTE: limits from very few runs are weak. 10+ boards is "
                  "a far better basis.")
        return EXIT_PASS

    if not os.path.exists(args.limits):
        print(f"ERROR: {args.limits} not found -- run with --calibrate first")
        return EXIT_ERROR

    with open(args.limits) as handle:
        limits = json.load(handle)

    differences = context_mismatch(limits, stimulus_context(args, tones))
    if differences:
        print(f"ERROR: {args.limits} does not apply to this run:")
        for difference in differences:
            print(f"  {difference}")
        print("Re-run --calibrate with these exact options, or pass the options "
              "the limits were built with.")
        return EXIT_ERROR

    metrics = await measure_board(args, tones)
    rows, passed = evaluate(metrics, limits)
    print_rows(rows)

    print()
    if passed:
        print("RESULT: PASS")
        return EXIT_PASS
    failed = [key for key, _, _, _, ok, _ in rows if ok is not None and not ok]
    print(f"RESULT: FAIL ({', '.join(failed)})")
    return EXIT_FAIL


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Neosapien mic production QC")
    parser.add_argument("--measure", action="store_true",
                        help="take one measurement and print it, without any limits file "
                             "(use this to set speaker level/distance before calibrating)")
    parser.add_argument("--calibrate", action="store_true",
                        help="measure known-good boards and write the limits file")
    parser.add_argument("--runs", type=int, default=10,
                        help="captures to average when calibrating")
    parser.add_argument("--limits", default="limits.json",
                        help="limits file to write (--calibrate) or test against")
    parser.add_argument("--tones", nargs="+",
                        default=[",".join(str(int(f)) for f in DEFAULT_TONES)],
                        help="stimulus frequencies in Hz; commas or spaces both work")
    parser.add_argument("--sigma", type=float, default=3.0,
                        help="limit width in standard deviations")
    parser.add_argument("--min-sigma", type=float, default=0.5,
                        help="floor on sigma so limits never become impossibly tight")
    parser.add_argument("--tone-bandwidth", type=float, default=TONE_BANDWIDTH_HZ,
                        help="measurement bandwidth around each tone in Hz; narrower "
                             "rejects more room noise")
    parser.add_argument("--stimulus-lead", type=float, default=0.5,
                        help="seconds the speaker plays before recording starts, so "
                             "ALSA is definitely outputting by then")
    parser.add_argument("--stimulus", choices=("multitone", "sequence"),
                        default="multitone",
                        help="multitone: all tones at once (shortest capture). "
                             "sequence: an audible beep-beep-beep arpeggio, one tone "
                             "at a time, which an operator can recognise by ear")
    parser.add_argument("--amplitude", type=float, default=0.5,
                        help="stimulus amplitude, 0-1 of full scale")
    parser.add_argument("--adapter", default=None, metavar="HCI",
                        help="Bluetooth controller to use, e.g. hci1")
    parser.add_argument("--tone-ramp-ms", type=float, default=TONE_RAMP_MS,
                        help="raised-cosine edge length; higher is gentler to listen "
                             "to. Clamped so the measured flat top survives.")
    parser.add_argument("--rising", dest="descending", action="store_false",
                        help="play low to high (default is high to low, which sounds "
                             "less like an alarm)")
    parser.add_argument("--even-gaps", dest="grouped", action="store_false",
                        help="equal gaps instead of a grouped phrase")
    parser.add_argument("--tone-gains", type=float, nargs="+", default=None,
                        metavar="G",
                        help="per-tone multiplier, one per --tones value. Flattens a "
                             "speaker that is far louder at one frequency than "
                             "another; --measure prints the values to use.")
    parser.add_argument("--sd-gpio", type=int,
                        help="BCM GPIO driving this position's MAX98357A SD pin, so "
                             "several amps can share one I2S bus")
    parser.add_argument("--sd-active-low", action="store_true",
                        help="use if your SD line is inverted by buffering")
    parser.add_argument("--sd-check", action="store_true",
                        help="enable SD and play the stimulus, to confirm which "
                             "speaker a given pin drives")
    parser.add_argument("--alsa-device",
                        help="ALSA device for the speaker, e.g. plughw:CARD=sndrpihifiberry,DEV=0")
    parser.add_argument("--capture-ms", type=int, default=1000,
                        help="recording length per capture")
    parser.add_argument("--wav", help="save the stimulus capture for inspection")
    parser.add_argument("--dump-stimulus", metavar="PATH",
                        help="write the speaker stimulus to a .wav and exit, so you can "
                             "listen to it or check it on a scope")
    parser.add_argument("--address", help="DUT MAC address (skips scanning)")
    parser.add_argument("--name", default=DEVICE_NAME, help="advertised name to scan for")
    parser.add_argument("--scan-timeout", type=float, default=15.0)
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    parser.add_argument("--transfer-timeout", type=float, default=180.0)
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
