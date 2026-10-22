"""Meta Graph API publishing implementation for Facebook Pages and Instagram."""
import json
import mimetypes
import os
from pathlib import Path
import secrets
from urllib import parse, request, error

class IntegrationRequired(Exception):
    """No request was sent. Retry is safe after configuring the adapter."""

class PublishRejected(Exception):
    """Use ONLY when the API definitively confirms no post was created."""

class Publisher:
    """Meta Graph API publisher supporting Facebook Pages and Instagram Professional accounts."""
    
    GRAPH_API_VERSION = "v20.0"
    GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

    def get_credentials(self, account: dict) -> tuple[str, str]:
        """Resolve Page/Account ID and Access Token from account or environment."""
        external_id = str(account.get("external_id") or "").strip()
        page_id = external_id
        token = ""

        # Check if external_id is formatted as PAGE_ID:ACCESS_TOKEN or PAGE_ID|ACCESS_TOKEN
        if ":" in external_id:
            page_id, token = external_id.split(":", 1)
            page_id = page_id.strip()
            token = token.strip()
        elif "|" in external_id:
            page_id, token = external_id.split("|", 1)
            page_id = page_id.strip()
            token = token.strip()

        # If not embedded in external_id, check environment variables
        if not token:
            token = (
                os.environ.get(f"FB_PAGE_TOKEN_{page_id}")
                or os.environ.get(f"META_TOKEN_{page_id}")
                or os.environ.get("FB_PAGE_ACCESS_TOKEN")
                or os.environ.get("META_PAGE_ACCESS_TOKEN")
                or os.environ.get("META_ACCESS_TOKEN")
                or ""
            ).strip()

        # Check if page_id is in environment if missing
        if not page_id:
            page_id = (
                os.environ.get("FB_PAGE_ID")
                or os.environ.get("META_PAGE_ID")
                or ""
            ).strip()

        return page_id, token

    def publish(self, *, account: dict, caption: str, media: dict | None, idempotency_key: str) -> str:
        """Publishes post to Facebook Pages or Instagram via Meta Graph API.
        
        Returns confirmed remote post ID.
        """
        platform = account.get("platform", "facebook")
        page_id, access_token = self.get_credentials(account)

        if not page_id:
            raise IntegrationRequired(
                f"Meta Page / Account ID is missing for destination '{account.get('name', 'Account')}'. "
                "Configure external_id in Accounts or set FB_PAGE_ID in .env."
            )

        if not access_token:
            raise IntegrationRequired(
                f"Page Access Token is missing for destination '{account.get('name', 'Account')}'. "
                "Set FB_PAGE_ACCESS_TOKEN in .env or provide PAGE_ID:ACCESS_TOKEN in the account ID field."
            )

        if platform == "facebook":
            return self._publish_facebook(page_id=page_id, token=access_token, caption=caption, media=media)
        elif platform == "instagram":
            return self._publish_instagram(ig_user_id=page_id, token=access_token, caption=caption, media=media)
        else:
            raise PublishRejected(f"Unsupported platform: {platform}")

    def _publish_facebook(self, *, page_id: str, token: str, caption: str, media: dict | None) -> str:
        """Publish post (text, photo, or video) to a Facebook Page."""
        if media is None:
            # Text-only post via POST /{page-id}/feed
            endpoint = f"{self.GRAPH_API_BASE}/{page_id}/feed"
            data = parse.urlencode({
                "message": caption,
                "access_token": token,
            }).encode("utf-8")
            req = request.Request(endpoint, data=data, method="POST")
            res_data = self._send_request(req)
            post_id = res_data.get("id")
            if not post_id:
                raise RuntimeError("Facebook feed API succeeded but did not return a post ID.")
            return str(post_id)

        mime = media.get("mime", "")
        file_path = Path(media["path"])
        if not file_path.is_file():
            raise PublishRejected(f"Media file not found on server: {media.get('name', '')}")

        file_bytes = file_path.read_bytes()

        if mime.startswith("image/"):
            # Photo post via POST /{page-id}/photos
            endpoint = f"{self.GRAPH_API_BASE}/{page_id}/photos"
            fields = {
                "caption": caption,
                "access_token": token,
            }
            files = {
                "source": (media.get("name", "photo.jpg"), file_bytes, mime)
            }
            body, content_type = self._encode_multipart(fields, files)
            req = request.Request(endpoint, data=body, method="POST", headers={"Content-Type": content_type})
            res_data = self._send_request(req)
            # Facebook photo response contains {"id": "<photo_id>", "post_id": "<page_id_post_id>"}
            return str(res_data.get("post_id") or res_data.get("id"))

        elif mime.startswith("video/"):
            # Video post via POST /{page-id}/videos
            endpoint = f"{self.GRAPH_API_BASE}/{page_id}/videos"
            fields = {
                "description": caption,
                "access_token": token,
            }
            files = {
                "source": (media.get("name", "video.mp4"), file_bytes, mime)
            }
            body, content_type = self._encode_multipart(fields, files)
            req = request.Request(endpoint, data=body, method="POST", headers={"Content-Type": content_type})
            res_data = self._send_request(req)
            return str(res_data.get("id"))

        else:
            raise PublishRejected(f"Unsupported media type for Facebook: {mime}")

    def _publish_instagram(self, *, ig_user_id: str, token: str, caption: str, media: dict | None) -> str:
        """Publish post to Instagram Professional Account via Meta Graph API."""
        if not media:
            raise PublishRejected("Instagram requires media (photo or video). Caption-only posts are not supported by Instagram API.")

        # Note: Instagram Graph API requires media to be accessible via a public URL
        public_url = media.get("public_url") or os.environ.get("PUBLIC_MEDIA_BASE_URL", "").rstrip("/")
        if public_url and not public_url.startswith("http"):
            public_url = f"{public_url}/media/{media['id']}"

        if not public_url:
            raise IntegrationRequired(
                "Instagram Graph API requires a publicly accessible image_url or video_url. "
                "Set PUBLIC_MEDIA_BASE_URL in .env so Meta servers can fetch the uploaded media."
            )

        mime = media.get("mime", "")
        is_video = mime.startswith("video/")

        # 1. Create Media Container
        container_endpoint = f"{self.GRAPH_API_BASE}/{ig_user_id}/media"
        container_params = {
            "caption": caption,
            "access_token": token,
        }
        if is_video:
            container_params["video_url"] = public_url
            container_params["media_type"] = "REELS"
        else:
            container_params["image_url"] = public_url

        container_req = request.Request(
            container_endpoint,
            data=parse.urlencode(container_params).encode("utf-8"),
            method="POST",
        )
        container_res = self._send_request(container_req)
        creation_id = container_res.get("id")
        if not creation_id:
            raise RuntimeError("Instagram media container creation did not return an ID.")

        # 2. Publish Media Container
        publish_endpoint = f"{self.GRAPH_API_BASE}/{ig_user_id}/media_publish"
        publish_params = {
            "creation_id": creation_id,
            "access_token": token,
        }
        publish_req = request.Request(
            publish_endpoint,
            data=parse.urlencode(publish_params).encode("utf-8"),
            method="POST",
        )
        publish_res = self._send_request(publish_req)
        post_id = publish_res.get("id")
        if not post_id:
            raise RuntimeError("Instagram media publish did not return a post ID.")
        return str(post_id)

    def _send_request(self, req: request.Request) -> dict:
        """Execute request and handle Meta Graph API responses and errors."""
        try:
            with request.urlopen(req, timeout=60) as resp:
                resp_bytes = resp.read()
                return json.loads(resp_bytes.decode("utf-8"))
        except error.HTTPError as e:
            try:
                err_data = json.loads(e.read().decode("utf-8"))
                error_obj = err_data.get("error", {})
                msg = error_obj.get("message", str(e))
                code = error_obj.get("code")
                err_type = error_obj.get("type", "")

                # Definite authentication / permission rejection
                if code in (100, 190, 200, 368) or "OAuthException" in err_type:
                    raise PublishRejected(f"Meta Graph API error ({code}): {msg}")
                # Definite media validation rejection
                if code in (36000, 36001, 36002, 36003):
                    raise PublishRejected(f"Meta Media Error: {msg}")

                raise RuntimeError(f"Meta Graph API error ({code}): {msg}")
            except (PublishRejected, RuntimeError):
                raise
            except Exception:
                raise RuntimeError(f"Meta HTTP Error {e.code}: {e.reason}")
        except error.URLError as e:
            raise RuntimeError(f"Failed to connect to Meta Graph API: {e.reason}")

    def _encode_multipart(self, fields: dict, files: dict) -> tuple[bytes, str]:
        """Encode fields and files into multipart/form-data body."""
        boundary = f"----WebKitFormBoundary{secrets.token_hex(16)}"
        body = bytearray()
        for key, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        for key, (filename, content, mime) in files.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode("utf-8"))
            body.extend(f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
            body.extend(content)
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))
        content_type = f"multipart/form-data; boundary={boundary}"
        return bytes(body), content_type
