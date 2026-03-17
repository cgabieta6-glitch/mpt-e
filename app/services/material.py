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
            "gsrsearch": f"filetype:{filetype} {search_term}",
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
                
                # Check for high resolution
                if width < 1080 and height < 1080:
                    continue
                    
                # Check for file size limit (> 100MB = 104857600 bytes)
                if size > 104857600:
                    logger.warning(f"skipping wikimedia material {url} due to large size: {size} bytes")
                    continue
                    
                if not url:
                    continue

                item = MaterialInfo()
                item.provider = "wikimedia"
                item.url = url
                item.duration = minimum_duration
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
    video_path = f"{save_dir}/{video_id}.mp4"

    # if video already exists, return the path
    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        logger.info(f"video already exists: {video_path}")
        # Save metadata if search_term is provided and metadata doesn't exist
        if search_term and not semantic_video.load_video_metadata(video_path):
            additional_info: dict[str, Any] = {}
            if thumbnail_url:
                additional_info["thumbnail_url"] = thumbnail_url
            if preview_images:
                additional_info["preview_images"] = preview_images
            semantic_video.save_video_metadata(video_path, search_term, additional_info)
        return video_path

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    # if video does not exist, download it
    with open(video_path, "wb") as f:
        f.write(
            requests.get(
                video_url,
                headers=headers,
                proxies=config.proxy,
                verify=False,
                timeout=(60, 240),
            ).content
        )

    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        try:
            clip = VideoFileClip(video_path)
            duration = clip.duration
            fps = clip.fps
            clip.close()
            
            del clip
            import gc
            gc.collect()
            
            if duration > 0 and fps > 0:
                # Save metadata with search term and image data
                if search_term:
                    additional_info = {} # type: dict[str, Any]
                    if thumbnail_url:
                        additional_info["thumbnail_url"] = thumbnail_url
                    if preview_images:
                        additional_info["preview_images"] = preview_images
                    semantic_video.save_video_metadata(video_path, search_term, additional_info)
                return video_path
        except Exception as e:
            try:
                os.remove(video_path)
            except Exception:
                pass
            logger.warning(f"invalid video file: {video_path} => {str(e)}")
    return ""


def save_image_as_video(image_url: str, save_dir: str = "", search_term: str = "", target_duration: int = 5, video_aspect: VideoAspect = VideoAspect.portrait) -> str:
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
            additional_info = {"thumbnail_url": image_url} # type: dict[str, Any]
            semantic_video.save_video_metadata(video_path, search_term, additional_info)
        return video_path

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    # Download image
    if not os.path.exists(image_path) or os.path.getsize(image_path) == 0:
        with open(image_path, "wb") as f:
            f.write(
                requests.get(
                    image_url,
                    headers=headers,
                    proxies=config.proxy,
                    verify=False,
                    timeout=(60, 240),
                ).content
            )

    if os.path.exists(image_path) and os.path.getsize(image_path) > 0:
        try:
            from app.services.video import video_codec, video_bitrate, audio_bitrate, quality_params, fps  # type: ignore
            
            aspect = VideoAspect(video_aspect)
            req_width, req_height = aspect.to_resolution()

            img_clip = ImageClip(image_path)
            
            # Resizing Guard (4K max)
            if img_clip.w > 3840 or img_clip.h > 2160:
                logger.info(f"downscaling extremely large image {img_clip.size} to conserve memory: {image_path}")
                scale = min(1920 / img_clip.w, 1080 / img_clip.h)
                new_w, new_h = int(img_clip.w * scale), int(img_clip.h * scale)
                img_clip = img_clip.resized(new_size=(new_w, new_h))
            
            # Apply Ken Burns effect
            # Scale the image slightly larger than the target resolution
            scale_factor = max(req_width / img_clip.w, req_height / img_clip.h) * 1.2
            new_width = int(img_clip.w * scale_factor)
            new_height = int(img_clip.h * scale_factor)
            img_clip = img_clip.resized(new_size=(new_width, new_height))
            
            # Smoothly pan over the duration
            def get_position(t):
                # Calculate progress from 0 to 1
                progress = max(0.0, min(1.0, t / target_duration))
                
                # Pan from (left, top) to (right, bottom) of the overflowing region
                # When image is larger than canvas, position can be negative text indicating the top-left corner of the image relative to canvas
                x_overflow = img_clip.w - req_width
                y_overflow = img_clip.h - req_height
                
                x = - (x_overflow * progress)
                y = - (y_overflow * progress)
                return (x, y)
                
            img_clip = img_clip.with_position(get_position)
            
            # Create a background to composite on
            bg_clip = ColorClip(size=(req_width, req_height), color=(0, 0, 0)).with_duration(target_duration)
            final_clip = CompositeVideoClip([bg_clip, img_clip.with_duration(target_duration)])
            
            final_clip.write_videofile(
                video_path, 
                fps=fps, 
                codec=video_codec, 
                logger=None,
                bitrate=video_bitrate,
                ffmpeg_params=quality_params
            )
            
            final_clip.close()
            img_clip.close()
            bg_clip.close()
            
            del final_clip
            del img_clip
            del bg_clip
            import gc
            gc.collect()
            
            if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
                if search_term:
                    additional_info = {"thumbnail_url": image_url} # type: dict[str, Any]
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

    video_paths = []
    material_directory = config.app.get("material_directory", "").strip()
    if material_directory == "task":
        material_directory = utils.task_dir(task_id)
    elif material_directory and not os.path.isdir(material_directory):
        material_directory = ""

    total_duration = 0.0
    downloaded_urls = set()  # Track downloaded URLs to prevent runtime duplicates
    
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
            
            # Double-check for URL duplicates at download time
            if item_url in downloaded_urls:
                logger.warning(f"skipping duplicate URL: {item_url}")
                continue
                
            logger.info(f"downloading material: {item_url}")
            
            # Check if this material is an image or video based on the flag we set during search
            is_image = getattr(item, 'is_image', False)
            
            if is_image:
                worker = WorkerThread(save_image_as_video, image_url=item_url, save_dir=material_directory, search_term=item_search_term, target_duration=max_clip_duration, video_aspect=video_aspect)
            else:
                worker = WorkerThread(save_video, video_url=item_url, save_dir=material_directory, search_term=item_search_term, thumbnail_url=item_thumbnail_url, preview_images=item_preview_images)
                
            worker.start()
            worker.join(timeout=60)
            
            if worker.is_alive():
                logger.error(f"task timeout (60s) processing material: {item_url}. Abandoning thread and moving to next.")
                continue
                
            if worker.exception:
                logger.error(f"task failed processing material: {item_url}. Error: {str(worker.exception)}")
                continue
                
            saved_video_path = worker.result
                
            if saved_video_path:
                logger.info(f"video saved: {saved_video_path} (search_term: '{item_search_term}')")
                video_paths.append(saved_video_path)
                downloaded_urls.add(item_url)
                seconds: float = float(min(float(max_clip_duration), float(item_duration)))
                total_duration += seconds  # type: ignore
                if total_duration > audio_duration:
                    logger.info(
                        f"total duration of downloaded videos: {total_duration} seconds, skip downloading more"
                    )
                    break
        except Exception as e:
            logger.error(f"failed to download video: {utils.to_json(item)} => {str(e)}")
    
    # Final diversity report
    logger.success(f"downloaded {len(video_paths)} videos")
    logger.info(f"🎯 Final diversity: {len(downloaded_urls)} unique URLs downloaded")
    
    return video_paths


if __name__ == "__main__":
    download_videos(
        "test_wikimedia", ["nature landscape"], audio_duration=10, source="wikimedia"
    )
