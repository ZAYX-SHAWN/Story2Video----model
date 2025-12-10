from typing import List, Optional
from pydantic import BaseModel, Field

# 通用响应结构与错误结构

class ErrorResponse(BaseModel):
    code: str = Field(..., description="错误代码")
    message: str = Field(..., description="错误信息")

class OperationStatus(BaseModel):
    operation_id: str
    status: str = Field(..., description="Operation 状态，如 Success/Running/Failed")
    detail: Optional[str] = None

# Shot 结构
class Shot(BaseModel):
    id: str
    sequence: int
    subject: Optional[str] = None
    detail: Optional[str] = None
    camera: Optional[str] = None
    narration: Optional[str] = None
    tone: Optional[str] = None
    image_url: Optional[str] = None
    video_url: Optional[str] = None

class CreateStoryboardRequest(BaseModel):
    operation_id: str
    story_id: str
    user_id: str
    display_name: str
    script_content: str
    style: str

class CreateStoryboardResponse(BaseModel):
    operation: OperationStatus
    shots: List[Shot]

class RegenerateShotRequest(BaseModel):
    operation_id: str
    story_id: str
    shot_id: str
    user_id: str
    subject: Optional[str] = None
    detail: Optional[str] = None
    details: Optional[str] = None
    prompt: Optional[str] = None
    camera: Optional[str] = None
    narration: Optional[str] = None
    tone: Optional[str] = None
    style: Optional[str] = None

class RegenerateShotResponse(BaseModel):
    operation: OperationStatus
    shot: Shot

class RenderVideoRequest(BaseModel):
    operation_id: Optional[str] = None
    story_id: Optional[str] = None
    user_id: Optional[str] = None
    operation: Optional[OperationStatus] = None
    shots: Optional[List[Shot]] = None
    
    def get_operation_id(self) -> str:
        """获取 operation_id，支持从 operation 对象或直接字段获取"""
        if self.operation_id:
            return self.operation_id
        if self.operation and self.operation.operation_id:
            return self.operation.operation_id
        raise ValueError("必须提供 operation_id 或 operation.operation_id")
    
    def get_story_id(self) -> str:
        """获取 story_id"""
        if self.story_id:
            return self.story_id
        # 尝试从 shots 的 image_url 中提取
        if self.shots and len(self.shots) > 0:
            url = self.shots[0].image_url
            if url:
                # 先 URL 解码，将 %2F 转换为 /
                from urllib.parse import unquote
                import re
                decoded_url = unquote(url)
                # 匹配 story/u-XXX/story-XXX 这样的模式，提取 story-XXX
                match = re.search(r'story/[^/]+/(story-[^/?\s]+)', decoded_url)
                if not match:
                    # 如果没匹配到，尝试匹配旧的模式 stories/story-001
                    match = re.search(r'stories/(story-[^/?\s]+)', decoded_url)
                if match:
                    return match.group(1)
        raise ValueError("必须提供 story_id 或在 shots 的 image_url 中包含 story_id")
    
    def get_user_id(self) -> str:
        """获取 user_id"""
        if self.user_id:
            return self.user_id
        # 尝试从 shots 的 image_url 中提取
        if self.shots and len(self.shots) > 0:
            url = self.shots[0].image_url
            if url:
                # 先 URL 解码
                from urllib.parse import unquote
                import re
                decoded_url = unquote(url)
                # 匹配 users/user123 这样的模式
                match = re.search(r'users/([^/?\s]+)', decoded_url)
                if match:
                    return match.group(1)
        # 默认用户
        return "default_user"


class RenderVideoResponse(BaseModel):
    operation: OperationStatus
    video_url: str