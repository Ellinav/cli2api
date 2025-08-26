import logging
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from typing import List, Set

# --- 1. 使用 asyncio.Queue 作为日志的线程安全通道 ---
log_queue = asyncio.Queue()

# --- 2. 新的日志处理器，将日志放入 Queue ---
class QueueLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        # 过滤掉不想要的日志
        log_entry = self.format(record)
        if "/health" in log_entry:
            return
        
        try:
            # 将格式化后的日志放入队列
            log_queue.put_nowait(log_entry)
        except asyncio.QueueFull:
            # 如果队列满了（几乎不可能发生），可以选择忽略或打印错误
            pass

# --- 3. 配置所有相关的日志记录器 ---
def setup_logging():
    # 获取根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # 清理所有旧的处理器
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 获取 Uvicorn 访问日志记录器，这是关键！
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.handlers = []
    
    # 创建我们自定义的队列处理器
    queue_handler = QueueLogHandler()
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s', 
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    queue_handler.setFormatter(formatter)
    
    # 将我们的处理器应用到根日志和 Uvicorn 访问日志
    root_logger.addHandler(queue_handler)
    uvicorn_access_logger.addHandler(queue_handler)
    
    # 为了在容器控制台也能看到日志，额外添加一个标准输出处理器
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)
    
setup_logging()


# --- 4. FastAPI 应用主体 ---
from .gemini_routes import router as gemini_router
from .openai_routes import router as openai_router

app = FastAPI()

# 用一个集合来存储所有活跃的 WebSocket 连接
active_connections: Set[WebSocket] = set()

# 后台任务：从队列中取出日志并广播
async def log_broadcaster():
    logging.info("Log broadcaster task started.")
    while True:
        try:
            log_entry = await log_queue.get()
            # 使用 asyncio.gather 来并发地发送给所有客户端
            await asyncio.gather(
                *[ws.send_text(log_entry) for ws in active_connections],
                return_exceptions=True  # 忽略发送失败的错误
            )
        except Exception as e:
            logging.error(f"Error in log broadcaster: {e}")


@app.on_event("startup")
async def startup_event():
    # 在应用启动时，将广播员作为后台任务运行
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
            # 保持连接开放，等待客户端断开
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        logging.info("A client disconnected from the log stream.")


# 健康检查端点
@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# 包含其他业务逻辑路由
app.include_router(openai_router)
app.include_router(gemini_router)
