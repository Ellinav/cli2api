import logging
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from typing import List

# --- 1. WebSocket 连接管理器 ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

# --- 2. 自定义日志处理器，将日志发送到 WebSocket ---
class WebSocketLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.loop = asyncio.get_event_loop()

    def emit(self, record):
        log_entry = self.format(record)
        
        # 在这里过滤不想要的日志
        if "/health" in log_entry or "/favicon.ico" in log_entry:
            return
            
        # 通过 manager 将格式化后的日志广播给所有连接的客户端
        asyncio.run_coroutine_threadsafe(manager.broadcast(log_entry), self.loop)

# --- 3. 配置日志 ---
# 获取根日志记录器
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 移除所有现有的处理器，避免日志重复
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# 添加我们自定义的 WebSocket 处理器
ws_handler = WebSocketLogHandler()
ws_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(ws_handler)

# 同时，为了在容器的标准输出中也能看到日志，我们再添加一个 StreamHandler
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(stream_handler)


# --- 4. FastAPI 应用主体 ---
# 注意：我们将原来的大部分代码移到下面，以确保日志配置先被应用
from .gemini_routes import router as gemini_router
from .openai_routes import router as openai_router

app = FastAPI()

@app.get("/")
async def get_log_page():
    # 这个端点现在返回我们创建的HTML文件
    return FileResponse('templates/log_viewer.html')

@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # 保持连接开放
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("A client disconnected from the log stream.")


# 健康检查端点
@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# 包含其他路由
app.include_router(openai_router)
app.include_router(gemini_router)


# 在应用启动时打印信息
@app.on_event("startup")
async def startup_event():
    logger.info("Application startup complete. Ready to accept requests.")
    logger.info("Visit the root URL '/' in a browser to view the real-time log stream.")
