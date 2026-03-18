#!/usr/bin/env python3
"""
Standalone Ken Burns Effect Test Script

Downloads a single Wikimedia Commons image and renders a Ken Burns video clip
for quick visual verification. No Streamlit or full pipeline needed.

Usage:
    cd mpt-e-main
    python test_ken_burns.py

Output:
    test_output/ken_burns_test_<preset>.mp4  (one clip per preset)
    test_output/ken_burns_random.mp4         (random preset like production)
"""

import os
import sys
import random
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
from moviepy.video.VideoClip import ImageClip, ColorClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip

# Import the Ken Burns helpers from the updated material.py
from app.services.material import (
    _smoothstep,
    _pick_ken_burns_preset,
    _create_ken_burns_position_fn,
)

# ── Configuration ──
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "test_output")
TEST_IMAGE_URL = "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b6/Image_created_with_a_mobile_phone.png/1280px-Image_created_with_a_mobile_phone.png"
FALLBACK_IMAGE_URL = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/Camponotus_flavomarginatus_ant.jpg/1280px-Camponotus_flavomarginatus_ant.jpg"

# Target canvas (portrait 9:16)
CANVAS_W, CANVAS_H = 1080, 1920
FPS = 24  # Lower FPS for faster test renders
DURATION = 5  # seconds
ZOOM_FACTOR = 1.25

ALL_PRESETS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "diagonal", "parallax"]


def download_test_image(image_url: str, save_path: str) -> bool:
    """Download a test image if not already cached."""
    if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
        print(f"  ✓ Using cached image: {save_path}")
        return True
    
    print(f"  ↓ Downloading: {image_url}")
    try:
        headers = {"User-Agent": "ken-burns-test/1.0"}
        r = requests.get(image_url, headers=headers, timeout=30, stream=True)
        r.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        print(f"  ✓ Downloaded: {os.path.getsize(save_path) / 1024:.0f} KB")
        return True
    except Exception as e:
        print(f"  ✗ Download failed: {e}")
        return False


def render_ken_burns_clip(image_path: str, output_path: str, preset: str, 
                          canvas_w: int, canvas_h: int, duration: float, 
                          zoom: float, _fps: int) -> bool:
    """Render a single Ken Burns clip for the given preset."""
    try:
        img_clip = ImageClip(image_path)
        
        # Cap at 4K
        if img_clip.w > 3840 or img_clip.h > 2160:
            scale = min(3840 / img_clip.w, 2160 / img_clip.h)
            img_clip = img_clip.resized(new_size=(int(img_clip.w * scale), int(img_clip.h * scale)))
        
        # Scale to oversize canvas by zoom factor
        scale_factor = max(canvas_w / img_clip.w, canvas_h / img_clip.h) * zoom
        new_w = int(img_clip.w * scale_factor)
        new_h = int(img_clip.h * scale_factor)
        img_clip = img_clip.resized(new_size=(new_w, new_h))
        
        print(f"    Image: {new_w}x{new_h} → Canvas: {canvas_w}x{canvas_h} | Overflow: {new_w - canvas_w}x{new_h - canvas_h}px")
        
        # Create position function
        position_fn = _create_ken_burns_position_fn(
            preset=preset,
            img_w=new_w, img_h=new_h,
            canvas_w=canvas_w, canvas_h=canvas_h,
            duration=duration,
            zoom_factor=zoom,
        )
        
        img_clip = img_clip.with_position(position_fn).with_duration(duration)
        bg_clip = ColorClip(size=(canvas_w, canvas_h), color=(0, 0, 0)).with_duration(duration)
        final_clip = CompositeVideoClip([bg_clip, img_clip])
        
        final_clip.write_videofile(
            output_path,
            fps=_fps,
            codec="libx264",
            logger=None,
            preset="ultrafast",  # Fast encoding for test
            ffmpeg_params=["-pix_fmt", "yuv420p"],
        )
        
        final_clip.close()
        file_size = os.path.getsize(output_path) / 1024
        print(f"    ✓ Written: {output_path} ({file_size:.0f} KB)")
        return True
        
    except Exception as e:
        print(f"    ✗ Render failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_smoothstep():
    """Quick validation that smoothstep produces correct values."""
    print("\n── Testing smoothstep easing ──")
    assert abs(_smoothstep(0.0) - 0.0) < 0.001, "smoothstep(0) should be 0"
    assert abs(_smoothstep(1.0) - 1.0) < 0.001, "smoothstep(1) should be 1"
    assert abs(_smoothstep(0.5) - 0.5) < 0.001, "smoothstep(0.5) should be 0.5"
    
    # Check ease-in (slow start): derivative at 0 should be 0
    delta = 0.001
    slope_at_0 = (_smoothstep(delta) - _smoothstep(0)) / delta
    assert slope_at_0 < 0.1, f"slope at t=0 should be near 0 (ease-in), got {slope_at_0}"
    
    # Check ease-out (slow end): derivative at 1 should be 0
    slope_at_1 = (_smoothstep(1.0) - _smoothstep(1.0 - delta)) / delta
    assert slope_at_1 < 0.1, f"slope at t=1 should be near 0 (ease-out), got {slope_at_1}"
    
    print("  ✓ All smoothstep assertions passed")


def test_preset_picker():
    """Test that aspect-ratio-aware preset picking works."""
    print("\n── Testing preset picker ──")
    
    # Wide image on portrait canvas → should favor horizontal pans
    presets_wide = [_pick_ken_burns_preset(2000, 800, 1080, 1920) for _ in range(100)]
    horizontal_count = sum(1 for p in presets_wide if p in ("pan_left", "pan_right"))
    print(f"  Wide image → horizontal pans: {horizontal_count}/100 ({horizontal_count}%)")
    assert horizontal_count > 30, "Wide images should favor horizontal pans"
    
    # Tall image on landscape canvas → should favor vertical pans
    presets_tall = [_pick_ken_burns_preset(800, 2000, 1920, 1080) for _ in range(100)]
    vertical_count = sum(1 for p in presets_tall if p in ("pan_up", "pan_down"))
    print(f"  Tall image → vertical pans: {vertical_count}/100 ({vertical_count}%)")
    assert vertical_count > 30, "Tall images should favor vertical pans"
    
    # Square-ish image → should favor zoom effects
    presets_square = [_pick_ken_burns_preset(1200, 1100, 1080, 1080) for _ in range(100)]
    zoom_count = sum(1 for p in presets_square if p in ("zoom_in", "zoom_out"))
    print(f"  Square image → zoom effects: {zoom_count}/100 ({zoom_count}%)")
    assert zoom_count > 30, "Square images should favor zoom effects"
    
    print("  ✓ All preset picker assertions passed")


def main():
    print("=" * 70)
    print("  KEN BURNS EFFECT — STANDALONE TEST")
    print("=" * 70)
    
    # ── Unit tests ──
    test_smoothstep()
    test_preset_picker()
    
    # ── Setup ──
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    image_path = os.path.join(OUTPUT_DIR, "test_image.jpg")
    
    # ── Download test image ──
    print(f"\n── Downloading test image ──")
    if not download_test_image(TEST_IMAGE_URL, image_path):
        print("  Trying fallback image...")
        if not download_test_image(FALLBACK_IMAGE_URL, image_path):
            print("  ✗ Could not download any test image. Exiting.")
            sys.exit(1)
    
    # ── Render one clip per preset ──
    print(f"\n── Rendering {len(ALL_PRESETS)} presets + 1 random ──")
    print(f"   Canvas: {CANVAS_W}x{CANVAS_H} | Duration: {DURATION}s | FPS: {FPS} | Zoom: {ZOOM_FACTOR}")
    
    success_count = 0
    total_time = 0
    
    for preset in ALL_PRESETS:
        output_path = os.path.join(OUTPUT_DIR, f"ken_burns_{preset}.mp4")
        print(f"\n  [{preset}]")
        
        t0 = time.time()
        if render_ken_burns_clip(image_path, output_path, preset, CANVAS_W, CANVAS_H, DURATION, ZOOM_FACTOR, FPS):
            success_count += 1
        elapsed = time.time() - t0
        total_time += elapsed
        print(f"    ⏱️  {elapsed:.1f}s")
    
    # ── Random preset (like production usage) ──
    random_preset = _pick_ken_burns_preset(1280, 960, CANVAS_W, CANVAS_H)  # simulate a typical Wikimedia image
    output_path = os.path.join(OUTPUT_DIR, f"ken_burns_random_{random_preset}.mp4")
    print(f"\n  [random → {random_preset}]")
    
    t0 = time.time()
    if render_ken_burns_clip(image_path, output_path, random_preset, CANVAS_W, CANVAS_H, DURATION, ZOOM_FACTOR, FPS):
        success_count += 1
    elapsed = time.time() - t0
    total_time += elapsed
    print(f"    ⏱️  {elapsed:.1f}s")
    
    # ── Summary ──
    total_clips = len(ALL_PRESETS) + 1
    print(f"\n{'=' * 70}")
    print(f"  RESULTS: {success_count}/{total_clips} clips rendered successfully")
    print(f"  Total time: {total_time:.1f}s ({total_time / total_clips:.1f}s per clip)")
    print(f"  Output dir: {os.path.abspath(OUTPUT_DIR)}")
    print(f"{'=' * 70}")
    
    if success_count == total_clips:
        print("  ✅  ALL TESTS PASSED — review the .mp4 files visually!")
    else:
        print(f"  ⚠️  {total_clips - success_count} clips failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
