import os
from dashscope import Generation
import dashscope
dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'

messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "你是谁？"},
]
response = Generation.call(
    api_key="sk-529919bfaabb436cafa16fd3564922f6",
    model="qwen-plus",
    messages=messages,
    result_format="message",
    enable_thinking=False,
)

if response.status_code == 200:
    # 打印回复
    print("=" * 20 + "完整回复" + "=" * 20)
    print(response.output.choices[0].message.content)
else:
    print(f"HTTP返回码：{response.status_code}")
    print(f"错误码：{response.code}")
    print(f"错误信息：{response.message}")