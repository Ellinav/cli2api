import logging
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from typing import List, Set

# --- 1. 日志队列 ---
log_queue = asyncio.Queue()

# --- 2. 带过滤功能的日志处理器 ---
class QueueLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        log_entry = self.format(record)
        
        # --- 日志过滤规则 (核心修改) ---
        # 1. 彻底屏蔽所有 favicon.ico 相关的日志
        if "favicon.ico" in log_entry:
            return
        # 2. 屏蔽关于 Gemini proxy request 的无用日志
        if "Gemini proxy request: path=" in log_entry:
            return
        # 3. 屏蔽关于凭证刷新的日常信息，只在出错时才显示
        if "credentials expired, attempting refresh" in log_entry:
            return
        if "credentials refreshed successfully" in log_entry:
            return
        if "Converted environment expiry format" in log_entry:
            return
        # --- 过滤结束 ---
        
        try:
            log_queue.put_nowait(log_entry)
        except asyncio.QueueFull:
            pass

# --- 3. 配置日志系统 ---
def setup_logging():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.handlers = []
    
    queue_handler = QueueLogHandler()
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s', 
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    queue_handler.setFormatter(formatter)
    
    root_logger.addHandler(queue_handler)
    uvicorn_access_logger.addHandler(queue_handler)
    
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)
    
setup_logging()

# --- 4. FastAPI 应用主体 (与上一版相同) ---
from .gemini_routes import router as gemini_router
from .openai_routes import router as openai_router

app = FastAPI()

active_connections: Set[WebSocket] = set()

async def log_broadcaster():
    logging.info("Log broadcaster task started.")
    while True:
        try:
            log_entry = await log_queue.get()
            await asyncio.gather(
                *[ws.send_text(log_entry) for ws in active_connections],
                return_exceptions=True
            )
        except Exception as e:
            logging.error(f"Error in log broadcaster: {e}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(log_broadcaster())
    logging.info("Application startup complete. Real-time log stream is active.")
    logging.info("Visit the root URL '/' in a browser to view logs.")

@app.get("/")
async def get_log_page():
    return FileResponse('templates/log_viewer.html')

@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.add(websocket)
    logging.info("A new client connected to the log stream.")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        logging.info("A client disconnected from the log stream.")

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

app.include_router(openai_router)
app.include_router(gemini_router)
