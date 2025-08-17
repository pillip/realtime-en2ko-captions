#!/usr/bin/env python3
"""
OpenAI Realtime API 토큰 생성 테스트 스크립트
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

def test_token_generation():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ OPENAI_API_KEY가 설정되지 않았습니다.")
        return False
    
    print(f"🔑 API Key: {api_key[:20]}...")
    
    url = "https://api.openai.com/v1/realtime/sessions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "realtime=v1",
    }
    body = {
        "model": "gpt-4o-mini-realtime-preview",
        "modalities": ["text", "audio"],
        "instructions": "You are a helpful assistant that translates English speech to Korean captions in real-time.",
    }
    
    try:
        print("🔄 토큰 요청 중...")
        response = requests.post(url, headers=headers, json=body, timeout=15)
        
        print(f"📊 응답 상태: {response.status_code}")
        print(f"📋 응답 헤더: {dict(response.headers)}")
        
        if response.status_code == 200:
            data = response.json()
            print("✅ 토큰 생성 성공!")
            print(f"🆔 세션 ID: {data.get('id', 'N/A')}")
            print(f"⏰ 만료 시간: {data.get('expires_at', 'N/A')}")
            
            client_secret = data.get('client_secret', {})
            if isinstance(client_secret, dict):
                secret_value = client_secret.get('value', '')
                print(f"🔐 클라이언트 시크릿: {secret_value[:20]}...")
            else:
                print(f"🔐 클라이언트 시크릿: {str(client_secret)[:20]}...")
            
            return True
        else:
            print(f"❌ 토큰 생성 실패: {response.status_code}")
            try:
                error_data = response.json()
                print(f"📝 오류 세부사항: {error_data}")
            except:
                print(f"📝 응답 텍스트: {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ 네트워크 오류: {e}")
        return False
    except Exception as e:
        print(f"❌ 예상치 못한 오류: {e}")
        return False

if __name__ == "__main__":
    success = test_token_generation()
    exit(0 if success else 1)