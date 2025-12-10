import json
# -*- coding: utf-8 -*-
import os
import dashscope
from dashscope import MultiModalConversation

dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'

messages = [
    {
        "role": "user",
        "content": [
            {"text": "一副典雅庄重的对联悬挂于厅堂之中，房间是个安静古典的中式布置，桌子上放着一些青花瓷，对联上左书“义本生知人机同道善思新”，右书“通云赋智乾坤启数高志远”，横批“智启通义”，字体飘逸，中间挂着一副中国风的画作，内容是岳阳楼"}
        ]
    }
]

# 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx"
api_key = os.getenv("DASHSCOPE_API_KEY")

response = MultiModalConversation.call(
    api_key=api_key,
    model="qwen-image-plus",
    messages=messages,
    result_format='message',
    stream=False,
    watermark=False,
    prompt_extend=False,
    negative_prompt='',
    size='928*1664'
)

if response.status_code == 200:
    print(json.dumps(response, ensure_ascii=False))
else:
    print(f"HTTP返回码：{response.status_code}")
    print(f"错误码：{response.code}")
    print(f"错误信息：{response.message}")
    print("请参考文档：https://www.alibabacloud.com/help/zh/model-studio/error-code")
