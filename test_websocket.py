#!/usr/bin/env python3
"""Test WebSocket server startup"""

import asyncio
import socket

import websockets


def find_free_port(start_port=8765, max_port=8800):
    """동적으로 사용 가능한 포트 찾기"""
    for port in range(start_port, max_port + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("localhost", port))
                print(f"[Port] 포트 {port} 사용 가능")
                return port
        except OSError:
            continue

    # 지정된 범위에서 포트를 찾지 못한 경우, OS에 자동 할당 요청
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))  # 0번 포트로 자동 할당
            port = s.getsockname()[1]
            print(f"[Port] OS 자동 할당 포트: {port}")
            return port
    except OSError:
        raise Exception("사용 가능한 포트를 찾을 수 없습니다")


async def test_handler(websocket):
    """Simple test handler"""
    print(f"[WebSocket] 클라이언트 연결: {websocket.remote_address}")
    async for message in websocket:
        print(f"[WebSocket] 받은 메시지: {message}")
        await websocket.send(f"Echo: {message}")


async def start_test_server():
    """Start test WebSocket server"""
    try:
        free_port = find_free_port()
        print(f"[WebSocket] 할당된 포트: {free_port}")

        server = await websockets.serve(test_handler, "localhost", free_port)
        print(f"[WebSocket] 서버 시작 완료: ws://localhost:{free_port}")

        # Keep server running
        await server.wait_closed()

    except Exception as e:
        print(f"[WebSocket] 서버 시작 오류: {e}")
        raise


if __name__ == "__main__":
    print("WebSocket 서버 테스트 시작...")
    asyncio.run(start_test_server())
