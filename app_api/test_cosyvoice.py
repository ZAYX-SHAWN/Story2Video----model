"""
测试 CosyVoice TTS API 调用
"""
import os
import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer

# 设置 API Key
dashscope.api_key = "sk-529919bfaabb436cafa16fd3564922f6"

# 测试文本
text = "地球要完�?

print(f"开始测�?TTS: {text}")

# 初始化语音合成器
synthesizer = SpeechSynthesizer(
    model='cosyvoice-v3-flash',
    voice='longhua_v2'
)

# 调用合成
print("调用 TTS API...")
audio_data = synthesizer.call(text)

print(f"返回数据类型: {type(audio_data)}")
print(f"返回数据长度: {len(audio_data) if audio_data else 0} bytes")

if audio_data:
    # 保存到文�?
    output_file = "test_audio.mp3"
    with open(output_file, 'wb') as f:
        f.write(audio_data)
    print(f"音频已保存到: {output_file}")
    
    # 获取请求信息
    request_id = synthesizer.get_last_request_id()
    delay = synthesizer.get_first_package_delay()
    print(f"Request ID: {request_id}")
    print(f"First package delay: {delay}ms")
else:
    print("错误：TTS API 返回空数�?)
