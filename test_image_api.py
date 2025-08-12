#!/usr/bin/env python3
"""
Script to test image API
"""

import requests
import json

def test_unsplash_api():
    """Test Unsplash API"""
    print("=== Testing Unsplash API ===")
    
    # Test different categories
    categories = ['nature', 'abstract', 'minimal', 'gradient', 'geometric']
    
    for category in categories:
        print(f"\nTesting category: {category}")
        
        # Direct test Unsplash URL
        unsplash_url = f"https://source.unsplash.com/800x800/?{category}&sig=123"
        print(f"Unsplash URL: {unsplash_url}")
        
        try:
            response = requests.head(unsplash_url, timeout=10)
            print(f"Status code: {response.status_code}")
            print(f"Content type: {response.headers.get('content-type', 'Unknown')}")
            
            if response.status_code == 200:
                print("✅ Unsplash API working normally")
            else:
                print("❌ Unsplash API returned error status code")
                
        except Exception as e:
            print(f"❌ Unsplash API request failed: {e}")

def test_pexels_api():
    """Test Pexels API (if API key is available)"""
    print("\n=== Testing Pexels API ===")
    
    import os
    pexels_api_key = os.environ.get('PEXELS_API_KEY', '')
    
    if not pexels_api_key:
        print("❌ Pexels API key not found")
        print("Please set environment variable: PEXELS_API_KEY")
        return
    
    print(f"✅ Found Pexels API key: {pexels_api_key[:10]}...")
    
    # Test Pexels API
    url = "https://api.pexels.com/v1/search?query=nature&per_page=1"
    headers = {'Authorization': pexels_api_key}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"Status code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"Response data keys: {list(data.keys())}")
            
            if 'photos' in data and len(data['photos']) > 0:
                photo = data['photos'][0]
                print(f"Photo data keys: {list(photo.keys())}")
                
                if 'src' in photo:
                    print(f"Image source keys: {list(photo['src'].keys())}")
                    if 'medium' in photo['src']:
                        print(f"✅ Pexels API working normally")
                        print(f"Example image URL: {photo['src']['medium']}")
                    else:
                        print("❌ Medium size image not found")
                else:
                    print("❌ No src key in photo data")
            else:
                print("❌ No photo data found")
        else:
            print(f"❌ Pexels API returned error status code: {response.status_code}")
            print(f"Error message: {response.text}")
            
    except Exception as e:
        print(f"❌ Pexels API request failed: {e}")

def test_flask_api():
    """Test Flask application API endpoints"""
    print("\n=== Testing Flask API ===")
    
    base_url = "http://localhost:5000"
    
    # Test image API endpoints
    categories = ['nature', 'abstract']
    
    for category in categories:
        print(f"\nTesting category: {category}")
        
        try:
            response = requests.get(f"{base_url}/api/card/image/{category}", timeout=10)
            print(f"Status code: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"Response data: {json.dumps(data, indent=2)}")
                
                if data.get('success') and data.get('image_url'):
                    print("✅ Flask API working normally")
                    print(f"Image URL: {data['image_url']}")
                else:
                    print("❌ Flask API returned incorrect data format")
            else:
                print(f"❌ Flask API returned error status code: {response.status_code}")
                print(f"Response content: {response.text}")
                
        except Exception as e:
            print(f"❌ Flask API request failed: {e}")

if __name__ == "__main__":
    print("Starting image API testing...")
    
    # Test Unsplash API
    test_unsplash_api()
    
    # Test Pexels API
    test_pexels_api()
    
    # Test Flask API
    test_flask_api()
    
    print("\nTesting completed!") 