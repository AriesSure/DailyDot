<div align="center">
  <h1>DailyDot</h1>
  <p>
    <strong>AI 驱动的智能习惯追踪平台</strong>
  </p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.11-blue?logo=python" alt="Python 3.11">
    <img src="https://img.shields.io/badge/Flask-2.3-lightgrey?logo=flask" alt="Flask 2.3">
    <img src="https://img.shields.io/badge/MySQL-8.0-blue?logo=mysql" alt="MySQL 8.0">
    <img src="https://img.shields.io/badge/Docker-compose-2496ED?logo=docker" alt="Docker Compose">
    <img src="https://img.shields.io/badge/LLM-DeepSeek%20%7C%20Tongyi-FF6F00" alt="LLM Support">
  </p>
</div>

---

## 目录

- [项目简介](#项目简介)
- [核心功能](#核心功能)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [项目结构](#项目结构)
- [API 概览](#api-概览)
- [AI 功能](#ai-功能)
- [测试](#测试)
- [部署](#部署)

---

## 项目简介

**DailyDot** 是一个全栈习惯追踪 Web 应用，帮助用户建立和维持良好习惯。用户可创建习惯、每日打卡、生成成就卡片、查看统计数据。项目集成了 **3 个 AI 功能**（RAG 习惯推荐、自然语言创建、智能周报），是一份面向 AI 应用岗求职的完整全栈作品。

---

## 核心功能

### ✅ 习惯管理
- 创建/编辑/删除习惯（支持 12 种图标、频率、时间段）
- 每日打卡（含备注和时间戳）
- 按日期打卡/取消打卡
- 连续打卡天数统计
- 习惯日历视图 & 年度热力图

### 📋 待办管理
- 创建/编辑/删除待办事项
- 一键完成切换
- 按日期排序查看

### 🃏 成就卡片
- AI 随机图片 + 励志名言生成
- Pexels API 智能配图（自动降级 Picsum）
- Canvas 截图下载
- 日期选择与连续打卡天数展示
- 卡片收藏画廊

### 📊 数据统计
- 14 天打卡趋势折线图（Chart.js）
- 习惯完成率排名
- 待办完成率
- 年度日历热力图

### 🤖 AI 功能
- **RAG 习惯推荐**：输入目标 → 向量搜索知识库 → LLM 个性化建议
- **自然语言创建**：输入"每周一三五早上7点跑步30分钟" → 自动解析创建
- **AI 周报/月报**：自动统计数据 → LLM 生成分析报告（支持 3 种语气）

---

## 技术栈

### 后端
| 技术 | 用途 |
|------|------|
| Flask 2.3 | Web 框架 |
| SQLAlchemy 2.0 | ORM 数据库映射 |
| Flask-Migrate / Alembic | 数据库迁移 |
| Flask-Login | 用户认证 |
| Flask-WTF | 表单 & CSRF 保护 |
| MySQL 8.0 / SQLite | 数据库（多环境） |
| Redis | 缓存 / Celery 消息队列 |

### AI / ML
| 技术 | 用途 |
|------|------|
| sentence-transformers | 中文/英文语义 Embedding |
| FAISS / numpy | 向量相似度搜索 |
| OpenAI SDK | LLM API 调用（兼容 DeepSeek、通义千问） |
| Celery | 定时 AI 周报生成 |

### 前端
| 技术 | 用途 |
|------|------|
| Tailwind CSS | UI 框架（CDN） |
| Font Awesome 6.5 | 图标库 |
| Chart.js | 数据可视化 |
| html2canvas | 卡片截图下载 |
| Alpine.js | 下拉菜单交互 |

### DevOps
| 技术 | 用途 |
|------|------|
| Docker + Compose | 容器化编排（Flask + MySQL + Redis + Celery） |
| Gunicorn | WSGI 服务器 |
| GitHub Actions | CI/CD |
| pytest + coverage | 测试 & 覆盖率 |

---

## 快速开始

### 本地开发（SQLite）

```bash
# 1. 克隆仓库
git clone https://github.com/yourusername/dailydot.git
cd dailydot

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行
flask run
# 或
python run.py
```

访问 http://localhost:5000

### Docker 部署（MySQL）

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 SECRET_KEY 等

# 2. 启动
docker-compose up --build

# 3. 初始化数据库
docker-compose exec web flask db upgrade
```

访问 http://localhost:5000

---

## 配置说明

环境变量通过 `.env` 文件或系统环境变量配置：

### 必需
| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SECRET_KEY` | Flask 密钥 | -（必须设置） |

### 数据库
| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DATABASE_URL` | 数据库连接串 | `sqlite:///app/data/data.sqlite` |
| `FLASK_ENV` | 运行环境 | `development` |

`FLASK_ENV=development` → SQLite（本地开发）  
`FLASK_ENV=testing` → 内存 SQLite（自动测试）  
`FLASK_ENV=production` → 读取 `DATABASE_URL`（推荐 MySQL）

### AI 功能（可选）
| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | LLM API 密钥 | -（不配置则降级为纯本地） |
| `LLM_BASE_URL` | API 地址 | `https://api.deepseek.com` |
| `LLM_MODEL` | 模型名 | `deepseek-chat` |
| `PEXELS_API_KEY` | 卡片配图 API 密钥 | - |

> **AI 降级策略**：未配置 `LLM_API_KEY` 时，推荐功能返回向量匹配结果，解析功能提示未配置，报告功能展示纯数据统计。

---

## 项目结构

```
DailyDot/
├── app/
│   ├── __init__.py              # 应用入口
│   ├── factory.py               # create_app() 工厂
│   ├── config.py                # 多环境配置
│   ├── extensions.py            # 惰性加载扩展
│   ├── logging_config.py        # 日志配置
│   ├── error_handlers.py        # HTTP 错误处理
│   ├── models.py                # ORM 模型（User/Habit/Record/Todo/Card）
│   ├── forms.py                 # WTForms 表单
│   ├── data/
│   │   ├── quotes.json          # 名言数据
│   │   └── habit_embeddings.pkl # 向量索引缓存
│   ├── utils/
│   │   ├── constants.py         # 共享常量
│   │   ├── query_helpers.py     # 查询辅助
│   │   └── json_response.py     # 统一 JSON 响应
│   ├── services/
│   │   ├── habit_service.py     # 习惯业务逻辑
│   │   ├── record_service.py    # 打卡业务逻辑
│   │   ├── todo_service.py      # 待办业务逻辑
│   │   ├── card_service.py      # 卡片业务逻辑
│   │   └── statistics_service.py
│   ├── ai/
│   │   ├── knowledge_base.py    # 50+ 习惯知识库
│   │   ├── vector_store.py      # 向量搜索
│   │   ├── llm_client.py        # LLM 统一客户端
│   │   └── prompt_templates.py  # Prompt 模板
│   ├── blueprints/
│   │   ├── auth/   → /auth      # 认证
│   │   ├── main/   → /          # 主页
│   │   ├── habits/ → /habits    # 习惯
│   │   ├── todos/  → /todos     # 待办
│   │   ├── cards/  → /cards     # 卡片
│   │   ├── stats/  → /stats     # 统计
│   │   └── ai/     → /ai        # AI 功能
│   ├── templates/               # Jinja2 模板
│   └── static/                  # 静态资源
├── tests/
│   ├── conftest.py              # pytest fixtures
│   ├── test_models.py
│   ├── test_auth.py
│   ├── test_habits.py
│   ├── test_todos.py
│   ├── test_cards.py
│   └── test_ai.py
├── migrations/                  # Alembic 迁移
├── Dockerfile
├── docker-compose.yml
├── celery_app.py                # Celery 定时任务
├── .env.example
└── .github/workflows/ci.yml     # CI/CD
```

---

## API 概览

### 认证 `/auth`
| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/auth/login` | 登录 |
| GET/POST | `/auth/register` | 注册 |
| GET | `/auth/logout` | 登出 |
| GET/POST | `/auth/change_pw` | 修改密码 |
| GET | `/auth/account` | 账户中心 |

### 习惯 `/habits`
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/habits/` | 我的习惯列表 |
| GET/POST | `/habits/new` | 创建习惯 |
| GET | `/habits/view/<id>` | 习惯详情 |
| GET/POST | `/habits/edit/<id>` | 编辑习惯 |
| POST | `/habits/delete/<id>` | 删除习惯 |
| POST | `/habits/check_in/<id>` | 今日打卡 |
| POST | `/habits/checkin_by_date/<id>` | 按日期打卡 |
| POST | `/habits/uncheck_by_date/<id>` | 取消打卡 |
| GET | `/habits/checkin_logs/<id>` | 打卡日志 |

### 待办 `/todos`
| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/todos/new` | 创建待办 |
| POST | `/todos/complete/<id>` | 完成切换 |
| POST | `/todos/edit/<id>` | 编辑待办 |
| POST | `/todos/delete/<id>` | 删除待办 |
| GET | `/todos/list` | 待办列表 |

### 卡片 `/cards`
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/cards/new` | 创建卡片 |
| POST | `/cards/api/card/save` | 保存卡片 |
| GET | `/cards/api/card/quote/<cat>` | 随机名言 |
| GET | `/cards/api/card/image/<cat>` | 随机图片 |
| DELETE | `/cards/api/card/<id>` | 删除卡片 |
| GET | `/cards/stack` | 卡片收藏 |

### 统计 `/stats`
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/stats/` | 统计面板 |
| GET | `/stats/annual/<id>` | 年度热力图 |

### AI `/ai`
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/ai/recommend` | RAG 习惯推荐 |
| POST | `/ai/parse-habit` | 自然语言创建 |
| GET | `/ai/report` | AI 周报/月报 |

---

## AI 功能

### 1. RAG 习惯推荐

```
用户输入目标 → sentence-transformers 编码 → FAISS 向量检索 Top-5
→ LLM 生成个性化建议 → 一键创建习惯
```

- **降级策略**：无 `LLM_API_KEY` 时直接返回向量匹配结果
- **知识库**：50+ 中英双语习惯模板（健康、健身、学习、效率、正念、社交）

### 2. 自然语言创建

```
用户输入 "每周一三五早上7点跑步30分钟"
→ LLM Function Calling → 结构化数据 → 一键创建
```

- 支持中英文输入
- 自动提取：习惯名称、频率、时间段、图标、备注

### 3. AI 周报/月报

```
自动汇总周期数据 → LLM 生成 Markdown 报告 → 多彩渲染展示
```

- 3 种语气：教练（coach）、朋友（friend）、分析师（analyst）
- 降级策略：无 `LLM_API_KEY` 时展示纯数据统计

---

## 测试

```bash
# 运行全部测试
pytest

# 带覆盖率报告
pytest --cov=app --cov-report=term-missing

# 运行特定测试
pytest tests/test_habits.py -v
```

**测试覆盖**：48 个用例，涵盖模型、认证、习惯 CRUD、打卡逻辑、待办 CRUD、卡片 API、AI 端点。

---

## 部署

### Docker Compose（推荐）

```bash
# 1. 配置环境
cp .env.example .env
# 编辑 SECRET_KEY, LLM_API_KEY 等

# 2. 启动全部服务
docker-compose up -d --build

# 3. 初始化数据库
docker-compose exec web flask db upgrade

# 4. 查看日志
docker-compose logs -f web
```

启动后访问 `http://localhost:5000`

### 手动部署

```bash
# 生产环境依赖
pip install gunicorn

# 启动
gunicorn --bind 0.0.0.0:5000 --workers 4 "app:app"
```

---

## License

MIT
