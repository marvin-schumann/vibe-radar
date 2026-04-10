"""Generate atmospheric background images for Frequenz shareable cards.

Uses Replicate's REST API directly (avoids the Python SDK's pydantic v1
incompatibility with Python 3.14). Outputs go to src/cards/backgrounds/.

Usage:
    python scripts/generate_card_backgrounds.py [model] [card_name|all]

Examples:
    python scripts/generate_card_backgrounds.py flux-2-pro all
    python scripts/generate_card_backgrounds.py flux-2-max scene_city
    python scripts/generate_card_backgrounds.py flux-schnell taste_dna
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent
SECRETS = Path("/Users/marvinschumann/Library/CloudStorage/OneDrive-Personal/ClaudeWorkspace/secrets/replicate.env")
OUT_DIR = ROOT / "src" / "cards" / "backgrounds"

# Card-specific prompts. Aspect ratio matches Instagram story (9:16).
# Each card type gets a distinct atmospheric background that supports
# the data overlay above it.
PROMPTS: dict[str, str] = {
    "scene_city": (
        "Atmospheric night photograph of an empty European city street at 4am, "
        "wet cobblestones reflecting neon signs, dramatic moody lighting, "
        "deep black sky with subtle lime green and warm orange light leaks, "
        "fog and haze in the air, electronic music nightlife aesthetic, "
        "cinematic wide composition, shot on Kodak Portra 800 at night, "
        "shallow depth of field with bokeh, 9:16 vertical, photorealistic, no people"
    ),
    "taste_dna": (
        "Macro abstract photograph of glowing audio waveforms and light particles "
        "suspended in deep black space, bioluminescent lime green and warm orange "
        "energy strands, ethereal scientific visualization aesthetic, "
        "shallow depth of field, dust motes in volumetric light, "
        "premium dark editorial style, 9:16 vertical, photorealistic, "
        "no text, no people, abstract"
    ),
    "taste_tribe": (
        "Wide cinematic shot of a packed underground techno club crowd "
        "from behind, silhouettes of dancers with hands raised against "
        "bright lime green and orange stage lights, heavy fog machine haze, "
        "lens flare, dark mysterious atmosphere, motion blur on the crowd, "
        "Berlin Berghain aesthetic, 9:16 vertical, photorealistic, faces obscured"
    ),
    "cross_genre": (
        "Macro detail photograph of a vintage analog DJ mixer in a dark studio, "
        "glowing knobs and faders with lime green LEDs and warm orange "
        "VU meter lights, vinyl record edge in soft focus, deep blacks, "
        "shallow depth of field bokeh, music production studio aesthetic, "
        "Annie Leibovitz lighting, 9:16 vertical, photorealistic, no text"
    ),
    "dancefloor": (
        "Long exposure photograph of dancers in motion at an underground "
        "techno club, abstract motion blur of bodies, light trails in "
        "lime green and warm orange, dark venue with deep shadows, "
        "dramatic lasers cutting through fog, ethereal mood, "
        "Wolfgang Tillmans aesthetic, 9:16 vertical, photorealistic, "
        "faces blurred by motion"
    ),
}


def get_token() -> str:
    if not SECRETS.exists():
        sys.exit(f"ERROR: secrets file not found: {SECRETS}")
    return SECRETS.read_text().strip().split("=", 1)[1]


def model_input(model: str, prompt: str) -> dict:
    """Build the Replicate input payload for the given model."""
    if model.startswith("flux-2"):
        return {
            "prompt": prompt,
            "aspect_ratio": "9:16",
            "resolution": "2 MP" if "max" in model else "1 MP",
            "output_format": "png",
            "output_quality": 95,
            "safety_tolerance": 2,
        }
    if model == "flux-schnell":
        return {
            "prompt": prompt,
            "aspect_ratio": "9:16",
            "output_format": "png",
            "output_quality": 95,
            "num_outputs": 1,
            "num_inference_steps": 4,
            "go_fast": True,
        }
    if model == "flux-1.1-pro":
        return {
            "prompt": prompt,
            "aspect_ratio": "9:16",
            "output_format": "png",
            "output_quality": 95,
            "safety_tolerance": 2,
        }
    sys.exit(f"ERROR: unknown model {model}")


def generate(model: str, card_name: str, prompt: str, token: str) -> Path | None:
    """Run a single generation. Returns the path to the saved image, or None on failure."""
    print(f"\n→ {card_name} via {model}")
    print(f"  prompt: {prompt[:100]}...")

    url = f"https://api.replicate.com/v1/models/black-forest-labs/{model}/predictions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "wait",  # block until done; saves a polling loop
    }
    payload = {"input": model_input(model, prompt)}

    with httpx.Client(timeout=300.0) as client:
        try:
            r = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as e:
            print(f"  ✗ network error: {e}")
            return None

    if r.status_code not in (200, 201):
        print(f"  ✗ HTTP {r.status_code}: {r.text[:200]}")
        return None

    body = r.json()
    if body.get("status") not in ("succeeded", "starting", "processing"):
        print(f"  ✗ status {body.get('status')}: {body.get('error', '')}")
        return None

    # If still in progress (Prefer: wait should have blocked, but be safe), poll
    pred_url = body["urls"]["get"]
    while body.get("status") in ("starting", "processing"):
        time.sleep(2)
        r = client.get(pred_url, headers={"Authorization": f"Bearer {token}"})
        body = r.json()

    if body.get("status") != "succeeded":
        print(f"  ✗ final status {body.get('status')}: {body.get('error', '')}")
        return None

    output = body.get("output")
    if isinstance(output, list):
        output = output[0]
    if not output:
        print(f"  ✗ no output URL in response: {body}")
        return None

    print(f"  ✓ generated in {body.get('metrics', {}).get('predict_time', '?')}s")

    # Download the image
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = OUT_DIR / f"{card_name}_{model}.png"
    with httpx.Client(timeout=60.0) as client:
        img = client.get(output)
    filename.write_bytes(img.content)
    print(f"  ↓ saved {filename.relative_to(ROOT)} ({len(img.content)//1024} KB)")
    return filename


def main() -> int:
    args = sys.argv[1:]
    model = args[0] if len(args) > 0 else "flux-2-pro"
    target = args[1] if len(args) > 1 else "all"

    token = get_token()

    if target == "all":
        cards = list(PROMPTS.items())
    elif target in PROMPTS:
        cards = [(target, PROMPTS[target])]
    else:
        sys.exit(f"ERROR: unknown card '{target}'. Options: {', '.join(PROMPTS.keys())} or 'all'")

    print(f"Generating {len(cards)} background(s) with {model}")
    print(f"Output dir: {OUT_DIR.relative_to(ROOT)}")

    results = []
    for card_name, prompt in cards:
        result = generate(model, card_name, prompt, token)
        results.append((card_name, result))

    print("\n=== Results ===")
    for card_name, result in results:
        status = str(result.relative_to(ROOT)) if result else "FAILED"
        print(f"  {card_name}: {status}")

    failed = sum(1 for _, r in results if r is None)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
