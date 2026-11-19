"""启动审计工作平台后端：固定 5281 端口，与前端 5280 配对。"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=5281, reload=False)
