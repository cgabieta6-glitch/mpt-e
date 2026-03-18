import os
import random
from typing import List, Optional, Any
from urllib.parse import urlencode

import requests  # type: ignore
from loguru import logger  # type: ignore
from moviepy.video.io.VideoFileClip import VideoFileClip  # type: ignore

from app.config import config  # type: ignore
from app.models.schema import MaterialInfo, VideoAspect, VideoConcatMode  # type: ignore
from app.utils import utils  # type: ignore
from app.services import semantic_video  # type: ignore
from moviepy.video.VideoClip import ImageClip  # type: ignore
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip  # type: ignore
from moviepy.video.VideoClip import ColorClip  # type: ignore

_state = {"requested_count": 0}


def get_api_key(cfg_key: str):
    api_keys = config.app.get(cfg_key)
    if not api_keys:
        raise ValueError(
            f"\n\n##### {cfg_key} is not set #####\n\nPlease set it in the config.toml file: {config.config_file}\n\n"
            f"{utils.to_json(config.app)}"
        )

    # if only one key is provided, return it
    if isinstance(api_keys, str):
        return api_keys

    _state["requested_count"] += 1
    return api_keys[_state["requested_count"] % len(api_keys)]


def search_videos_pexels(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    aspect = VideoAspect(video_aspect)
    video_orientation = aspect.name
    video_width, video_height = aspect.to_resolution()
    api_key = get_api_key("pexels_api_keys")
    headers = {
        "Authorization": api_key,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    }
    # Build URL
    params = {"query": search_term, "per_page": 20, "orientation": video_orientation}
    query_url = f"https://api.pexels.com/videos/search?{urlencode(params)}"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url,
            headers=headers,
            proxies=config.proxy,
            verify=False,
            timeout=(30, 60),
        )
        response = r.json()
        video_items = []
        if "videos" not in response:
            logger.error(f"search videos failed: {response}")
            return video_items
        videos = response["videos"]
        # loop through each video in the result
        for v in videos:
            duration = v["duration"]
            # check if video has desired minimum duration
            if duration < minimum_duration:
                continue
            video_files = v["video_files"]
            # loop through each url to determine the best quality
            for video in video_files:
                w = int(video["width"])
                h = int(video["height"])
                if w == video_width and h == video_height:
                    item = MaterialInfo()
                    item.provider = "pexels"
                    item.url = video["link"]
                    item.duration = duration
                    
                    # Capture image data for similarity comparison
                    if "image" in v:
                        item.thumbnail_url = v["image"]
                    
                    if "video_pictures" in v:
                        item.preview_images = [pic["picture"] for pic in v["video_pictures"]]
                    
                    video_items.append(item)
                    break
        return video_items
    except Exception as e:
        logger.error(f"search videos failed: {str(e)}")

    return []


def search_videos_pixabay(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    aspect = VideoAspect(video_aspect)

    video_width, video_height = aspect.to_resolution()

    api_key = get_api_key("pixabay_api_keys")
    # Build URL
    params = {
        "q": search_term,
        "video_type": "all",  # Accepted values: "all", "film", "animation"
        "per_page": 50,
        "key": api_key,
    }
    query_url = f"https://pixabay.com/api/videos/?{urlencode(params)}"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url, proxies=config.proxy, verify=False, timeout=(30, 60)
        )
        response = r.json()
        video_items = []
        if "hits" not in response:
            logger.error(f"search videos failed: {response}")
            return video_items
        videos = response["hits"]
        # loop through each video in the result
        for v in videos:
            duration = v["duration"]
            # check if video has desired minimum duration
            if duration < minimum_duration:
                continue
            video_files = v["videos"]
            # loop through each url to determine the best quality
            for video_type in video_files:
                video = video_files[video_type]
                w = int(video["width"])
                # h = int(video["height"])
                if w >= video_width:
                    item = MaterialInfo()
                    item.provider = "pixabay"
                    item.url = video["url"]
                    item.duration = duration
                    video_items.append(item)
                    break
        return video_items
    except Exception as e:
        logger.error(f"search videos failed: {str(e)}")

    return []


def search_wikimedia_materials(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    aspect = VideoAspect(video_aspect)
    req_width, req_height = aspect.to_resolution()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    def fetch_materials(filetype: str, is_image: bool) -> List[MaterialInfo]:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f'"{search_term}" filetype:{filetype}',
            "gsrnamespace": 6,  # 6 is File namespace
            "gsrlimit": 50,
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata",
            "iiurlwidth": 640
        }
        
        query_url = "https://commons.wikimedia.org/w/api.php"
        logger.info(f"searching wikimedia {filetype}s: {query_url}?search={search_term}, with proxies: {config.proxy}")

        try:
            r = requests.get(query_url, params=params, headers=headers, proxies=config.proxy, verify=False, timeout=(30, 60))
            r.raise_for_status()

            content_type = (r.headers.get("content-type", "") or "").lower()
            if "json" not in content_type:
                snippet = (r.text or "")[:200].replace("\n", " ")
                logger.error(
                    f"wikimedia {filetype} response is not JSON (status={r.status_code}, content-type={content_type}): {snippet}"
                )
                return []

            try:
                response = r.json()
            except ValueError:
                snippet = (r.text or "")[:200].replace("\n", " ")
                logger.error(
                    f"wikimedia {filetype} returned invalid JSON (status={r.status_code}): {snippet}"
                )
                return []

            items = []
            
            if "query" not in response or "pages" not in response["query"]:
                return items
                
            pages = response["query"]["pages"]
            for page_id, page in pages.items():
                if "imageinfo" not in page:
                    continue
                    
                info = page["imageinfo"][0]
                width = info.get("width", 0)
                height = info.get("height", 0)
                size = info.get("size", 0)
                url = info.get("url", "")
                
                # Check for resolution (relaxed to 480p)
                if width < 480 and height < 480:
                    continue
                    
                # Check for file size limit (> 500MB = 524288000 bytes)
                if size > 524288000:
                    logger.warning(f"skipping wikimedia material {url} due to large size: {size} bytes")
                    continue
                    
                if not url:
                    continue

                # Extract true duration if it's a video
                real_duration = minimum_duration
                if not is_image:
                    ext_meta = info.get("extmetadata", {})
                    if "length" in ext_meta:
                        try:
                            # length is usually a string representing seconds
                            real_duration = float(ext_meta.get("length", {}).get("value", 0))
                        except (ValueError, TypeError):
                            pass
                    
                    # Ensure the video meets the actual minimum duration requirement
                    if real_duration < minimum_duration:
                        continue
                    # Reject excessively long videos to prevent massive downloads (e.g., > 15 mins)
                    if real_duration > 900:
                        continue

                item = MaterialInfo()
                item.provider = "wikimedia"
                item.url = url
                item.duration = real_duration
                item.is_image = is_image
                item.thumbnail_url = info.get("thumburl", "") or ""
                
                items.append(item)

            return items
        except Exception as e:
            logger.error(f"search wikimedia {filetype}s failed: {str(e)}")
            return []

    # Two-step fallback logic: try video first, fallback to bitmap
    video_items = fetch_materials("video", False)
    if video_items:
        return video_items
        
    logger.info(f"no wikimedia videos found for '{search_term}', falling back to images")
    image_items = fetch_materials("bitmap", True)
    return image_items


def save_video(video_url: str, save_dir: str = "", search_term: str = "", thumbnail_url: str = "", preview_images: Optional[list] = None) -> str:
    if not save_dir:
        save_dir = utils.storage_dir("cache_videos")

    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    url_without_query = video_url.split("?")[0]
    url_hash = utils.md5(url_without_query)
    video_id = f"vid-{url_hash}"
    
    # Detect the correct native extension from the URL
    ext = os.path.splitext(url_without_query)[-1].lower()
    if not ext or ext not in ['.mp4', '.webm', '.ogv', '.ogg', '.mov']:
        ext = '.mp4'  # Fallback
        
    native_video_path = f"{save_dir}/{video_id}{ext}"
    final_mp4_path = f"{save_dir}/{video_id}.mp4"

    # if video already exists, return the path
    if os.path.exists(final_mp4_path) and os.path.getsize(final_mp4_path) > 0:
        logger.info(f"video already exists: {final_mp4_path}")
        # Save metadata — wrapped in try/except to survive Streamlit thread context issues
        if search_term and not semantic_video.load_video_metadata(final_mp4_path):
            try:
                additional_info: dict[str, Any] = {}
                if thumbnail_url:
                    additional_info["thumbnail_url"] = thumbnail_url
                if preview_images:
                    additional_info["preview_images"] = preview_images
                semantic_video.save_video_metadata(final_mp4_path, search_term, additional_info)
            except Exception as meta_err:
                # Swallow Streamlit NoSessionContext and similar thread errors
                logger.debug(f"metadata save skipped (will retry on main thread): {meta_err}")
        return final_mp4_path

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    # ── Streaming download (Wikimedia files can be 100MB+, need generous timeouts) ──
    # connect=120s, read=600s — large .webm/.ogv may stream slowly from Wikimedia CDN
    download_timeout = (
        int(config.app.get("download_connect_timeout", 120)),
        int(config.app.get("download_read_timeout", 600)),
    )
    logger.info(f"downloading {ext} file: {video_url} (timeout: {download_timeout})")

    try:
        with requests.get(video_url, headers=headers, proxies=config.proxy, verify=False,
                          stream=True, timeout=download_timeout) as r:
            r.raise_for_status()
            downloaded_bytes = 0
            with open(native_video_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):  # 64KB chunks for speed
                    f.write(chunk)
                    downloaded_bytes += len(chunk)
            logger.info(f"download complete: {downloaded_bytes / 1024 / 1024:.1f} MB → {native_video_path}")
    except requests.exceptions.Timeout:
        logger.error(f"download timed out after {download_timeout}s: {video_url}")
        return ""
    except requests.exceptions.RequestException as dl_err:
        logger.error(f"download failed: {video_url} → {dl_err}")
        return ""

    if os.path.exists(native_video_path) and os.path.getsize(native_video_path) > 0:
        try:
            # ── Transcode non-mp4 formats via FFmpeg with subprocess timeout ──
            if ext != '.mp4':
                import subprocess
                logger.info(f"transcoding {ext} to mp4: {native_video_path}")
                
                ffmpeg_exe = "ffmpeg"
                try:
                    from moviepy.config import get_setting
                    ffmpeg_exe = get_setting("FFMPEG_BINARY")
                except ImportError:
                    pass

                # FFmpeg timeout = 180s — even large .ogv/.webm should finish within this
                ffmpeg_timeout = int(config.app.get("ffmpeg_transcode_timeout", 180))
                cmd = [
                    ffmpeg_exe, "-i", native_video_path, 
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k",
                    "-y", final_mp4_path
                ]
                try:
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=ffmpeg_timeout)
                    logger.info(f"transcode complete: {final_mp4_path}")
                except subprocess.TimeoutExpired:
                    logger.error(f"FFmpeg transcode timed out after {ffmpeg_timeout}s: {native_video_path}")
                    _cleanup_files([native_video_path, final_mp4_path])
                    return ""
                except subprocess.CalledProcessError as ffmpeg_err:
                    logger.error(f"FFmpeg transcode failed (exit {ffmpeg_err.returncode}): {native_video_path}")
                    _cleanup_files([native_video_path, final_mp4_path])
                    return ""

                # Cleanup native file after successful transcode
                try:
                    os.remove(native_video_path)
                except Exception:
                    pass
            else:
                final_mp4_path = native_video_path
                
            # ── Validate the transcoded/downloaded MP4 ──
            clip = VideoFileClip(final_mp4_path)
            duration = clip.duration
            fps = clip.fps
            clip.close()
            
            del clip
            import gc
            gc.collect()
            
            if duration > 0 and fps > 0:
                # Save metadata — wrapped to survive thread context issues
                if search_term:
                    try:
                        additional_info = {}  # type: dict[str, Any]
                        if thumbnail_url:
                            additional_info["thumbnail_url"] = thumbnail_url
                        if preview_images:
                            additional_info["preview_images"] = preview_images
                        semantic_video.save_video_metadata(final_mp4_path, search_term, additional_info)
                    except Exception as meta_err:
                        logger.debug(f"metadata save deferred (thread context): {meta_err}")
                return final_mp4_path
            else:
                logger.warning(f"invalid video (duration={duration}, fps={fps}): {final_mp4_path}")
        except Exception as e:
            _cleanup_files([final_mp4_path, native_video_path])
            logger.warning(f"invalid video file: {final_mp4_path} => {str(e)}")
    return ""


def _cleanup_files(paths: List[str]) -> None:
    """Silently remove a list of file paths (best-effort)."""
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def _smoothstep(t: float) -> float:
    """Hermite smoothstep interpolation: smooth ease-in/ease-out for cinematic motion."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _pick_ken_burns_preset(img_w: int, img_h: int, canvas_w: int, canvas_h: int) -> str:
    """Choose a Ken Burns motion preset based on image vs canvas aspect ratio with randomness."""
    img_ratio = img_w / max(img_h, 1)
    canvas_ratio = canvas_w / max(canvas_h, 1)
    
    # Aspect-ratio-aware presets weighted by suitability
    if img_ratio > canvas_ratio * 1.15:
        # Image is wider than canvas → horizontal pans work best
        presets = ["pan_left", "pan_right", "pan_left", "pan_right", "zoom_in", "zoom_out", "diagonal", "parallax"]
    elif img_ratio < canvas_ratio * 0.85:
        # Image is taller than canvas → vertical pans work best
        presets = ["pan_up", "pan_down", "pan_up", "pan_down", "zoom_in", "zoom_out", "diagonal", "parallax"]
    else:
        # Square-ish → zoom effects look best
        presets = ["zoom_in", "zoom_out", "zoom_in", "zoom_out", "pan_left", "pan_right", "diagonal", "parallax"]
    
    return random.choice(presets)


def _create_ken_burns_position_fn(
    preset: str,
    img_w: int, img_h: int,
    canvas_w: int, canvas_h: int,
    duration: float,
    zoom_factor: float = 1.25,
):
    """Create a position function for the given Ken Burns preset.
    
    Returns a callable (t) -> (x, y) for use with MoviePy .with_position().
    All motion uses smoothstep easing for cinematic feel.
    """
    x_overflow = img_w - canvas_w
    y_overflow = img_h - canvas_h
    
    # Ensure non-negative overflow (image should always be >= canvas after scaling)
    x_overflow = max(0, x_overflow)
    y_overflow = max(0, y_overflow)
    
    # Center offsets
    cx = -x_overflow / 2.0
    cy = -y_overflow / 2.0

    def _pos(t):
        progress = _smoothstep(max(0.0, min(1.0, t / duration)))
        
        if preset == "pan_left":
            # Pan from right edge to left edge
            x = -(x_overflow * (1.0 - progress))
            y = cy  # vertically centered
        elif preset == "pan_right":
            # Pan from left edge to right edge
            x = -(x_overflow * progress)
            y = cy
        elif preset == "pan_up":
            # Pan from bottom to top
            x = cx
            y = -(y_overflow * (1.0 - progress))
        elif preset == "pan_down":
            # Pan from top to bottom
            x = cx
            y = -(y_overflow * progress)
        elif preset == "diagonal":
            # Random diagonal direction picked per clip
            dx = random.choice([-1, 1])
            dy = random.choice([-1, 1])
            start_x = 0 if dx > 0 else -x_overflow
            start_y = 0 if dy > 0 else -y_overflow
            end_x = -x_overflow if dx > 0 else 0
            end_y = -y_overflow if dy > 0 else 0
            x = start_x + (end_x - start_x) * progress
            y = start_y + (end_y - start_y) * progress
        elif preset == "parallax":
            # Gentle S-curve horizontal drift with slight vertical sway
            import math as _math
            x = cx + (x_overflow * 0.4) * _math.sin(progress * _math.pi)
            y = cy + (y_overflow * 0.15) * _math.cos(progress * _math.pi * 0.5)
        elif preset == "zoom_out":
            # Start zoomed-in on center, pull back (simulated by starting tight, ending centered)
            zoom_progress = 1.0 - progress  # reverse
            tightness = 0.3 + 0.7 * (1.0 - zoom_progress)
            x = -(x_overflow * (0.5 - tightness * 0.5 + tightness * 0.5))
            y = -(y_overflow * (0.5 - tightness * 0.5 + tightness * 0.5))
            # Drift slightly during zoom for more natural feel
            x += (x_overflow * 0.15) * (1.0 - progress)
            y += (y_overflow * 0.1) * progress
        else:
            # zoom_in (default) — drift toward center while feeling like camera pushes in
            x = -(x_overflow * 0.1) - (x_overflow * 0.3) * progress
            y = -(y_overflow * 0.1) - (y_overflow * 0.3) * progress
        
        return (x, y)
    
    # Pre-resolve the random diagonal direction so it's consistent across frames
    if preset == "diagonal":
        dx = random.choice([-1, 1])
        dy = random.choice([-1, 1])
        start_x = 0 if dx > 0 else -x_overflow
        start_y = 0 if dy > 0 else -y_overflow
        end_x = -x_overflow if dx > 0 else 0
        end_y = -y_overflow if dy > 0 else 0
        
        def _diagonal_pos(t):
            progress = _smoothstep(max(0.0, min(1.0, t / duration)))
            x = start_x + (end_x - start_x) * progress
            y = start_y + (end_y - start_y) * progress
            return (x, y)
        return _diagonal_pos
    
    return _pos


def save_image_as_video(
    image_url: str,
    save_dir: str = "",
    search_term: str = "",
    target_duration: int = 5,
    video_aspect: VideoAspect = VideoAspect.portrait,
    ken_burns_zoom_factor: float = 0.0,
    ken_burns_speed: str = "",
) -> str:
    """Convert a static image to a video clip with cinematic Ken Burns effect.
    
    Args:
        image_url: URL of the image to download and convert.
        save_dir: Directory to save the output video. Defaults to cache_videos.
        search_term: Search term for metadata tagging.
        target_duration: Base duration in seconds for the output clip.
        video_aspect: Target aspect ratio.
        ken_burns_zoom_factor: Overscale factor (1.15-1.50). 0 = use config/default.
        ken_burns_speed: Speed preset ("slow", "normal", "fast"). Empty = use config/default.
    """
    if not save_dir:
        save_dir = utils.storage_dir("cache_videos")

    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    url_without_query = image_url.split("?")[0]
    url_hash = utils.md5(url_without_query)
    image_id = f"img-{url_hash}"
    image_path = f"{save_dir}/{image_id}.jpg"
    video_path = f"{save_dir}/{image_id}.mp4"

    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        logger.info(f"video already exists: {video_path}")
        if search_term and not semantic_video.load_video_metadata(video_path):
            additional_info = {"thumbnail_url": image_url}  # type: dict[str, Any]
            semantic_video.save_video_metadata(video_path, search_term, additional_info)
        return video_path

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    # ── Streaming download (avoids OOM on large Wikimedia images) ──
    if not os.path.exists(image_path) or os.path.getsize(image_path) == 0:
        try:
            with requests.get(
                image_url,
                headers=headers,
                proxies=config.proxy,
                verify=False,
                timeout=(60, 240),
                stream=True,
            ) as r:
                r.raise_for_status()
                with open(image_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)
        except Exception as dl_err:
            logger.error(f"failed to download image {image_url}: {dl_err}")
            return ""

    if os.path.exists(image_path) and os.path.getsize(image_path) > 0:
        try:
            from app.services.video import (  # type: ignore
                video_bitrate, quality_params, fps,
                _write_videofile_with_fallback,
            )

            aspect = VideoAspect(video_aspect)
            req_width, req_height = aspect.to_resolution()

            # ── Resolve Ken Burns parameters from config / defaults ──
            zoom = ken_burns_zoom_factor
            if zoom <= 0:
                zoom = float(config.app.get("ken_burns_zoom_factor", 1.25))
            zoom = max(1.10, min(1.50, zoom))  # clamp to sane range

            speed = ken_burns_speed or config.app.get("ken_burns_speed", "normal")
            speed_multipliers = {"slow": 1.4, "normal": 1.0, "fast": 0.7}
            duration_mult = speed_multipliers.get(speed, 1.0)
            effective_duration = max(2, int(target_duration * duration_mult))

            img_clip = ImageClip(image_path)

            # ── Resizing Guard: cap at 4K to conserve memory ──
            if img_clip.w > 3840 or img_clip.h > 2160:
                logger.info(f"downscaling extremely large image {img_clip.size} to conserve memory: {image_path}")
                scale = min(3840 / img_clip.w, 2160 / img_clip.h)
                new_w, new_h = int(img_clip.w * scale), int(img_clip.h * scale)
                img_clip = img_clip.resized(new_size=(new_w, new_h))

            # ── Low-res quality guard ──
            min_dim = min(img_clip.w, img_clip.h)
            target_min_dim = min(req_width, req_height)
            if min_dim < target_min_dim * 0.4:
                logger.warning(
                    f"image too small for quality Ken Burns ({img_clip.w}x{img_clip.h} vs "
                    f"target {req_width}x{req_height}). Proceeding with gentle zoom only."
                )
                zoom = min(zoom, 1.15)  # reduce zoom to hide upscale artifacts

            # ── Scale image to oversize the canvas by zoom factor ──
            scale_factor = max(req_width / img_clip.w, req_height / img_clip.h) * zoom
            new_width = int(img_clip.w * scale_factor)
            new_height = int(img_clip.h * scale_factor)
            img_clip = img_clip.resized(new_size=(new_width, new_height))

            # ── Pick motion preset and create position function ──
            preset = _pick_ken_burns_preset(new_width, new_height, req_width, req_height)
            logger.info(f"Ken Burns preset: {preset}, zoom: {zoom:.2f}, duration: {effective_duration}s")

            position_fn = _create_ken_burns_position_fn(
                preset=preset,
                img_w=new_width, img_h=new_height,
                canvas_w=req_width, canvas_h=req_height,
                duration=effective_duration,
                zoom_factor=zoom,
            )

            img_clip = img_clip.with_position(position_fn).with_duration(effective_duration)

            # ── Composite onto black background ──
            bg_clip = ColorClip(size=(req_width, req_height), color=(0, 0, 0)).with_duration(effective_duration)
            final_clip = CompositeVideoClip([bg_clip, img_clip])

            # ── Write via NVENC-aware fallback ──
            _write_videofile_with_fallback(
                final_clip,
                video_path,
                fps=fps,
                logger=None,
                bitrate=video_bitrate,
                ffmpeg_params=quality_params,
            )

            # ── Cleanup (close composite which recursively closes children) ──
            final_clip.close()
            del final_clip, img_clip, bg_clip
            import gc
            gc.collect()

            if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
                if search_term:
                    additional_info = {"thumbnail_url": image_url}  # type: dict[str, Any]
                    semantic_video.save_video_metadata(video_path, search_term, additional_info)
                return video_path
        except Exception as e:
            try:
                os.remove(video_path)
            except Exception:
                pass
            logger.warning(f"invalid image or conversion failed: {image_url} => {str(e)}")

    return ""


def download_videos(
    task_id: str,
    search_terms: List[str],
    source: str = "pexels",
    video_aspect: VideoAspect = VideoAspect.portrait,
    video_contact_mode: VideoConcatMode = VideoConcatMode.random,
    audio_duration: float = 0.0,
    max_clip_duration: int = 5,
) -> List[str]:
    videos_by_term: dict[str, Any] = {}
    found_duration: float = 0.0
    
    # Global URL tracking to prevent duplicates across all search terms
    global_video_urls = set()
    
    for search_term in search_terms:
        if source == "pixabay":
            video_items = search_videos_pixabay(
                search_term=search_term,
                minimum_duration=max_clip_duration,
                video_aspect=video_aspect,
            )
        elif source == "wikimedia":
            video_items = search_wikimedia_materials(
                search_term=search_term,
                minimum_duration=max_clip_duration,
                video_aspect=video_aspect,
            )
            if not video_items:
                pexels_keys = config.app.get("pexels_api_keys") or []
                if pexels_keys:
                    logger.warning(
                        f"wikimedia returned no materials for '{search_term}', falling back to pexels for this term"
                    )
                    try:
                        video_items = search_videos_pexels(
                            search_term=search_term,
                            minimum_duration=max_clip_duration,
                            video_aspect=video_aspect,
                        )
                    except ValueError as e:
                        logger.error(
                            f"pexels fallback is configured but unavailable for '{search_term}': {e}"
                        )
                        video_items = []
                else:
                    logger.warning(
                        f"wikimedia returned no materials for '{search_term}', and pexels_api_keys is not set; skipping fallback"
                    )
        else:
            video_items = search_videos_pexels(
                search_term=search_term,
                minimum_duration=max_clip_duration,
                video_aspect=video_aspect,
            )
        logger.info(f"found {len(video_items)} videos for '{search_term}'")

        # Filter out duplicates and associate with search term
        unique_videos: list[Any] = []
        duplicates_removed: int = 0
        
        for item in video_items:
            item_url = getattr(item, 'url', '')
            item_duration = getattr(item, 'duration', 0.0)
            # Check for URL duplicates across all search terms
            if item_url not in global_video_urls:
                setattr(item, 'search_term', search_term)
                unique_videos.append(item)
                global_video_urls.add(item_url)
                found_duration += float(item_duration)  # type: ignore
            else:
                duplicates_removed += 1  # type: ignore
        
        if duplicates_removed > 0:
            logger.info(f"removed {duplicates_removed} duplicate URLs for '{search_term}'")
        
        if unique_videos:
            videos_by_term[search_term] = unique_videos

    logger.info(
        f"found videos from {len(videos_by_term)} search terms, total duration: {found_duration} seconds, required: {audio_duration} seconds"
    )
    logger.info(f"total unique video URLs: {len(global_video_urls)}")

    # Create balanced selection from all search terms
    valid_video_items = []
    valid_video_urls = set()
    
    # Round-robin selection from each search term to ensure diversity
    max_videos_per_term: int = max(1, int(audio_duration / max_clip_duration / len(videos_by_term)) + 1) if videos_by_term else 1
    logger.info(f"targeting max {max_videos_per_term} videos per search term for balanced selection")
    
    # Track selection statistics
    selection_stats: dict[str, int] = {}
    
    for search_term, videos in videos_by_term.items():
        # Shuffle videos within each search term
        if video_contact_mode.value == VideoConcatMode.random.value:
            random.shuffle(videos)
        
        # Take up to max_videos_per_term from this search term
        count: int = 0
        for item in videos:
            item_url = getattr(item, 'url', '')
            if item_url not in valid_video_urls and count < max_videos_per_term:  # type: ignore
                valid_video_items.append(item)
                valid_video_urls.add(item_url)
                count += 1  # type: ignore
        
        selection_stats[search_term] = count
        logger.info(f"selected {count} videos from '{search_term}' ({count}/{len(videos)} available)")
    
    # Final shuffle of the balanced selection
    if video_contact_mode.value == VideoConcatMode.random.value:
        random.shuffle(valid_video_items)
    
    logger.info(f"selected {len(valid_video_items)} videos for download with balanced representation")
    
    # Log diversity metrics
    logger.info("🎯 Diversity metrics:")
    logger.info(f"   📊 Search terms represented: {len(selection_stats)}/{len(search_terms)}")
    for term, count in selection_stats.items():
        percentage = (count / len(valid_video_items)) * 100 if valid_video_items else 0
        logger.info(f"   📹 '{term}': {count} videos ({percentage:.1f}%)")

    # ── Thread-safe download loop with retries and configurable timeouts ──
    # Timeout defaults to 300s to handle slow Wikimedia transcoding/downloads
    task_timeout = int(config.app.get("download_task_timeout", 300))
    max_retries = int(config.app.get("download_max_retries", 2))
    
    # Store metadata to be saved on the main thread at the end (to avoid Streamlit thread crashes)
    deferred_metadata = []
    video_paths = []
    downloaded_urls = set()
    total_duration = 0.0

    material_directory = config.app.get("material_directory", "").strip()
    if material_directory == "task":
        material_directory = utils.task_dir(task_id)
    elif material_directory and not os.path.isdir(material_directory):
        material_directory = ""

    import threading
    class WorkerThread(threading.Thread):
        def __init__(self, func, *args, **kwargs):
            super().__init__()
            self.func = func
            self.args = args
            self.kwargs = kwargs
            self.result = ""
            self.exception = None
            self.daemon = True
            
        def run(self):
            try:
                self.result = self.func(*self.args, **self.kwargs)
            except Exception as e:
                self.exception = e  # type: ignore

    for item in valid_video_items:
        try:
            item_url = getattr(item, 'url', '')
            item_duration = getattr(item, 'duration', 0.0)
            item_thumbnail_url = getattr(item, 'thumbnail_url', '')
            item_preview_images = getattr(item, 'preview_images', [])
            item_search_term = getattr(item, 'search_term', 'unknown')
            is_image = getattr(item, 'is_image', False)
            
            if item_url in downloaded_urls:
                continue
                
            saved_video_path = ""
            for attempt in range(max_retries + 1):
                try:
                    attempt_str = f" (attempt {attempt + 1}/{max_retries + 1})" if attempt > 0 else ""
                    logger.info(f"processing material{attempt_str}: {item_url}")
                    
                    if is_image:
                        worker = WorkerThread(save_image_as_video, image_url=item_url, save_dir=material_directory, search_term=item_search_term, target_duration=max_clip_duration, video_aspect=video_aspect)
                    else:
                        worker = WorkerThread(save_video, video_url=item_url, save_dir=material_directory, search_term=item_search_term, thumbnail_url=item_thumbnail_url, preview_images=item_preview_images)
                        
                    worker.start()
                    worker.join(timeout=task_timeout)
                    
                    if worker.is_alive():
                        logger.error(f"timeout ({task_timeout}s) for: {item_url}. Abandoning thread.")
                        break # Don't retry on hard timeout as it likely indicates a stuck process
                        
                    if worker.exception:
                        logger.error(f"failed: {item_url}. Error: {str(worker.exception)}")
                        continue
                        
                    saved_video_path = worker.result
                    if saved_video_path:
                        break # Success!
                        
                except Exception as e:
                    logger.error(f"unexpected error in attempt {attempt + 1}: {e}")
            
            if saved_video_path:
                logger.info(f"video saved: {saved_video_path}")
                video_paths.append(saved_video_path)
                downloaded_urls.add(item_url)
                
                # Queue metadata saving for the main thread to ensure Streamlit safety
                deferred_metadata.append({
                    "path": saved_video_path,
                    "term": item_search_term,
                    "info": {
                        "thumbnail_url": item_thumbnail_url,
                        "preview_images": item_preview_images
                    }
                })
                
                seconds: float = float(min(float(max_clip_duration), float(item_duration)))
                total_duration += seconds
                if total_duration >= audio_duration:
                    logger.info(f"reached required duration ({total_duration}s), stopping downloads.")
                    break
            else:
                logger.error(f"permanently failed to download/process: {item_url}")

        except Exception as e:
            logger.error(f"failed to download video loop: {str(e)}")
    
    # ── Final Main-Thread Metadata Pass (Streamlit Safe) ──
    if deferred_metadata:
        logger.info(f"saving metadata for {len(deferred_metadata)} materials on main thread...")
        for meta in deferred_metadata:
            try:
                if not semantic_video.load_video_metadata(meta["path"]):
                    semantic_video.save_video_metadata(meta["path"], meta["term"], meta["info"])
            except Exception as e:
                logger.debug(f"secondary metadata save failed (non-critical): {e}")

    # Final diversity report
    logger.success(f"downloaded {len(video_paths)} videos")
    logger.info(f"🎯 Final diversity: {len(downloaded_urls)} unique URLs downloaded")
    
    return video_paths


if __name__ == "__main__":
    download_videos(
        "test_wikimedia", ["nature landscape"], audio_duration=10, source="wikimedia"
    )
