import os
import requests
import uvicorn
import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="Snap Downloader Extraction API")

class ExtractRequest(BaseModel):
    url: str

class MediaOption(BaseModel):
    url: str
    quality: str
    extension: str
    type: str

class ExtractResponse(BaseModel):
    title: str
    thumbnail: str
    duration: int
    platform: str
    medias: List[MediaOption]

@app.post("/api/v1/extract", response_model=ExtractResponse)
async def extract(request: ExtractRequest):
    url = request.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")
    
    parsed_url = url.lower()
    if "youtube.com" in parsed_url or "youtu.be" in parsed_url:
        raise HTTPException(status_code=400, detail="YouTube is not supported to comply with store policies.")
    
    # Determine platform name roughly for response field
    platform = "unknown"
    if "instagram.com" in parsed_url:
        platform = "instagram"
    elif "tiktok.com" in parsed_url:
        platform = "tiktok"
    elif "facebook.com" in parsed_url or "fb.watch" in parsed_url:
        platform = "facebook"
    elif "twitter.com" in parsed_url or "x.com" in parsed_url:
        platform = "twitter"
    elif "pinterest.com" in parsed_url or "pin.it" in parsed_url:
        platform = "pinterest"
    
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # We fetch info without downloading
            info = ydl.extract_info(url, download=False)
            if not info:
                raise HTTPException(status_code=404, detail="Could not extract metadata from this link.")
            
            title = info.get("title") or info.get("description") or "Social Media Post"
            if len(title) > 80:
                title = title[:77] + "..."
            
            thumbnail = info.get("thumbnail") or ""
            thumbnails_list = info.get("thumbnails")
            if thumbnails_list and not thumbnail:
                thumbnail = thumbnails_list[-1].get("url") or ""
            
            duration = int(info.get("duration") or 0)
            medias = []
            
            formats = info.get("formats", [])
            direct_url = info.get("url")
            ext = info.get("ext") or "mp4"
            
            video_formats = []
            audio_formats = []
            
            for f in formats:
                f_url = f.get("url")
                if not f_url:
                    continue
                
                acodec = f.get("acodec")
                vcodec = f.get("vcodec")
                
                # Check for video + audio combo formats
                if vcodec != 'none' and vcodec is not None:
                    if acodec != 'none' and acodec is not None:
                        video_formats.append(f)
                    elif not formats:
                        video_formats.append(f)
                elif acodec != 'none' and acodec is not None:
                    audio_formats.append(f)

            # Fallback if no pre-merged formats
            if not video_formats and formats:
                for f in formats:
                    if f.get("vcodec") != 'none' and f.get("vcodec") is not None:
                        video_formats.append(f)
            
            # Populate video stream options
            if video_formats:
                video_formats.sort(key=lambda x: (x.get("height") or 0, x.get("tbr") or 0), reverse=True)
                for f in video_formats[:2]:
                    height = f.get("height")
                    quality_str = f"{height}p" if height else "HD"
                    medias.append(MediaOption(
                        url=f["url"],
                        quality=quality_str,
                        extension=f.get("ext") or "mp4",
                        type="video"
                    ))
            elif direct_url:
                medias.append(MediaOption(
                    url=direct_url,
                    quality="HD",
                    extension=ext,
                    type="video"
                ))
                
            # Populate audio stream options
            if audio_formats:
                audio_formats.sort(key=lambda x: x.get("abr") or 0, reverse=True)
                best_audio = audio_formats[0]
                abr = best_audio.get("abr")
                quality_str = f"{int(abr)}kbps" if abr else "128kbps"
                medias.append(MediaOption(
                    url=best_audio["url"],
                    quality=quality_str,
                    extension=best_audio.get("ext") or "mp3",
                    type="audio"
                ))
            elif direct_url and platform != "unknown":
                medias.append(MediaOption(
                    url=direct_url,
                    quality="128kbps",
                    extension="mp3",
                    type="audio"
                ))

            if not medias:
                raise HTTPException(status_code=400, detail="No downloadable streams found.")
            
            return ExtractResponse(
                title=title,
                thumbnail=thumbnail,
                duration=duration,
                platform=platform,
                medias=medias
            )
            
    except yt_dlp.utils.DownloadError as e:
        # Strip generic errors to keep it short & helpful
        err_msg = str(e)
        if "Unsupported URL" in err_msg:
            err_msg = "Unsupported platform or invalid video URL."
        elif "Private video" in err_msg or "Sign in" in err_msg:
            err_msg = "This video is private or requires authentication."
        raise HTTPException(status_code=400, detail=f"Extraction failed: {err_msg}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")

@app.get("/api/v1/download")
async def download(url: str):
    import urllib.parse
    
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required")
    
    try:
        # Decode the URL in case it is double URL-encoded from the client
        decoded_url = urllib.parse.unquote(url)
        
        parsed_url = decoded_url.lower()
        if "youtube.com" in parsed_url or "youtu.be" in parsed_url:
            raise HTTPException(status_code=400, detail="YouTube downloads are not supported.")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.tiktok.com/",
            "Accept-Encoding": "identity",
        }
        
        r = requests.get(decoded_url, headers=headers, stream=True, timeout=30)
        r.raise_for_status()
        
        content_type = r.headers.get("content-type", "application/octet-stream")
        content_length = r.headers.get("content-length")
        
        def iter_content():
            for chunk in r.iter_content(chunk_size=512 * 1024):
                if chunk:
                    yield chunk
        
        response_headers = {
            "X-Accel-Buffering": "no",  # Disable Vercel buffering for serverless streaming
        }
        if content_length:
            response_headers["Content-Length"] = content_length
            
        return StreamingResponse(
            iter_content(),
            media_type=content_type,
            headers=response_headers
        )
    except requests.exceptions.RequestException as e:
        status_code = getattr(e.response, 'status_code', 400)
        raise HTTPException(
            status_code=400, 
            detail=f"Proxy fetch failed (HTTP {status_code}): {str(e)}. URL attempted: {decoded_url[:120]}..."
        )
    except Exception as e:
        raise HTTPException(
            status_code=400, 
            detail=f"Proxy internal exception: {str(e)}. URL attempted: {decoded_url[:120]}..."
        )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
