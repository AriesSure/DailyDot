"""RAG evaluation dataset — curated query cases for retrieval quality assessment.

DD-TASK-005A: Each case has a stable ``id``, a natural-language ``query``,
``expected_names`` (from ``HABIT_TEMPLATES``), and ``should_retrieve``.
"""

RAG_EVAL_CASES = [
    # ── Relevant — expect to retrieve relevant templates ──────
    {"id": "english",      "query": "我想提高英语水平",
     "expected_names": ["学习外语"], "should_retrieve": True},
    {"id": "exercise",     "query": "想养成锻炼身体的好习惯",
     "expected_names": ["跑步", "力量训练", "瑜伽"], "should_retrieve": True},
    {"id": "sleep",        "query": "改善睡眠质量",
     "expected_names": ["早睡", "深呼吸 5 分钟"], "should_retrieve": True},
    {"id": "journal",      "query": "每天记录学了什么",
     "expected_names": ["写日记"], "should_retrieve": True},
    {"id": "social",       "query": "和朋友保持联系",
     "expected_names": ["给家人打电话", "约朋友见面"], "should_retrieve": True},
    {"id": "productivity", "query": "提高工作效率",
     "expected_names": ["番茄工作法", "每日计划"], "should_retrieve": True},
    {"id": "meditate",     "query": "冥想放松",
     "expected_names": ["晨间冥想", "散步冥想"], "should_retrieve": True},
    {"id": "weight",       "query": "减肥",
     "expected_names": ["跑步", "跳绳", "限时进食 16:8"], "should_retrieve": True},
    {"id": "health_en",    "query": "get healthier",
     "expected_names": ["喝 8 杯水", "跑步", "早睡"], "should_retrieve": True},
    {"id": "water",        "query": "多喝水",
     "expected_names": ["喝 8 杯水"], "should_retrieve": True},
    {"id": "read",         "query": "读更多书",
     "expected_names": ["阅读 30 分钟"], "should_retrieve": True},
    {"id": "detox",        "query": "少看手机",
     "expected_names": ["数字排毒"], "should_retrieve": True},
    {"id": "swim",         "query": "游泳",
     "expected_names": ["游泳"], "should_retrieve": True},
    # ── Unrelated — expect NO high-confidence retrieval ────────
    {"id": "flask",        "query": "帮我修复 Flask 数据库连接错误",
     "expected_names": [], "should_retrieve": False},
    {"id": "meaning",      "query": "What is the meaning of life",
     "expected_names": [], "should_retrieve": False},
    {"id": "windows",      "query": "怎么安装 Windows 11",
     "expected_names": [], "should_retrieve": False},
]
