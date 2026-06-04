# HTTP 文件服务器 — 源码解读

## 项目结构

```
http-server/
├── server.py       # 主程序
├── start.sh        # 后台启动脚本
└── .gitignore
```

## 启动方式

```bash
python3 server.py --port 8080 --dir /path/to/share
```

参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-p` / `--port` | 8080 | 监听端口 |
| `-d` / `--dir` | 当前目录 | 文件共享根目录 |
| `-b` / `--bind` | 0.0.0.0 | 绑定地址 |

---

## 整体架构

```
请求 → ThreadingHTTPServer (多线程接收)
         ↓
  FileServerHandler (处理每个请求)
         ↓
    ┌─ 有 Range 头? ─→ _handle_range() ─→ 206 Partial Content
    │
    └─ 无 Range 头? ─→ 返回完整文件 ─→ 200 OK
```

### 为什么用 ThreadingHTTPServer？

Python 内置的 `HTTPServer` 是**单线程**的，同一时间只能处理一个请求。如果某个客户端下载大文件，其他所有请求（包括新的下载和目录浏览）都会被阻塞。改用 `ThreadingHTTPServer` 后，每个请求分配到独立线程，并发下载互不干扰。

---

## 核心类：`FileServerHandler`

继承自 `http.server.SimpleHTTPRequestHandler`，重写了两个方法：

### 1. `send_head()` — 请求入口（第14-47行）

```
接收 HTTP 请求
  │
  ├─ 文件不存在? ──────────→ 404
  │
  ├─ 路径是目录? ──────────→ list_directory() 展示文件列表
  │
  ├─ 请求包含 Range 头? ───→ _handle_range() 处理断点续传
  │
  └─ 普通下载请求 ─────────→ 200 + 完整文件内容
```

关键点：
- `self.translate_path(self.path)` —— 将 URL 路径（如 `/foo.txt`）转为本地文件系统路径
- `self.guess_type(path)` —— 根据文件扩展名推断 MIME 类型
- `os.fstat(f.fileno())` —— 通过文件描述符获取文件大小和修改时间，比 `os.stat` 更高效
- 每次响应都附带 `Accept-Ranges: bytes` 头，向客户端声明服务端支持断点续传
- 默认以附件形式下载：`Content-Disposition: attachment`

### 2. `_handle_range()` — 断点续传核心逻辑（第49-96行）

#### 2.1 解析 Range 请求头

```
Range: bytes=0-99       → 第 0~99 字节
Range: bytes=100-       → 第 100 字节到文件尾
Range: bytes=-200       → 最后 200 字节（suffix range）
Range: bytes=0-50,100-150 → 多段范围（不支持，返回 416）
```

正则匹配：`^bytes=(\d*)-(\d*)$`

- `group(1)` = 起始位置，`group(2)` = 结束位置
- 空字符串转为 `None`，便于区分三种格式

#### 2.2 区间换算

```
                    start        end
bytes=100-200       100          200            (固定区间)
bytes=100-          100          size-1         (从指定位置到文件尾)
bytes=-200          size-200     size-1         (最后 N 字节)
bytes=-             None         None           (无效，返回 416)
```

#### 2.3 有效性校验

```
条件                             结果
────────────────────────────────────────────
start ≥ size                    416 (超出文件大小)
start < 0                       416
end ≥ size                      416
end < start                     416 (区间颠倒)
bytes=0-50,100-150 (多段)       416 (格式不匹配)
```

校验失败时返回 `416 Range Not Satisfiable`，并在 `Content-Range` 头中告知文件总大小，方便客户端重新规划请求：

```
HTTP/1.1 416 Range Not Satisfiable
Content-Range: bytes */10240     ← 文件总大小 10240 字节
```

#### 2.4 发送 206 Partial Content

```python
length = end - start + 1          # 计算需要发送的字节数
f.seek(start)                     # 将文件指针定位到起始位置

HTTP/1.1 206 Partial Content
Content-Range: bytes 100-199/10240
Content-Length: 100
Accept-Ranges: bytes
```

然后只发送指定区间的数据：

```python
while remaining > 0:
    chunk = f.read(min(65536, remaining))   # 每次最多读 64KB
    self.wfile.write(chunk)
    remaining -= len(chunk)
```

分块发送避免大文件一次性读入内存（流式传输）。

#### 2.5 和普通下载的对比

| | 普通下载 (200) | 断点续传 (206) |
|---|---|---|
| 状态码 | 200 OK | 206 Partial Content |
| Content-Length | 文件总大小 | 区间长度 |
| Content-Range | 无 | `bytes start-end/total` |
| 文件指针 | 从 0 开始 | seek 到 start |
| 发送数据 | 整个文件 | 仅区间数据 |

---

## 目录列表生成：`list_directory()`（第98-130行）

当 URL 指向目录时，生成一个简单的 HTML 页面展示文件列表：

- 显示当前路径作为标题
- `../` 链接返回上级目录
- 子目录名称后加 `/` 标识
- URL 路径经过 `urllib.parse.quote` 编码，避免中文或特殊字符问题

---

## 启动流程：`main()`（第133-158行）

```
解析命令行参数 (port, dir, bind)
  │
  ├─ 目录不存在? ─→ 报错退出
  │
  ├─ chdir 到共享目录
  │
  ├─ 创建 ThreadingHTTPServer
  │    绑定 0.0.0.0:PORT
  │    SO_REUSEADDR 允许快速重启
  │
  └─ serve_forever() ─→ 等待 Ctrl+C 关闭
```

### 为什么需要 SO_REUSEADDR？

如果不设置，服务停止后端口会进入 `TIME_WAIT` 状态，短时间内无法重启。`SO_REUSEADDR` 允许立即复用端口。

---

## 协议时序图

### 普通下载

```
Client                          Server
  │                               │
  ├──── GET /file.txt ────────────┤
  │                               ├── 文件存在? 200
  │                               ├── Content-Length: 10240
  │◄──── 200 OK (完整文件) ───────┤
```

### 断点续传

```
Client                          Server
  │                               │
  ├──── GET /file.txt ────────────┤
  │      Range: bytes=100-199     │
  │                               ├── 校验区间
  │                               ├── seek(100)
  │                               ├── Content-Range: bytes 100-199/10240
  │◄──── 206 Partial Content ─────┤
  │      (仅 100 字节)            │
```

### 超出范围

```
Client                          Server
  │                               │
  ├──── GET /file.txt ────────────┤
  │      Range: bytes=999999-     │
  │                               ├── start ≥ size
  │◄──── 416 Range Not Satisfiable┤
  │      Content-Range: */10240   │
```

---

## 常见问题

**Q: 为什么用 ThreadingHTTPServer 而不是 HTTPServer？**

A: HTTPServer 单线程处理请求。当有客户端下载大文件时，其他所有请求（包括新下载和页面浏览）都要排队等待，导致服务完全不可用。ThreadingHTTPServer 为每个请求创建独立线程，并发处理。

**Q: Content-Disposition 的作用？**

A: 强制浏览器以附件形式下载文件，而不是直接在浏览器中打开（如文本文件、图片等）。

**Q: 客户端如何验证下载的文件是否正确？**

A: 结合 `Content-Range` 和 `Content-Length` 可以确认收到的片段范围是否正确。完整文件校验通常依赖文件自身的校验和（MD5/SHA256），服务器不额外提供。
