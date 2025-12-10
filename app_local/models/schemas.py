from typing import List, Optional
from pydantic import BaseModel, Field

# 通用响应结构与错误结构
class ErrorResponse(BaseModel):
    code: str = Field(..., description="错误代码")
    message: str = Field(..., description="错误信息")

class OperationStatus(BaseModel):
    operation_id: str
    status: str = Field(..., description="Operation 状态，Success/Running/Failed")
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
    operation_id: str
    story_id: str
    user_id: str

class RenderVideoResponse(BaseModel):
    operation: OperationStatus
    video_url: str
