from pathlib import Path
# -*- coding: utf-8 -*-
from urllib.parse import urlparse, urlsplit, urlunsplit, quote, parse_qs, urlencode

from app_api.core.config import (
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

def upload_to_oss(object_key: str, local_path: Path, max_retries: int = 3) -> str:
    from app_api.core.logging import logger
    import time
    
    if not local_path.exists():
        logger.error(f"OSS上传失败: 本地文件不存在 - {local_path}")
        return ""
    
    # 记录文件大小
    file_size = local_path.stat().st_size
    file_size_mb = file_size / (1024 * 1024)
    logger.info(f"准备上传文件到 OSS: {local_path.name}, 大小: {file_size_mb:.2f} MB")
    
    if not OSS_ENDPOINT or not OSS_BUCKET or not OSS_ACCESS_KEY_ID or not OSS_ACCESS_KEY_SECRET:
        logger.error("OSS上传失败: OSS 配置不完整(缺少 ENDPOINT/BUCKET/ACCESS_KEY_ID/ACCESS_KEY_SECRET)")
        return ""
    try:
        import oss2
    except Exception as e:
        logger.error(f"OSS上传失败: oss2 模块导入失败 - {e}")
        return ""
    
    # 重试上传
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"OSS上传尝试 {attempt}/{max_retries}: {object_key}")
            auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
            bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)
            
            # 对于大文件（>100MB），使用分片上传
            if file_size > 100 * 1024 * 1024:
                logger.info(f"文件大于 100MB，使用分片上传")
                oss2.resumable_upload(bucket, object_key, str(local_path), 
                                     multipart_threshold=100*1024*1024,
                                     part_size=10*1024*1024)
            else:
                with local_path.open("rb") as f:
                    bucket.put_object(object_key, f)
            
            logger.info(f"OSS上传成功 (尝试 {attempt}/{max_retries}): {object_key}")
            
            # 返回预签名 URL（私有桶也可用），按配置的过期秒数
            try:
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
                logger.info(f"生成预签名 URL 成功，有效期 {OSS_URL_EXPIRES} 秒")
                return presigned
            except Exception as e:
                logger.warning(f"OSS生成预签名URL失败: {e}，尝试使用公共URL")
                base = _public_base_url()
                return f"{base}/{object_key}" if base else ""
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"OSS上传失败 (尝试 {attempt}/{max_retries}): {error_msg}, object_key={object_key}")
            
            # 如果不是最后一次尝试，等待后重试
            if attempt < max_retries:
                wait_time = min(2 ** attempt, 10)  # 指数退避，最多 10 秒
                logger.info(f"等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
            else:
                logger.error(f"OSS上传失败，已达到最大重试次数 {max_retries}")
    
    return ""
