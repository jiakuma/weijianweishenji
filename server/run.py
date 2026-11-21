"""启动审计工作平台后端：固定 5281 端口，与前端 5280 配对。

单实例保护：Milvus Lite 的数据目录是单进程独占的，重复启动会导致后一个
进程抢不到锁、抛出 DataDirLockedError。启动前先探测端口，若已有实例在跑，
直接给出清晰提示并退出，而不是让用户面对底层异常堆栈。
"""
import socket
import sys

import uvicorn

HOST = "0.0.0.0"
PORT = 5281


def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def main() -> None:
    if _port_in_use(PORT):
        print(
            f"[启动中止] 端口 {PORT} 已被占用，审计平台可能已在运行。\n"
            f"  · 若服务已正常启动，无需重复执行。\n"
            f"  · 若是残留进程占用，请先结束该进程（Windows 可在任务管理器或执行\n"
            f"    netstat -ano | findstr :{PORT} 查看 PID 后结束），再重新运行本脚本。",
            file=sys.stderr,
        )
        sys.exit(1)
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
