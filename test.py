"""Gradium TTS smoke test — set GRADIUM_API_KEY in .env first."""
import asyncio
import os
import wave

import gradium


async def main():
    api_key = os.environ.get("GRADIUM_API_KEY")
    if not api_key:
        raise SystemExit("Set GRADIUM_API_KEY in your environment or .env file")

    client = gradium.client.GradiumClient(api_key=api_key)

    # Voice: Kent (LFZvm12tW_z0xfGo) — relaxed, authentic American male.
    # Speed 1.8x for brisk, confident delivery.
    result = await client.tts(
        {
            "voice_id": "LFZvm12tW_z0xfGo",
            "output_format": "pcm",
            "json_config": {"speed": 1.8},
        },
        "Alert. Rack 4 is heating at nearly 13 degrees per minute. "
        "It'll throttle in about 3 minutes. "
        "I recommend migrating pod 0006 to Rack 32 — "
        "it has full capacity and 52 degrees of thermal headroom. "
        "Approve or override.",
    )

    # Write as .wav (1 channel, 16-bit, at the model's native sample rate)
    with wave.open("output.wav", "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(result.sample_rate)
        wav.writeframes(result.raw_data)

    duration = len(result.raw_data) / (result.sample_rate * 2)
    print(f"Saved output.wav — {duration:.1f}s at {result.sample_rate}Hz ({len(result.raw_data)} bytes)")


asyncio.run(main())
