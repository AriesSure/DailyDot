#!/usr/bin/env python3
"""
测试图片API的脚本
"""

import requests
import json

def test_unsplash_api():
    """测试Unsplash API"""
    print("=== 测试 Unsplash API ===")
    
    # 测试不同的类别
    categories = ['nature', 'abstract', 'minimal', 'gradient', 'geometric']
    
    for category in categories:
        print(f"\n测试类别: {category}")
        
        # 直接测试Unsplash URL
        unsplash_url = f"https://source.unsplash.com/800x800/?{category}&sig=123"
        print(f"Unsplash URL: {unsplash_url}")
        
        try:
            response = requests.head(unsplash_url, timeout=10)
            print(f"状态码: {response.status_code}")
            print(f"内容类型: {response.headers.get('content-type', 'Unknown')}")
            
            if response.status_code == 200:
                print("✅ Unsplash API 工作正常")
            else:
                print("❌ Unsplash API 返回错误状态码")
                
        except Exception as e:
            print(f"❌ Unsplash API 请求失败: {e}")

def test_pexels_api():
    """测试Pexels API（如果有API密钥）"""
    print("\n=== 测试 Pexels API ===")
    
    import os
    pexels_api_key = os.environ.get('PEXELS_API_KEY', '')
    
    if not pexels_api_key:
        print("❌ 未找到 Pexels API 密钥")
        print("请设置环境变量: PEXELS_API_KEY")
        return
    
    print(f"✅ 找到 Pexels API 密钥: {pexels_api_key[:10]}...")
    
    # 测试Pexels API
    url = "https://api.pexels.com/v1/search?query=nature&per_page=1"
    headers = {'Authorization': pexels_api_key}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"状态码: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"响应数据键: {list(data.keys())}")
            
            if 'photos' in data and len(data['photos']) > 0:
                photo = data['photos'][0]
                print(f"照片数据键: {list(photo.keys())}")
                
                if 'src' in photo:
                    print(f"图片源键: {list(photo['src'].keys())}")
                    if 'medium' in photo['src']:
                        print(f"✅ Pexels API 工作正常")
                        print(f"示例图片URL: {photo['src']['medium']}")
                    else:
                        print("❌ 未找到 medium 尺寸的图片")
                else:
                    print("❌ 照片数据中没有 src 键")
            else:
                print("❌ 未找到照片数据")
        else:
            print(f"❌ Pexels API 返回错误状态码: {response.status_code}")
            print(f"错误信息: {response.text}")
            
    except Exception as e:
        print(f"❌ Pexels API 请求失败: {e}")

def test_flask_api():
    """测试Flask应用的API端点"""
    print("\n=== 测试 Flask API ===")
    
    base_url = "http://localhost:5000"
    
    # 测试图片API端点
    categories = ['nature', 'abstract']
    
    for category in categories:
        print(f"\n测试类别: {category}")
        
        try:
            response = requests.get(f"{base_url}/api/card/image/{category}", timeout=10)
            print(f"状态码: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"响应数据: {json.dumps(data, indent=2)}")
                
                if data.get('success') and data.get('image_url'):
                    print("✅ Flask API 工作正常")
                    print(f"图片URL: {data['image_url']}")
                else:
                    print("❌ Flask API 返回的数据格式不正确")
            else:
                print(f"❌ Flask API 返回错误状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                
        except Exception as e:
            print(f"❌ Flask API 请求失败: {e}")

if __name__ == "__main__":
    print("开始测试图片API...")
    
    # 测试Unsplash API
    test_unsplash_api()
    
    # 测试Pexels API
    test_pexels_api()
    
    # 测试Flask API
    test_flask_api()
    
    print("\n测试完成！") 