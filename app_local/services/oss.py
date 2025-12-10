from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit, quote, parse_qs, urlencode

from app_local.core.config import (
    OSS_ENDPOINT,
    OSS_ACCESS_KEY_ID,
    OSS_ACCESS_KEY_SECRET,
    OSS_BUCKET,
    OSS_BASE_URL,
    OSS_URL_EXPIRES,
)

def _public_base_url() -> str:
    if OSS_BASE_URL:
        return OSS_BASE_URL.rstrip("/")
    if not OSS_ENDPOINT or not OSS_BUCKET:
        return ""
    host = urlparse(OSS_ENDPOINT).netloc
    return f"https://{OSS_BUCKET}.{host}"

def upload_to_oss(object_key: str, local_path: Path) -> str:
    if not local_path.exists():
        return ""
    if not OSS_ENDPOINT or not OSS_BUCKET or not OSS_ACCESS_KEY_ID or not OSS_ACCESS_KEY_SECRET:
        return ""
    try:
        import oss2
    except Exception:
        return ""
    auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)
    with local_path.open("rb") as f:
        bucket.put_object(object_key, f)
    # 返回预签�?URL（私有桶也可用），按配置的过期秒�?    try:
        presigned = bucket.sign_url('GET', object_key, OSS_URL_EXPIRES)
        # 对签�?URL 的查询参数进行安全编码，确保 + 等特殊字符被正确处理
        # 这对�?Android 真机等严格环境很重要
        parts = urlsplit(presigned)
        query_params = parse_qs(parts.query, keep_blank_values=True)
        # 重新编码查询参数，确保特殊字符如 + 被编码为 %2B
        encoded_params = urlencode(
            {k: v[0] if len(v) == 1 else v for k, v in query_params.items()},
            safe=''
        )
        presigned = urlunsplit((parts.scheme, parts.netloc, parts.path, encoded_params, parts.fragment))
        return presigned
    except Exception:
        base = _public_base_url()
        return f"{base}/{object_key}" if base else ""
