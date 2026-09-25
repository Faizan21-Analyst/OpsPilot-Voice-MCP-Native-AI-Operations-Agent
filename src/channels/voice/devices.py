import os

import pyaudio


def select_audio_devices() -> tuple[int | None, int, int | None]:
    """
    Returns (input_device_index, input_sample_rate, output_device_index).
    """
    pa = pyaudio.PyAudio()

    try:
        input_device_index = _resolve_override("OPSPILOT_INPUT_DEVICE_INDEX")
        output_device_index = _resolve_override("OPSPILOT_OUTPUT_DEVICE_INDEX")

        if output_device_index is not None:
            out_info = pa.get_device_info_by_index(output_device_index)
            print(
                f"[audio] output device pinned via env var: {out_info['name']} "
                f"(index {output_device_index})"
            )
        else:
            output_device_index, out_info = _find_output_device(pa)
            if output_device_index is None:
                print(
                    "[audio] no output device found - voice replies will "
                    "have nowhere to play. Continuing anyway (text/logs "
                    "still work)."
                )

        if input_device_index is not None:
            info = pa.get_device_info_by_index(input_device_index)
            print(
                f"[audio] input device pinned via env var: {info['name']} "
                f"(index {input_device_index})"
            )
        else:
            input_device_index, info = _find_matching_input_device(pa, out_info)
            if input_device_index is None:
                input_device_index, info = _find_input_device(pa)

        if input_device_index is None:
            raise RuntimeError(
                "No microphone detected. Plug in a microphone and check "
                "your OS sound settings, then restart. To force a specific "
                "device, list devices (see list_audio_devices() below) and "
                "set OPSPILOT_INPUT_DEVICE_INDEX."
            )

        input_sample_rate = int(info["defaultSampleRate"])

        return input_device_index, input_sample_rate, output_device_index

    finally:
        pa.terminate()


def _host_api_name(pa: pyaudio.PyAudio, info: dict) -> str:
    try:
        return pa.get_host_api_info_by_index(info["hostApi"])["name"]
    except Exception:
        return "unknown"


def _find_matching_input_device(pa: pyaudio.PyAudio, out_info):
    if not out_info:
        return None, None

    out_name = out_info["name"].lower()
    generic_words = {
        "headphones", "headset", "speakers", "speaker", "audio", "device",
        "microphone", "mic", "realtek", "hands-free", "hands", "free", "ag",
    }
    keywords = [
        w for w in out_name.replace("(", " ").replace(")", " ").split()
        if w not in generic_words and len(w) > 2
    ]

    if not keywords:
        return None, None

    candidates = []
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) <= 0:
            continue
        name = info["name"].lower()
        if any(kw in name for kw in keywords):
            candidates.append((i, info))

    if not candidates:
        return None, None

    print("[audio] candidate mic(s) matching your headset:")
    for i, info in candidates:
        print(f"         [{i}] {info['name']} via {_host_api_name(pa, info)}")

    wasapi_candidates = [
        (i, info) for i, info in candidates
        if "wasapi" in _host_api_name(pa, info).lower()
    ]
    chosen_i, chosen_info = (wasapi_candidates or candidates)[0]

    print(
        f"[audio] using: {chosen_info['name']} (index {chosen_i}, "
        f"{int(chosen_info['defaultSampleRate'])} Hz) via "
        f"{_host_api_name(pa, chosen_info)}"
    )
    print(
        "[audio] note: switching a Bluetooth headset to its mic "
        "profile (Hands-Free) usually drops its OUTPUT audio to "
        "mono/lower quality too - that's a Bluetooth/Windows "
        "limitation, not a bug in this code."
    )
    return chosen_i, chosen_info


def _resolve_override(env_var: str) -> int | None:
    raw = os.environ.get(env_var)
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError:
        print(f"[audio] ignoring invalid {env_var}={raw!r} (must be an integer)")
        return None


def _find_input_device(pa: pyaudio.PyAudio):
    try:
        info = pa.get_default_input_device_info()
        print(
            f"[audio] using default input device: {info['name']} "
            f"(index {info['index']}, {int(info['defaultSampleRate'])} Hz)"
        )
        return info["index"], info
    except OSError:
        pass

    print("[audio] no default input device reported, scanning devices...")
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            print(
                f"[audio] found input device: {info['name']} "
                f"(index {i}, {int(info['defaultSampleRate'])} Hz)"
            )
            return i, info

    return None, None


def _find_output_device(pa: pyaudio.PyAudio):
    try:
        info = pa.get_default_output_device_info()
        print(f"[audio] using default output device: {info['name']} (index {info['index']})")
        return info["index"], info
    except OSError:
        pass

    print("[audio] no default output device reported, scanning devices...")
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("maxOutputChannels", 0) > 0:
            print(f"[audio] found output device: {info['name']} (index {i})")
            return i, info

    return None, None


def list_audio_devices() -> None:
    pa = pyaudio.PyAudio()
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            print(
                f"[{i}] {info['name']} via {_host_api_name(pa, info)} "
                f"(in={info.get('maxInputChannels', 0)}, "
                f"out={info.get('maxOutputChannels', 0)}, "
                f"default_rate={int(info.get('defaultSampleRate', 0))} Hz)"
            )
    finally:
        pa.terminate()